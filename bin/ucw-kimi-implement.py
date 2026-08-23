#!/usr/bin/env python3
"""Offload an implementation task to Kimi K3 via the claude-kimi bridge.

Given a task prompt (arg or stdin) and a target repo dir, run Claude Code
headlessly against Kimi with file-edit rights but NO shell:

  claude-kimi -p "<prompt>" --allowedTools "Read,Edit,Write,Grep,Glob" \\
      --add-dir <repo>

Bash is deliberately EXCLUDED from the allowlist — the offloaded
implementer may read and edit files in the target repo but can never run
shell commands there. Verification (tests, lint) stays with the
orchestrating agent.

After the run, a summary of changed files is printed via
`git status --porcelain` in the target repo. That inspection is
read-only: this script NEVER runs `git add`, `git commit`, or `git push`.

Exit codes:
  0 — claude-kimi ran to completion ("Kimi made no edits" is still 0)
  1 — invocation failure (bad args, missing binary, timeout, nonzero exit)

subprocess is used directly rather than kimi_invoke.kimi_invoke because
that helper's ok/fail contract requires parsable JSON in stdout — an
implementer run emits prose, and its success signal is exit code 0.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Tools Kimi may use in the target repo. Bash is EXCLUDED on purpose:
# the offloaded implementer edits files; it never gets a shell.
KIMI_ALLOWED_TOOLS = "Read,Edit,Write,Grep,Glob"

# Last-resort fallback when neither the env var nor project settings pin a
# timeout. Higher than kimi_invoke's 300s single-answer default because an
# implementation run edits many files.
DEFAULT_TIMEOUT = 600

# Bound on the read-only `git status` summary shell-out (same 60s bound as
# ucw-kimi-opinion.py's shell-outs).
GIT_TIMEOUT = 60


def resolve_timeout(repo: Path) -> int:
    """Settings-resolved timeout, mirroring kimi_invoke's chain.

    UCW_KIMI_TIMEOUT_SECS env var > `<repo>/.ucw/state/settings.json` key
    `kimi.timeout_secs` > DEFAULT_TIMEOUT. Invalid values fall through —
    same escape-hatch tolerance as ucw-settings.
    """
    raw = os.environ.get("UCW_KIMI_TIMEOUT_SECS", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    try:
        data = json.loads((repo / ".ucw" / "state" / "settings.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        value = data.get("kimi.timeout_secs")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return DEFAULT_TIMEOUT


def build_cmd(prompt: str, repo: Path) -> list[str]:
    """The exact claude-kimi command line for an implementer run."""
    return [
        "claude-kimi", "-p", prompt,
        "--allowedTools", KIMI_ALLOWED_TOOLS,
        "--add-dir", str(repo),
    ]


def changed_files(repo: Path) -> tuple[list[str] | None, str | None]:
    """Read-only `git status --porcelain` in *repo* → (lines, error).

    (list, None) on success — the list may be empty (no edits made).
    (None, reason) when git status itself can't run; the caller treats
    that as a degraded summary, never as an invocation failure.
    """
    try:
        cp = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # git missing, repo dir vanished, status hung past GIT_TIMEOUT, ...
        return None, str(exc)
    if cp.returncode != 0:
        stderr = (cp.stderr or "").strip()
        return None, stderr or f"git status exited {cp.returncode}"
    lines = [ln for ln in (cp.stdout or "").splitlines() if ln.strip()]
    return lines, None


def _print_summary(repo: Path) -> None:
    lines, error = changed_files(repo)
    print("--- changed files (git status --porcelain) ---")
    if error is not None:
        print(f"(summary unavailable: {error})")
    elif lines:
        for ln in lines:
            print(ln)
    else:
        print("(no changes — Kimi made no edits)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ucw-kimi-implement",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "prompt", nargs="?",
        help="task prompt; omit (or pass '-') to read it from stdin",
    )
    parser.add_argument(
        "--repo", default=os.getcwd(),
        help="target repo dir, forwarded as --add-dir (default: cwd)",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="seconds before claude-kimi is killed (default: settings-resolved "
             "— UCW_KIMI_TIMEOUT_SECS / kimi.timeout_secs, else "
             f"{DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args(argv)

    prompt = args.prompt
    if prompt is None or prompt == "-":
        prompt = sys.stdin.read()
    prompt = prompt.strip()
    if not prompt:
        print("error: empty task prompt", file=sys.stderr)
        return 1

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"error: repo dir not found: {repo}", file=sys.stderr)
        return 1

    timeout = args.timeout if args.timeout is not None else resolve_timeout(repo)

    cmd = build_cmd(prompt, repo)
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"error: claude-kimi timed out after {timeout}s",
              file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("error: claude-kimi not found on PATH", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: failed to launch claude-kimi: {exc}", file=sys.stderr)
        return 1

    if cp.returncode != 0:
        stderr = (cp.stderr or "").strip()
        detail = f": {stderr[:500]}" if stderr else ""
        print(f"error: claude-kimi exited {cp.returncode}{detail}",
              file=sys.stderr)
        return 1

    out = (cp.stdout or "").strip()
    if out:
        print(out)
        print()

    _print_summary(repo)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
