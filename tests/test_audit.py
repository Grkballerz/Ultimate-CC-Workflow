"""Tests for the audit scanner — secrets, injections, MCP, agent scopes."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "ucw_audit", REPO_ROOT / "bin" / "ucw-audit.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_audit"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_secret_pattern_flags_anthropic_key(tmp_path):
    audit = _load_audit()
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-ant-AbCdEf012345_6789xyzABCDEF"}}))
    findings = audit.scan_secrets(p)
    assert any(f.rule == "anthropic_key" for f in findings)
    assert all(f.severity in {"critical", "minor"} for f in findings)


def test_placeholder_secret_is_minor(tmp_path):
    audit = _load_audit()
    p = tmp_path / "example.json"
    # The pattern matches but the line obviously says "example"
    p.write_text(json.dumps({"key": "sk-ant-example-replace-me-AbCdEf012345-please"}))
    findings = audit.scan_secrets(p)
    assert any(f.severity == "minor" for f in findings)


def test_injection_pattern_flags_bash_c(tmp_path):
    audit = _load_audit()
    p = tmp_path / "hook.sh"
    p.write_text('bash -c $USER_INPUT\n')
    findings = audit.scan_injection(p)
    assert any(f.rule == "bash_c_var" for f in findings)


def test_review_agent_with_write_tools_is_flagged(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text("""\
---
name: reviewer
tools: [Read, Edit, Write]
model: opus
---
# Reviewer body
""")
    findings = audit.scan_agent_scopes(agent)
    assert any(f.rule == "review_agent_writes" for f in findings)


def test_review_agent_readonly_passes(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text("""\
---
name: reviewer
tools: [Read, Grep, Glob]
model: opus
---
# Reviewer body
""")
    assert audit.scan_agent_scopes(agent) == []


def _agent_md(tools, extra_front=""):
    return f"""\
---
name: reviewer
tools: [{tools}]
{extra_front}model: opus
---
# Reviewer body
"""


def test_confined_write_scope_demotes_to_nit(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text(_agent_md(
        "Read, Grep, Glob, Write",
        "write-scope: .ucw/state/review-report.md\n"))
    findings = audit.scan_agent_scopes(agent)
    assert [f.severity for f in findings if f.rule == "review_agent_writes"] == ["nit"]


def test_write_scope_outside_ucw_state_stays_major(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text(_agent_md(
        "Read, Write", "write-scope: src/report.md\n"))
    findings = audit.scan_agent_scopes(agent)
    assert [f.severity for f in findings if f.rule == "review_agent_writes"] == ["major"]


def test_write_scope_with_traversal_stays_major(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text(_agent_md(
        "Read, Write", "write-scope: .ucw/state/../../src/x.md\n"))
    findings = audit.scan_agent_scopes(agent)
    assert [f.severity for f in findings if f.rule == "review_agent_writes"] == ["major"]


def test_edit_tool_never_demotable_despite_write_scope(tmp_path):
    audit = _load_audit()
    agent = tmp_path / "reviewer.md"
    agent.write_text(_agent_md(
        "Read, Edit, Write", "write-scope: .ucw/state/review-report.md\n"))
    findings = audit.scan_agent_scopes(agent)
    assert [f.severity for f in findings if f.rule == "review_agent_writes"] == ["major"]


def test_audit_clean_repo_returns_zero(tmp_path):
    audit = _load_audit()
    # Empty tree — no findings.
    rc = audit.main(["--repo", str(tmp_path), "--json"])
    assert rc == 0


def test_audit_repo_self_clean(capsys):
    """Run audit against this repository — there should be no critical findings."""
    audit = _load_audit()
    rc = audit.main(["--repo", str(REPO_ROOT), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    criticals = [f for f in data if f["severity"] == "critical"]
    assert criticals == [], f"unexpected critical findings: {criticals}"
    assert rc != 2
