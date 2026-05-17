#!/usr/bin/env python3
"""Summarize what's changed in the repo since the last scribe run.

Output is consumed by the **scribe** subagent — it tells the agent
*specifically* which Knowledge docs likely need updates and why.

Mechanics:
- Reads `.ucw/last-scribe-sha` (or falls back to HEAD~1 if absent).
- Runs `git diff --name-status <sha> HEAD` to get changed paths.
- Classifies each file by what knowledge-doc impact it likely has:
    package.json/lockfile/pyproject.toml → STACK.md
    new file under src/, lib/, internal/   → DESIGN.md (potential abstraction)
    .ucw/knowledge/*                       → already touched, no action
    rules/CONVENTIONS.md, .eslintrc.*      → CONVENTIONS.md
    README.md, docs/*                      → noise
- Emits JSON: { changed: [...], suggested_updates: { doc: [reason, ...] } }
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

STACK_TRIGGERS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
    "pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
}

CONVENTIONS_TRIGGER_PATTERNS = [
    re.compile(r"^\.eslintrc"),
    re.compile(r"^\.prettierrc"),
    re.compile(r"^biome\.json"),
    re.compile(r"^ruff\.toml"),
    re.compile(r"^pyrightconfig\.json"),
    re.compile(r"^\.golangci\."),
]

DESIGN_DIR_PREFIXES = ("src/", "lib/", "internal/", "app/", "pkg/", "memory/", "hooks/", "bin/")

NOISE_PATTERNS = [
    re.compile(r"^README\."),
    re.compile(r"^docs/"),
    re.compile(r"^\.ucw/knowledge/"),
    re.compile(r"^\.gitignore$"),
    re.compile(r"^LICENSE$"),
]


def _git(*args, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _read_last_sha(repo_root: Path) -> str | None:
    sf = repo_root / ".ucw" / "last-scribe-sha"
    if sf.exists():
        try:
            return sf.read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


def _changed_files(repo_root: Path, since: str) -> list[tuple[str, str]]:
    out = _git("diff", "--name-status", f"{since}..HEAD", cwd=repo_root)
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[1].strip()
        rows.append((status, path))
    return rows


def _classify(path: str) -> list[str]:
    """Which knowledge docs does this path likely impact?"""
    impacted: list[str] = []
    base = path.split("/")[-1]
    if base in STACK_TRIGGERS:
        impacted.append("STACK.md")
    if any(p.match(base) for p in CONVENTIONS_TRIGGER_PATTERNS):
        impacted.append("CONVENTIONS.md")
    if any(path.startswith(p) for p in DESIGN_DIR_PREFIXES):
        impacted.append("DESIGN.md")
    if any(p.match(path) for p in NOISE_PATTERNS):
        return []
    return impacted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-knowledge-diff")
    parser.add_argument("--repo", default=".", help="repo root (default: cwd)")
    parser.add_argument("--since", help="explicit base ref (default: .ucw/last-scribe-sha or HEAD~1)")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not (repo_root / ".git").exists():
        print(json.dumps({"error": "not a git repo", "repo": str(repo_root)}))
        return 2

    since = args.since or _read_last_sha(repo_root)
    if since is None:
        # First-time scribe run: compare against HEAD~1 if it exists, else nothing.
        try:
            since = _git("rev-parse", "HEAD~1", cwd=repo_root)
        except subprocess.CalledProcessError:
            since = None

    if since is None:
        print(json.dumps({"changed": [], "suggested_updates": {}, "since": None}))
        return 0

    try:
        changed = _changed_files(repo_root, since)
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"error": "git diff failed", "stderr": str(exc)}))
        return 2

    suggested: dict[str, list[str]] = {}
    for status, path in changed:
        for doc in _classify(path):
            suggested.setdefault(doc, []).append(f"{status} {path}")

    result = {
        "since": since,
        "head": _git("rev-parse", "HEAD", cwd=repo_root),
        "changed": [{"status": s, "path": p} for s, p in changed],
        "suggested_updates": suggested,
    }
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
