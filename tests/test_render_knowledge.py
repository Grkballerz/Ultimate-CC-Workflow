"""Tests for the knowledge renderer."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_render():
    spec = importlib.util.spec_from_file_location(
        "ucw_render_knowledge", REPO_ROOT / "bin" / "ucw-render-knowledge.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_render_knowledge"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _state(tmp_path):
    return {
        "stack": {
            "languages": ["python"],
            "frameworks": ["flask"],
            "package_managers": ["uv"],
            "test_runners": ["pytest"],
            "linters": ["ruff"],
            "formatters": ["ruff"],
            "datastores": ["postgres"],
            "deploy_targets": ["fly.io"],
            "runtimes": ["python>=3.12"],
            "build_tools": [],
            "key_libraries": [],
            "evidence": [{"file": "pyproject.toml", "found": ["python project"]}],
        },
        "preferences": {
            "image_gen_tool": "Flux",
            "diagram_tool": "Mermaid",
            "deploy_target": "Fly.io",
            "database": "Postgres",
            "package_manager": "uv",
            "docs_surface": "Plain `.ucw/knowledge/`",
            "embedding_provider": "Voyage",
            "scribe_mode": "auto",
        },
        "project_summary": "A flask demo with /healthz.",
        "init_at": "2026-05-17",
    }


def test_renders_all_files(tmp_path):
    render = _load_render()
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_state(tmp_path)))
    project_root = tmp_path / "project"
    project_root.mkdir()

    rc = render.main(["ucw-render-knowledge", "--state", str(state_file), "--project-root", str(project_root)])
    assert rc == 0

    knowledge = project_root / ".ucw" / "knowledge"
    for name in ("INDEX.md", "STACK.md", "DESIGN.md", "CONVENTIONS.md",
                 "GLOSSARY.md", "ROADMAP.md", "PREFERENCES.md"):
        path = knowledge / name
        assert path.exists(), f"missing {name}"
        text = path.read_text(encoding="utf-8")
        assert "{{" not in text, f"unsubstituted placeholder in {name}"


def test_skips_existing_without_force(tmp_path):
    render = _load_render()
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_state(tmp_path)))
    project_root = tmp_path / "project"
    knowledge = project_root / ".ucw" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "STACK.md").write_text("user-edited content — do not clobber", encoding="utf-8")

    render.main(["ucw-render-knowledge", "--state", str(state_file), "--project-root", str(project_root)])
    assert "user-edited" in (knowledge / "STACK.md").read_text(encoding="utf-8")

    # With --force, overwrite
    render.main(["ucw-render-knowledge", "--state", str(state_file), "--project-root", str(project_root), "--force"])
    assert "user-edited" not in (knowledge / "STACK.md").read_text(encoding="utf-8")


def test_preferences_includes_user_tooling(tmp_path):
    render = _load_render()
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(_state(tmp_path)))
    project_root = tmp_path / "project"
    project_root.mkdir()

    render.main(["ucw-render-knowledge", "--state", str(state_file), "--project-root", str(project_root)])
    prefs = (project_root / ".ucw" / "knowledge" / "PREFERENCES.md").read_text(encoding="utf-8")
    assert "Flux" in prefs
    assert "Mermaid" in prefs
    assert "Voyage" in prefs
