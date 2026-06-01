"""Cover the remaining audit paths: color output, gather_files traversal,
empty / dir target handling, markdown of all severities."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_audit_final", REPO_ROOT / "bin" / "ucw-audit.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_audit_final"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_format_text_with_color_renders_ansi(tmp_path):
    audit = _load()
    f = audit.Finding("critical", "secret", "x.py", 1, "rule", "msg")
    out = audit.format_text([f], color=True)
    assert "\033[" in out
    assert "critical" in out


def test_format_text_with_all_severities(tmp_path):
    audit = _load()
    findings = [
        audit.Finding("critical", "secret", "f", 1, "r1", "m1"),
        audit.Finding("major",    "injection", "f", 2, "r2", "m2"),
        audit.Finding("minor",    "secret", "f", 3, "r3", "m3"),
        audit.Finding("nit",      "agent", "f", 4, "r4", "m4"),
    ]
    text = audit.format_text(findings)
    for sev in ("critical", "major", "minor", "nit"):
        assert sev in text


def test_format_markdown_with_all_severities():
    audit = _load()
    findings = [
        audit.Finding("critical", "secret", "f", 1, "r1", "m1"),
        audit.Finding("major",    "injection", "f", 2, "r2", "m2"),
    ]
    md = audit.format_markdown(findings)
    assert "## Audit Report" in md
    assert "Critical" in md
    assert "Major" in md


def test_gather_files_walks_directory(tmp_path):
    audit = _load()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("x=1")
    (tmp_path / "sub" / "b.json").write_text("{}")
    files = audit.gather_files([tmp_path])
    names = {f.name for f in files}
    assert "a.py" in names
    assert "b.json" in names


def test_skip_dotfile_dirs(tmp_path):
    audit = _load()
    # Items under .someweird should be skipped
    (tmp_path / ".weird").mkdir()
    (tmp_path / ".weird" / "junk.py").write_text("x=1")
    files = audit.gather_files([tmp_path])
    assert all("/.weird/" not in str(f) for f in files)


def test_skip_binary_extensions(tmp_path):
    audit = _load()
    (tmp_path / "file.png").write_bytes(b"\x89PNG")
    (tmp_path / "file.pdf").write_bytes(b"%PDF")
    (tmp_path / "file.sqlite").write_bytes(b"SQLite format 3")
    files = audit.gather_files([tmp_path])
    assert files == []


def test_audit_main_text_output_colorless(tmp_path, capsys):
    audit = _load()
    rc = audit.main(["--repo", str(tmp_path), "--no-color"])
    out = capsys.readouterr().out
    assert "AUDIT: clean" in out
    assert rc == 0


def test_audit_main_markdown_clean(tmp_path, capsys):
    audit = _load()
    rc = audit.main(["--repo", str(tmp_path), "--markdown"])
    out = capsys.readouterr().out
    assert "**AUDIT:**" in out
    assert rc == 0


def test_default_targets_picks_up_existing_paths(tmp_path):
    """default_targets returns the subset of standard locations that exist."""
    audit = _load()
    (tmp_path / "agents").mkdir()
    (tmp_path / "commands").mkdir()
    targets = audit.default_targets(tmp_path)
    paths = [str(p) for p in targets]
    assert any("agents" in p for p in paths)
    assert any("commands" in p for p in paths)


def test_scan_handles_unreadable_file(tmp_path):
    """A path that can't be read should yield no findings, not crash."""
    audit = _load()
    p = tmp_path / "ghost"  # nonexistent
    assert audit.scan_secrets(p) == []
    assert audit.scan_injection(p) == []
