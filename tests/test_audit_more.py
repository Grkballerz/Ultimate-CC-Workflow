"""Additional audit coverage: markdown output, MCP shell detection,
suppression mechanic, exit codes."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_audit_more", REPO_ROOT / "bin" / "ucw-audit.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_audit_more"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_markdown_output_format(tmp_path, capsys):
    audit = _load()
    bad = tmp_path / "h.sh"
    bad.write_text("bash -c $USER\n")
    audit.main(["--repo", str(tmp_path), "--target", str(bad), "--markdown"])
    out = capsys.readouterr().out
    assert "## Audit Report" in out
    assert "| Severity | Count |" in out


def test_mcp_shell_exec_detected(tmp_path):
    audit = _load()
    bad = tmp_path / "mcp.json"
    bad.write_text(json.dumps({
        "mcpServers": {
            "evil": {"command": "bash", "args": ["-c", "echo $WHATEVER"]}
        }
    }))
    findings = audit.scan_mcp_configs(bad)
    assert any(f.rule == "mcp_shell_exec" for f in findings)


def test_suppression_comment_downgrades_secret_to_nit(tmp_path):
    audit = _load()
    # github_token has a distinct pattern (no overlap with other secret rules).
    p = tmp_path / "doc.md"
    p.write_text("token = ghp_" + "A" * 36 + "  # audit-allow: github_token\n")
    findings = audit.scan_secrets(p)
    # Suppressed findings are downgraded to a visible nit, not silently dropped.
    assert [f.severity for f in findings] == ["nit"]
    assert findings[0].rule == "github_token"


def test_suppression_comment_downgrades_injection_to_nit(tmp_path):
    audit = _load()
    p = tmp_path / "ex.sh"
    p.write_text("bash -c $USER # audit-allow: bash_c_var\n")
    findings = audit.scan_injection(p)
    assert [f.severity for f in findings] == ["nit"]
    assert findings[0].rule == "bash_c_var"


def test_suppression_requires_matching_rule(tmp_path):
    audit = _load()
    # Token names a *different* rule — must NOT silence the real secret.
    p = tmp_path / "doc.md"
    p.write_text("token = ghp_" + "C" * 36 + "  # audit-allow: bash_c_var\n")
    findings = audit.scan_secrets(p)
    assert any(f.severity == "critical" and f.rule == "github_token" for f in findings)
    assert all(f.severity != "nit" for f in findings)


def test_suppression_wildcard_token(tmp_path):
    audit = _load()
    p = tmp_path / "ex.sh"
    p.write_text("bash -c $USER # audit-allow: *\n")
    findings = audit.scan_injection(p)
    assert [f.severity for f in findings] == ["nit"]


def test_suppression_ignored_on_target_paths(tmp_path):
    audit = _load()
    # A file reached via --target is untrusted: its audit-allow must NOT suppress.
    p = tmp_path / "planted.sh"
    p.write_text("bash -c $USER # audit-allow: bash_c_var\n")
    findings = audit.scan_injection(p, allow_suppress=False)
    assert any(f.severity == "major" and f.rule == "bash_c_var" for f in findings)
    # And end-to-end through main(): --target file keeps its major → exit 3.
    rc = audit.main(["--repo", str(tmp_path), "--target", str(p), "--json"])
    assert rc == 3


def test_exit_2_on_critical(tmp_path):
    audit = _load()
    p = tmp_path / "secrets.json"
    p.write_text(json.dumps({"key": "sk-ant-AbCdEf012345_6789xyzABCDEFGH-real"}))
    rc = audit.main(["--repo", str(tmp_path), "--target", str(p), "--json"])
    assert rc == 2


def test_exit_3_on_major_only(tmp_path):
    audit = _load()
    p = tmp_path / "hook.sh"
    p.write_text("bash -c $USER\n")
    rc = audit.main(["--repo", str(tmp_path), "--target", str(p), "--json"])
    assert rc == 3


def test_format_text_clean_returns_ok(tmp_path):
    audit = _load()
    assert "clean" in audit.format_text([])


def test_skip_large_files(tmp_path):
    audit = _load()
    big = tmp_path / "big.dat"
    big.write_bytes(b"x" * 600_000)  # > 512KB
    assert audit._skip(big)
