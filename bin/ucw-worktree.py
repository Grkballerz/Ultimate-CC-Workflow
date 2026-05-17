#!/usr/bin/env python3
"""Manage parallel worktrees for multi-agent fan-out.

UCW's orchestrator uses git worktrees to run independent plan tasks in
parallel without conflicts. Each worktree gets:
- A unique branch named `ucw/wt/<slug>-<short_id>`
- A `.ucw/worktree-meta.json` recording parent ref + creation time

    ucw-worktree.py create <slug>          # create a new worktree under .ucw/wt/
    ucw-worktree.py list                    # list active UCW worktrees
    ucw-worktree.py cleanup [--max-age 24h] # remove old idle worktrees
    ucw-worktree.py remove <slug>           # remove a specific one
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _git(*args, cwd: Path | None = None, check: bool = True) -> str:
    cp = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)
    return cp.stdout.strip()


def _repo_root() -> Path:
    return Path(_git("rev-parse", "--show-toplevel"))


def _wt_root() -> Path:
    return _repo_root() / ".ucw" / "wt"


def _slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text.lower()).strip("-")
    return s[:32] or "task"


def cmd_create(args: argparse.Namespace) -> int:
    slug = _slugify(args.slug)
    short_id = uuid.uuid4().hex[:6]
    branch = f"ucw/wt/{slug}-{short_id}"
    wt_path = _wt_root() / f"{slug}-{short_id}"

    parent_ref = args.base or _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("worktree", "add", "-b", branch, str(wt_path), parent_ref)

    meta = {
        "slug": slug,
        "branch": branch,
        "parent_ref": parent_ref,
        "created_at": int(time.time()),
        "path": str(wt_path),
    }
    meta_path = wt_path / ".ucw" / "worktree-meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps(meta, indent=2))
    return 0


def _list_worktrees() -> list[dict]:
    """Parse `git worktree list --porcelain` into structured entries."""
    raw = _git("worktree", "list", "--porcelain")
    entries: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def cmd_list(args: argparse.Namespace) -> int:
    entries = [e for e in _list_worktrees() if "/ucw/wt/" in e.get("branch", "")]
    if args.json:
        json.dump(entries, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif not entries:
        print("(no UCW worktrees)")
    else:
        for e in entries:
            print(f"{e.get('branch', '?'):<40}  {e.get('path', '?')}")
    return 0


def _parse_max_age(text: str) -> int:
    """'24h' / '7d' / '120s' → seconds."""
    m = re.fullmatch(r"(\d+)\s*([smhd])?", text.strip())
    if not m:
        raise ValueError(f"bad duration: {text}")
    n = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _remove_one(path: str, branch: str, *, force: bool) -> None:
    """Helper: remove worktree at path, then delete its branch."""
    args = ["worktree", "remove", path]
    if force:
        args.append("--force")
    _git(*args, check=False)
    branch_short = branch.replace("refs/heads/", "")
    _git("branch", "-D" if force else "-d", branch_short, check=False)


def cmd_cleanup(args: argparse.Namespace) -> int:
    max_age = _parse_max_age(args.max_age) if args.max_age else None
    removed: list[str] = []
    skipped: list[str] = []

    for e in _list_worktrees():
        branch = e.get("branch", "")
        path_str = e.get("path", "")
        if "/ucw/wt/" not in branch or not path_str:
            continue
        path = Path(path_str)
        meta_path = path / ".ucw" / "worktree-meta.json"
        parent_ref = "HEAD"
        age = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                age = time.time() - meta.get("created_at", 0)
                parent_ref = meta.get("parent_ref", "HEAD")
            except (OSError, json.JSONDecodeError):
                pass
        if max_age is not None and (age is None or age < max_age):
            skipped.append(branch)
            continue
        # Check if there are unpushed commits — refuse to remove if so unless --force
        if not args.force:
            try:
                ahead = _git("rev-list", "--count", f"{parent_ref}..{branch}",
                             cwd=path, check=False)
                if ahead and int(ahead.strip() or 0) > 0:
                    skipped.append(f"{branch} (has unpushed commits, use --force)")
                    continue
            except subprocess.CalledProcessError:
                pass
        _remove_one(path_str, branch, force=args.force)
        removed.append(branch)

    result = {"removed": removed, "skipped": skipped}
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"removed: {len(removed)}, skipped: {len(skipped)}")
        for s in skipped:
            print(f"  skip: {s}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    for e in _list_worktrees():
        branch = e.get("branch", "")
        if args.slug in branch and "/ucw/wt/" in branch:
            path = e.get("path", "")
            # Use --force by default for explicit remove (user asked for it)
            _remove_one(path, branch, force=True)
            print(f"removed {branch}")
            return 0
    print(f"no UCW worktree matches '{args.slug}'", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-worktree")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="create a new worktree")
    p_create.add_argument("slug", help="short name for the task")
    p_create.add_argument("--base", help="base ref (default: current HEAD)")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="list UCW worktrees")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_cleanup = sub.add_parser("cleanup", help="remove old worktrees")
    p_cleanup.add_argument("--max-age", help="e.g. 24h, 7d, 120s")
    p_cleanup.add_argument("--force", action="store_true",
                           help="remove even with unpushed commits")
    p_cleanup.add_argument("--json", action="store_true")
    p_cleanup.set_defaults(func=cmd_cleanup)

    p_remove = sub.add_parser("remove", help="remove a specific worktree")
    p_remove.add_argument("slug")
    p_remove.add_argument("--force", action="store_true")
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
