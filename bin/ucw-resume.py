#!/usr/bin/env python3
"""Print a UCW resume block — what the agent needs to know after `/clear` or
`/compact` to pick up where it left off.

Single-shot helper, no side effects. Reads:
  .ucw/state/phase           current workflow phase
  .ucw/state/auto-mode       JSON {level, since, retry_cap}
  .ucw/state/auto-retries    current retry count (PR B)
  .ucw/state/edit-streak     edits-without-test counter
  .ucw/state/plan.md         planner's task list
  .ucw/state/spec.md         planner's spec paragraph (NEW in PR D)
  .ucw/state/pre-compact-digest.md   if PreCompact fired

Plus a couple of git probes (`HEAD`, `status --porcelain`) so the block
also shows the last commit + a dirty-tree warning when relevant.

Output is markdown for the agent (sized for additionalContext), not JSON.
The plan and spec sections are truncated so the whole block fits a few
KB. If nothing is set ("clean slate" — no UCW state, no commits), exit 0
with a short note rather than failing.

Usage:
    ucw-resume.py [--repo .] [--plan-bytes 3000] [--spec-bytes 1500]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _find_project_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".ucw").is_dir() or (parent / ".git").exists():
            return parent
    return start


def _read(path: Path, *, max_bytes: int | None = None) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if max_bytes is not None and len(text) > max_bytes:
        return text[:max_bytes].rstrip() + f"\n\n_… truncated at {max_bytes} chars …_"
    return text


def _state_int(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _auto_mode(state_dir: Path) -> tuple[int, dict | None]:
    """Return (level, full state dict or None). Env override wins."""
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    if env in {"0", "off", "no", "false"}:
        return 0, None
    if env.isdigit() and int(env) in (1, 2, 3, 4):
        return int(env), {"level": int(env), "source": "env"}
    if env == "on":
        return 4, {"level": 4, "source": "env"}

    sf = state_dir / "auto-mode"
    if not sf.exists():
        return 0, None
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, None
    level = data.get("level")
    if isinstance(level, int) and level in (1, 2, 3, 4):
        return level, data
    return 0, None


def _git(args: list[str], *, cwd: Path) -> str | None:
    """Run git, return stripped stdout or None on failure."""
    try:
        cp = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=5, check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return cp.stdout.strip()


def render(repo: Path, *, plan_bytes: int = 3000, spec_bytes: int = 1500) -> str:
    state_dir = repo / ".ucw" / "state"
    knowledge_dir = repo / ".ucw" / "knowledge"

    phase = _read(state_dir / "phase").strip() or None
    streak = _state_int(state_dir / "edit-streak")
    retries = _state_int(state_dir / "auto-retries")
    level, auto_state = _auto_mode(state_dir)

    plan_md = _read(state_dir / "plan.md", max_bytes=plan_bytes)
    spec_md = _read(state_dir / "spec.md", max_bytes=spec_bytes)
    digest = _read(state_dir / "pre-compact-digest.md", max_bytes=800)

    head_sha = _git(["rev-parse", "--short", "HEAD"], cwd=repo)
    head_subject = _git(["log", "-1", "--pretty=%s"], cwd=repo) if head_sha else None
    head_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    dirty_files = _git(["status", "--porcelain"], cwd=repo)
    dirty_count = len([ln for ln in (dirty_files or "").splitlines() if ln.strip()])

    # Empty state → clean-slate message, exit 0
    if not any([phase, level, plan_md, spec_md, head_sha]):
        return ("# UCW Resume\n\n"
                f"No UCW state in `{repo}`. Either this isn't a UCW project "
                "or no work has started yet — run `/ucw init` to bootstrap, "
                "or `/ucw plan <goal>` to begin a new task.\n")

    lines: list[str] = ["# UCW Resume\n"]
    lines.append(f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%S')} for `{repo}`._\n")

    # Workflow state
    state_table = []
    if phase:
        state_table.append(f"- **Phase**: `{phase}`")
    if level:
        since = (auto_state or {}).get("since")
        retry_cap = (auto_state or {}).get("retry_cap", 3)
        state_table.append(
            f"- **Auto-mode**: level {level}"
            + (f" (since {since})" if since else "")
            + f" — retries {retries}/{retry_cap}"
        )
    else:
        state_table.append("- **Auto-mode**: off")
    if streak:
        state_table.append(f"- **Edit streak**: {streak} (since last test run)")
    lines.append("## Workflow state\n\n" + "\n".join(state_table) + "\n")

    # Git
    if head_sha:
        git_lines = [f"- **HEAD**: `{head_sha}` — {head_subject or '(no subject)'}"]
        if head_branch and head_branch != "HEAD":
            git_lines.append(f"- **Branch**: `{head_branch}`")
        if dirty_count:
            warn_emoji = ""  # plain text — no emojis per project policy
            warn = f"- **Dirty tree**: {dirty_count} file(s) with uncommitted changes"
            if level >= 3 and phase == "land":
                warn += (
                    "\n  - **WARNING**: auto-mode is on AND phase is `land`. "
                    "Previous session may have crashed mid-ship. "
                    "Check `git status` before continuing — auto-mode commits "
                    "everything in the tree."
                )
            git_lines.append(warn)
        lines.append("## Git\n\n" + "\n".join(git_lines) + "\n")

    # Spec
    if spec_md:
        lines.append("## Spec (`.ucw/state/spec.md`)\n\n" + spec_md.strip() + "\n")
    elif phase:
        lines.append(
            "## Spec\n\n"
            "_No `.ucw/state/spec.md` found. If the scope phase ran before "
            "PR D shipped, the spec was never persisted — re-run "
            "`/ucw plan <goal>` to regenerate it, or rely on `plan.md` below._\n"
        )

    # Plan
    if plan_md:
        lines.append("## Plan (`.ucw/state/plan.md`)\n\n" + plan_md.strip() + "\n")

    # Knowledge inventory (just the file list — INDEX is small enough)
    if knowledge_dir.is_dir():
        knowledge_files = sorted(p.name for p in knowledge_dir.glob("*.md"))
        if knowledge_files:
            lines.append(
                "## Knowledge\n\n"
                f"`.ucw/knowledge/` has: {', '.join(f'`{f}`' for f in knowledge_files)}. "
                "Mention any keyword (\"stack\", \"design\", \"convention\", \"roadmap\", "
                "\"glossary\", \"preferences\") in a prompt to auto-inject the matching doc.\n"
            )

    # PreCompact digest (only if recent)
    if digest:
        lines.append("## Pre-compact digest\n\n" + digest.strip() + "\n")

    lines.append(
        "---\n\n"
        "_Run `/ucw status` for the dashboard view, or `/ucw auto status` "
        "for auto-mode details. Disable auto-mode any time with "
        "`/ucw auto off` or `UCW_AUTO_MODE=off`._"
    )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ucw-resume",
        description="Print a UCW resume block — what the agent needs after "
                    "/clear or /compact to pick up where it left off.",
    )
    parser.add_argument("--repo", default=".", help="project root (default: cwd)")
    parser.add_argument("--plan-bytes", type=int, default=3000,
                        help="max bytes of plan.md to include (default 3000)")
    parser.add_argument("--spec-bytes", type=int, default=1500,
                        help="max bytes of spec.md to include (default 1500)")
    args = parser.parse_args(argv)

    repo = _find_project_root(Path(args.repo).resolve())
    sys.stdout.write(render(repo, plan_bytes=args.plan_bytes,
                            spec_bytes=args.spec_bytes))
    if not sys.stdout.write("\n"):
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
