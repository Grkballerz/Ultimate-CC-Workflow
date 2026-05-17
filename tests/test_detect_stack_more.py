"""Edge-case coverage for ucw-detect-stack.py — language packs not in the
core tests, package-manager variations, deploy-target detection.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_detect_stack_more", REPO_ROOT / "bin" / "ucw-detect-stack.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_detect_stack_more"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---- Node ecosystem variations ----------------------------------------------

def test_yarn_lockfile_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({
        "name": "x", "dependencies": {"react": "18"}
    }))
    _write(tmp_path / "yarn.lock", "")
    state = d.detect(tmp_path)
    assert "yarn" in state["package_managers"]


def test_bun_lockfile_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({"name": "x"}))
    _write(tmp_path / "bun.lockb", "")
    state = d.detect(tmp_path)
    assert "bun" in state["package_managers"]


def test_npm_lockfile_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({"name": "x"}))
    _write(tmp_path / "package-lock.json", "{}")
    state = d.detect(tmp_path)
    assert "npm" in state["package_managers"]


def test_nvmrc_runtime_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({"name": "x"}))
    _write(tmp_path / ".nvmrc", "v18.17.0\n")
    state = d.detect(tmp_path)
    assert any("node@18.17.0" in r for r in state["runtimes"])


def test_biome_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({
        "name": "x", "devDependencies": {"biome": "1"}
    }))
    _write(tmp_path / "biome.json", "{}")
    state = d.detect(tmp_path)
    assert "biome" in state["linters"]
    assert "biome" in state["formatters"]


def test_javascript_without_typescript(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({"name": "x"}))
    state = d.detect(tmp_path)
    assert "javascript" in state["languages"]
    assert "typescript" not in state["languages"]


def test_remix_and_astro_detected(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", json.dumps({
        "name": "x", "dependencies": {"remix": "1", "astro": "3"}
    }))
    state = d.detect(tmp_path)
    assert "remix" in state["frameworks"]
    assert "astro" in state["frameworks"]


# ---- Python ecosystem variations --------------------------------------------

def test_poetry_lockfile(tmp_path):
    d = _load()
    _write(tmp_path / "pyproject.toml", """
[tool.poetry]
name = "demo"

[tool.poetry.dependencies]
django = "*"
""")
    _write(tmp_path / "poetry.lock", "")
    state = d.detect(tmp_path)
    assert "poetry" in state["package_managers"]
    assert "django" in state["frameworks"]


def test_pipenv_lockfile(tmp_path):
    d = _load()
    _write(tmp_path / "pyproject.toml", """
[project]
name = "demo"
""")
    _write(tmp_path / "Pipfile.lock", "{}")
    state = d.detect(tmp_path)
    assert "pipenv" in state["package_managers"]


def test_requirements_txt_only(tmp_path):
    d = _load()
    _write(tmp_path / "requirements.txt", "flask==3.0\npytest>=7\n# comment\nmypy\n")
    state = d.detect(tmp_path)
    assert "python" in state["languages"]
    assert "flask" in state["frameworks"]
    assert "pytest" in state["test_runners"]
    assert "mypy" in state["linters"]


def test_black_formatter_via_pyproject(tmp_path):
    d = _load()
    _write(tmp_path / "pyproject.toml", """
[project]
name = "demo"

[tool.black]
line-length = 100
""")
    state = d.detect(tmp_path)
    assert "black" in state["formatters"]


def test_starlette_aiohttp_tornado_frameworks(tmp_path):
    d = _load()
    _write(tmp_path / "pyproject.toml", """
[project]
name = "demo"
dependencies = ["starlette", "aiohttp", "tornado"]
""")
    state = d.detect(tmp_path)
    assert "starlette" in state["frameworks"]
    assert "aiohttp" in state["frameworks"]
    assert "tornado" in state["frameworks"]


# ---- Go ecosystem variations ------------------------------------------------

def test_go_gin_echo_fiber(tmp_path):
    d = _load()
    _write(tmp_path / "go.mod", """\
