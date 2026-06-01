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


def test_suppression_comment_skips_secret(tmp_path):
    audit = _load()
    p = tmp_path / "doc.md"
    p.write_text("Example anthropic key: sk-ant-AbCdEf012345_6789xyzABCDEF <!-- audit-allow: anthropic_key -->\n")
    findings = audit.scan_secrets(p)
    assert findings == []


def test_suppression_comment_skips_injection(tmp_path):
    audit = _load()
    p = tmp_path / "ex.sh"
    p.write_text("bash -c $USER # audit-allow: bash_c_var\n")
    findings = audit.scan_injection(p)
    assert findings == []


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
