#!/usr/bin/env python3
"""Detect a project's stack from manifest files. Emits structured JSON.

Used by the UCW onboarder to seed .ucw/knowledge/STACK.md and CONVENTIONS.md
without asking the user about things that can be inferred.

Usage:
    ucw-detect-stack.py [PROJECT_ROOT]

Output (stdout, JSON):
    {
      "languages":      ["typescript", "python"],
      "runtimes":       ["node@20", "python@3.12"],
      "package_managers": ["pnpm", "uv"],
      "frameworks":     ["next", "fastapi"],
      "test_runners":   ["vitest", "pytest"],
      "linters":        ["eslint", "ruff"],
      "formatters":     ["prettier", "black"],
      "datastores":     ["postgres"],
      "deploy_targets": ["vercel"],
      "key_libraries":  [...],
      "evidence":       [{"file": "package.json", "found": [...]}]
    }
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


# ---- helpers -----------------------------------------------------------------

def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_json(path: Path) -> dict[str, Any] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _load_toml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _exists(root: Path, *names: str) -> Path | None:
    for name in names:
        p = root / name
        if p.exists():
            return p
    return None


# ---- detectors ---------------------------------------------------------------

def detect_node(root: Path, state: dict[str, Any]) -> None:
    pkg = _load_json(root / "package.json")
    if pkg is None:
        return

    state["evidence"].append({"file": "package.json", "found": list(pkg.keys())})

    # Language: TS if tsconfig.json or typescript dep
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    has_ts = "typescript" in deps or (root / "tsconfig.json").exists()
    state["languages"].add("typescript" if has_ts else "javascript")

    # Package manager — lockfile sniff
    if (root / "pnpm-lock.yaml").exists():
        state["package_managers"].add("pnpm")
    elif (root / "yarn.lock").exists():
        state["package_managers"].add("yarn")
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        state["package_managers"].add("bun")
    elif (root / "package-lock.json").exists():
        state["package_managers"].add("npm")
    else:
        # ambiguous — let onboarder ask
        pass

    # Runtime
    engines = pkg.get("engines", {})
    if "node" in engines:
        state["runtimes"].add(f"node@{engines['node']}")
    elif (root / ".nvmrc").exists():
        version = _read_text(root / ".nvmrc")
        if version:
            state["runtimes"].add(f"node@{version.strip().lstrip('v')}")

    # Frameworks — order matters; first match wins per family
    fw_map = {
        "next":    "next",
        "react":   "react",
        "vue":     "vue",
        "svelte":  "svelte",
        "solid":   "solid",
        "express": "express",
        "fastify": "fastify",
        "hono":    "hono",
        "nestjs":  "nestjs",
        "@nestjs/core": "nestjs",
        "remix":   "remix",
        "astro":   "astro",
        "nuxt":    "nuxt",
    }
    for dep, fw in fw_map.items():
        if dep in deps:
            state["frameworks"].add(fw)

    # Test runners
    test_map = {"vitest": "vitest", "jest": "jest", "mocha": "mocha", "@playwright/test": "playwright"}
    for dep, runner in test_map.items():
        if dep in deps:
            state["test_runners"].add(runner)

    # Linters / formatters
    if "eslint" in deps or _exists(root, ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml", ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs"):
        state["linters"].add("eslint")
    if "prettier" in deps or _exists(root, ".prettierrc", ".prettierrc.json", ".prettierrc.yaml", "prettier.config.js"):
        state["formatters"].add("prettier")
    if "biome" in deps or (root / "biome.json").exists():
        state["linters"].add("biome")
        state["formatters"].add("biome")


def detect_python(root: Path, state: dict[str, Any]) -> None:
    pyproject = _load_toml(root / "pyproject.toml")
    if pyproject is None and not (root / "requirements.txt").exists() and not (root / "setup.py").exists():
        return

    state["languages"].add("python")
    state["evidence"].append({"file": "pyproject.toml" if pyproject else "requirements.txt", "found": ["python project"]})

    # Package manager
    if (root / "uv.lock").exists():
        state["package_managers"].add("uv")
    elif (root / "poetry.lock").exists():
        state["package_managers"].add("poetry")
    elif (root / "Pipfile.lock").exists():
        state["package_managers"].add("pipenv")
    else:
        state["package_managers"].add("pip")

    deps: list[str] = []
    if pyproject:
        project = pyproject.get("project", {})
        deps.extend(project.get("dependencies", []))
        # PEP 735 dependency-groups
        for group in pyproject.get("dependency-groups", {}).values():
            if isinstance(group, list):
                deps.extend(group)
        # poetry layout
        poetry = pyproject.get("tool", {}).get("poetry", {})
        deps.extend(poetry.get("dependencies", {}).keys())

        # Runtime
        py_req = project.get("requires-python")
        if py_req:
            state["runtimes"].add(f"python{py_req}")

    req_txt = _read_text(root / "requirements.txt")
    if req_txt:
        deps.extend(line.split("==")[0].split(">=")[0].split("<")[0].strip()
                    for line in req_txt.splitlines()
                    if line.strip() and not line.startswith("#"))

    deps_lower = [_normalize_dep(d) for d in deps]

    fw_map = {
        "django":   "django",
        "flask":    "flask",
        "fastapi":  "fastapi",
        "starlette":"starlette",
        "litestar": "litestar",
        "aiohttp":  "aiohttp",
        "tornado":  "tornado",
        "pyramid":  "pyramid",
    }
    for dep, fw in fw_map.items():
        if dep in deps_lower:
            state["frameworks"].add(fw)

    if "pytest" in deps_lower:
        state["test_runners"].add("pytest")
    if any(d == "unittest" for d in deps_lower) or (pyproject and "unittest" in str(pyproject)):
        pass  # unittest is stdlib; only flag if no other runner

    if "ruff" in deps_lower or (root / "ruff.toml").exists() or (pyproject and "tool" in pyproject and "ruff" in pyproject["tool"]):
        state["linters"].add("ruff")
        state["formatters"].add("ruff")
    if "black" in deps_lower or (pyproject and "tool" in pyproject and "black" in pyproject["tool"]):
        state["formatters"].add("black")
    if "mypy" in deps_lower or (root / "mypy.ini").exists() or (pyproject and "tool" in pyproject and "mypy" in pyproject["tool"]):
        state["linters"].add("mypy")


def _normalize_dep(dep: str) -> str:
    # Strip version specifiers and extras: "foo[bar]>=1.0" -> "foo"
    return re.split(r"[\[<>=!~ ]", dep, 1)[0].strip().lower()


def detect_go(root: Path, state: dict[str, Any]) -> None:
    gomod = _read_text(root / "go.mod")
    if gomod is None:
        return
    state["languages"].add("go")
    state["evidence"].append({"file": "go.mod", "found": []})

    m = re.search(r"^go\s+(\S+)", gomod, re.MULTILINE)
    if m:
        state["runtimes"].add(f"go@{m.group(1)}")
    state["package_managers"].add("go-modules")

    if "gin-gonic/gin" in gomod:
        state["frameworks"].add("gin")
    if "labstack/echo" in gomod:
        state["frameworks"].add("echo")
    if "gofiber/fiber" in gomod:
        state["frameworks"].add("fiber")
    if "go-chi/chi" in gomod:
        state["frameworks"].add("chi")

    if (root / ".golangci.yml").exists() or (root / ".golangci.yaml").exists():
        state["linters"].add("golangci-lint")
    state["formatters"].add("gofmt")


def detect_rust(root: Path, state: dict[str, Any]) -> None:
    cargo = _load_toml(root / "Cargo.toml")
    if cargo is None:
        return
    state["languages"].add("rust")
    state["evidence"].append({"file": "Cargo.toml", "found": list(cargo.keys())})

    pkg = cargo.get("package", {})
    edition = pkg.get("edition")
    if edition:
        state["runtimes"].add(f"rust@edition-{edition}")

    state["package_managers"].add("cargo")
    deps = {**cargo.get("dependencies", {}), **cargo.get("dev-dependencies", {})}
    fw_map = {"actix-web": "actix-web", "axum": "axum", "rocket": "rocket", "warp": "warp", "tokio": "tokio"}
    for dep, fw in fw_map.items():
        if dep in deps:
            state["frameworks"].add(fw)

    state["formatters"].add("rustfmt")
    state["linters"].add("clippy")


def detect_infra(root: Path, state: dict[str, Any]) -> None:
    """Datastores from docker-compose, deploy targets from config files."""
    compose = _read_text(root / "docker-compose.yml") or _read_text(root / "compose.yml")
    if compose:
        for db, image in [
            ("postgres", "postgres"), ("mysql", "mysql"), ("mongo", "mongo"),
            ("redis", "redis"), ("mariadb", "mariadb"), ("elasticsearch", "elasticsearch"),
        ]:
            if re.search(rf"image:\s*{image}", compose, re.IGNORECASE):
                state["datastores"].add(db)

    if (root / "vercel.json").exists():
        state["deploy_targets"].add("vercel")
    if (root / "netlify.toml").exists():
        state["deploy_targets"].add("netlify")
    if (root / "fly.toml").exists():
        state["deploy_targets"].add("fly.io")
    if (root / "wrangler.toml").exists() or (root / "wrangler.jsonc").exists():
        state["deploy_targets"].add("cloudflare-workers")
    if (root / "railway.json").exists() or (root / "railway.toml").exists():
        state["deploy_targets"].add("railway")
    if (root / "Dockerfile").exists():
        state["evidence"].append({"file": "Dockerfile", "found": []})

    if (root / ".github" / "workflows").is_dir():
        state["evidence"].append({"file": ".github/workflows", "found": ["github-actions"]})


# ---- main --------------------------------------------------------------------

def detect(root: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "languages": set(), "runtimes": set(), "package_managers": set(),
        "frameworks": set(), "test_runners": set(), "linters": set(),
        "formatters": set(), "datastores": set(), "deploy_targets": set(),
        "key_libraries": set(), "evidence": [],
    }
    detect_node(root, state)
    detect_python(root, state)
    detect_go(root, state)
    detect_rust(root, state)
    detect_infra(root, state)

    # Freeze sets to sorted lists for stable output
    return {k: sorted(v) if isinstance(v, set) else v for k, v in state.items()}


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    json.dump(detect(root), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