module example.com/demo
go 1.22
require (
    github.com/gin-gonic/gin v1.9
    github.com/labstack/echo/v4 v4.11
    github.com/gofiber/fiber/v2 v2.50
)
""")
    state = d.detect(tmp_path)
    assert "gin" in state["frameworks"]
    assert "echo" in state["frameworks"]
    assert "fiber" in state["frameworks"]


def test_go_golangci_lint(tmp_path):
    d = _load()
    _write(tmp_path / "go.mod", "module x\ngo 1.22\n")
    _write(tmp_path / ".golangci.yml", "")
    state = d.detect(tmp_path)
    assert "golangci-lint" in state["linters"]


# ---- Rust ecosystem ---------------------------------------------------------

def test_rust_actix_rocket_warp(tmp_path):
    d = _load()
    _write(tmp_path / "Cargo.toml", """
[package]
name = "demo"
version = "0.1.0"
edition = "2021"

[dependencies]
actix-web = "4"
rocket = "0.5"
warp = "0.3"
""")
    state = d.detect(tmp_path)
    assert "actix-web" in state["frameworks"]
    assert "rocket" in state["frameworks"]
    assert "warp" in state["frameworks"]


# ---- Infra variations -------------------------------------------------------

def test_deploy_target_netlify(tmp_path):
    d = _load()
    _write(tmp_path / "netlify.toml", "")
    state = d.detect(tmp_path)
    assert "netlify" in state["deploy_targets"]


def test_deploy_target_fly(tmp_path):
    d = _load()
    _write(tmp_path / "fly.toml", "")
    state = d.detect(tmp_path)
    assert "fly.io" in state["deploy_targets"]


def test_deploy_target_cloudflare(tmp_path):
    d = _load()
    _write(tmp_path / "wrangler.toml", "")
    state = d.detect(tmp_path)
    assert "cloudflare-workers" in state["deploy_targets"]


def test_deploy_target_railway(tmp_path):
    d = _load()
    _write(tmp_path / "railway.json", "{}")
    state = d.detect(tmp_path)
    assert "railway" in state["deploy_targets"]


def test_dockerfile_recorded_in_evidence(tmp_path):
    d = _load()
    _write(tmp_path / "Dockerfile", "FROM scratch\n")
    state = d.detect(tmp_path)
    assert any(e.get("file") == "Dockerfile" for e in state["evidence"])


def test_github_actions_recorded(tmp_path):
    d = _load()
    _write(tmp_path / ".github" / "workflows" / "ci.yml", "name: CI\n")
    state = d.detect(tmp_path)
    assert any(e.get("file", "").endswith("workflows") for e in state["evidence"])


def test_mysql_mongo_mariadb_elastic_compose(tmp_path):
    d = _load()
    _write(tmp_path / "docker-compose.yml", """
services:
  db1: { image: mysql:8 }
  db2: { image: mongo:7 }
  db3: { image: mariadb:11 }
  search: { image: elasticsearch:8 }
""")
    state = d.detect(tmp_path)
    for db in ("mysql", "mongo", "mariadb", "elasticsearch"):
        assert db in state["datastores"]


# ---- Error paths -------------------------------------------------------------

def test_corrupt_package_json_handled(tmp_path):
    d = _load()
    _write(tmp_path / "package.json", "{ this is not json")
    state = d.detect(tmp_path)
    # Corrupt manifest: no language inferred, no crash
    assert "javascript" not in state["languages"]
    assert "typescript" not in state["languages"]


def test_corrupt_pyproject_toml_handled(tmp_path):
    d = _load()
    _write(tmp_path / "pyproject.toml", "[[broken]]\n=invalid")
    # Falls back to requirements.txt detection — but neither exists
    state = d.detect(tmp_path)
    assert "python" not in state["languages"]


# ---- CLI ---------------------------------------------------------------------

def test_main_default_cwd_works(tmp_path, monkeypatch, capsys):
    """Calling without args defaults to current directory."""
    d = _load()
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "package.json", json.dumps({"name": "x"}))
    rc = d.main(["ucw-detect-stack.py"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert "javascript" in body["languages"]


def test_main_nonexistent_path_returns_2(tmp_path, capsys):
    d = _load()
    rc = d.main(["ucw-detect-stack.py", str(tmp_path / "does-not-exist")])
    assert rc == 2
