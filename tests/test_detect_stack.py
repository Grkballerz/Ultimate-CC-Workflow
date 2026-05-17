"""Tests for the stack detector. We build tiny synthetic projects and inspect the output."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_detect():
    """Load bin/ucw-detect-stack.py as a module (the dash in the name blocks normal import)."""
    spec = importlib.util.spec_from_file_location(
        "ucw_detect_stack", REPO_ROOT / "bin" / "ucw-detect-stack.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_detect_stack"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_node_typescript_pnpm_next(tmp_path):
    detect = _load_detect()
    _write(tmp_path / "package.json", json.dumps({
        "name": "demo",
        "engines": {"node": ">=20"},
        "dependencies": {"next": "14.0.0", "react": "18.0.0", "typescript": "5.0.0"},
        "devDependencies": {"vitest": "1.0.0", "eslint": "8.0.0", "prettier": "3.0.0"},
    }))
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: 6\n")
    _write(tmp_path / "tsconfig.json", "{}")
    _write(tmp_path / "vercel.json", "{}")

    state = detect.detect(tmp_path)
    assert "typescript" in state["languages"]
    assert "pnpm" in state["package_managers"]
    assert "next" in state["frameworks"]
    assert "react" in state["frameworks"]
    assert "vitest" in state["test_runners"]
    assert "eslint" in state["linters"]
    assert "prettier" in state["formatters"]
    assert "vercel" in state["deploy_targets"]
    assert any(rt.startswith("node@") for rt in state["runtimes"])


def test_python_uv_fastapi(tmp_path):
    detect = _load_detect()
    _write(tmp_path / "pyproject.toml", """
[project]
name = "demo"
requires-python = ">=3.12"
dependencies = ["fastapi", "ruff", "pytest"]

[tool.ruff]
line-length = 100
""")
    _write(tmp_path / "uv.lock", "")

    state = detect.detect(tmp_path)
    assert "python" in state["languages"]
    assert "uv" in state["package_managers"]
    assert "fastapi" in state["frameworks"]
    assert "ruff" in state["linters"]
    assert "ruff" in state["formatters"]
    assert "pytest" in state["test_runners"]


def test_go_chi(tmp_path):
    detect = _load_detect()
    _write(tmp_path / "go.mod", """\
module example.com/demo

go 1.22

require github.com/go-chi/chi/v5 v5.0.10
""")
    state = detect.detect(tmp_path)
    assert "go" in state["languages"]
    assert "go-modules" in state["package_managers"]
    assert "chi" in state["frameworks"]
    assert any(rt.startswith("go@") for rt in state["runtimes"])


def test_rust_axum(tmp_path):
    detect = _load_detect()
    _write(tmp_path / "Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"
edition = "2021"

[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
""")
    state = detect.detect(tmp_path)
    assert "rust" in state["languages"]
    assert "cargo" in state["package_managers"]
    assert "axum" in state["frameworks"]
    assert "tokio" in state["frameworks"]


def test_infra_postgres_compose(tmp_path):
    detect = _load_detect()
    _write(tmp_path / "compose.yml", """
services:
  db:
    image: postgres:16
  cache:
    image: redis:7
""")
    state = detect.detect(tmp_path)
    assert "postgres" in state["datastores"]
    assert "redis" in state["datastores"]


def test_empty_project_is_empty(tmp_path):
    detect = _load_detect()
    state = detect.detect(tmp_path)
    # No manifests → everything empty (but valid)
    assert state["languages"] == []
    assert state["package_managers"] == []
    assert state["frameworks"] == []
