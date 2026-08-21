#!/usr/bin/env python3
"""Shared plumbing for invoking the claude-kimi bridge headlessly.

`claude-kimi -p "<prompt>"` runs Claude Code against Kimi and prints the
answer to stdout. This module wraps that call with:

  - subprocess timeout + bounded retries
  - lenient JSON extraction (last parsable balanced {...} or [...] block in
    stdout, so chatter around the payload doesn't break callers)
  - a raw mode that skips JSON extraction entirely (any non-empty stdout
    is success) for prose answers like reviews and summaries
  - a NEVER-raise contract: every failure mode (timeout, nonzero exit,
    malformed output, missing binary) comes back as ok=False with `error` set

Timeout and model resolve lazily when not given explicitly:

  timeout — UCW_KIMI_TIMEOUT_SECS env > kimi.timeout_secs in the project's
            .ucw/state/settings.json > 300
  model   — UCW_KIMI_MODEL env > kimi.model in settings.json > kimi-k3;
            the resolved id is exported to the subprocess as KIMI_MODEL

The settings file is found with the same upward project-root walk
ucw-settings.py uses (nearest ancestor with a .ucw dir or .git entry).

Auth is claude-kimi's problem — no secrets or endpoints live here.

Importable (`from kimi_invoke import kimi_invoke`) or standalone:

  kimi_invoke.py "summarize this repo" --allowed-tools Read,Grep --add-dir /x
  git diff | kimi_invoke.py - --raw       # '-' reads the prompt from stdin
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_BRACKETS = {"{": "}", "[": "]"}

SETTINGS_RELPATH = Path(".ucw") / "state" / "settings.json"
TIMEOUT_ENV = "UCW_KIMI_TIMEOUT_SECS"
MODEL_ENV = "UCW_KIMI_MODEL"
DEFAULT_TIMEOUT_SECS = 300
DEFAULT_MODEL = "kimi-k3"


# ---- lenient JSON extraction -------------------------------------------------

def _match_balanced(text: str, start: int) -> int | None:
    """Index of the bracket closing the block opened at *start*, or None.

    String-aware: brackets inside JSON strings (and escaped quotes) don't
    move the depth counter.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json_block(text: str) -> object | None:
    """Last parsable balanced ``{...}`` or ``[...]`` block in *text*, or None.

    Chatty models echo example objects before the real payload, so the LAST
    top-level parsable block wins. Once a block parses, scanning resumes
    after its closing bracket — nested sub-objects of a valid payload are
    never returned on their own. Malformed blocks degrade gracefully: their
    openers are skipped one character at a time so an earlier or later valid
    payload still surfaces. json.loads is the final arbiter.
    """
    found: object | None = None
    i = 0
    while i < len(text):
        if text[i] not in _BRACKETS:
            i += 1
            continue
        end = _match_balanced(text, i)
        if end is None:
            i += 1
            continue
        try:
            found = json.loads(text[i:end + 1])
        except json.JSONDecodeError:
            i += 1
            continue
        i = end + 1
    return found


# ---- settings resolution -----------------------------------------------------

def _project_root(start: Path | None = None) -> Path:
    """Nearest ancestor (cwd included) with a .ucw dir or .git entry.

    Mirrors the walk in ucw-settings.py — kept inline to stay
    dependency-light (that file's hyphenated name defeats plain import).
    """
    cwd = start or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir() or (parent / ".git").exists():
            return parent
    return cwd


def _read_settings(project_root: Path | None = None) -> dict:
    sp = (project_root or _project_root()) / SETTINGS_RELPATH
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_timeout(timeout: int | None,
                     project_root: Path | None = None) -> int:
    """Explicit value > UCW_KIMI_TIMEOUT_SECS > settings kimi.timeout_secs > 300."""
    if timeout is not None:
        return timeout
    env_raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if env_raw:
        try:
            return int(env_raw)
        except ValueError:
            pass  # invalid env value → fall through, same as ucw-settings
    stored = _read_settings(project_root).get("kimi.timeout_secs")
    if isinstance(stored, int) and not isinstance(stored, bool):
        return stored
    return DEFAULT_TIMEOUT_SECS


def _resolve_model(model: str | None,
                   project_root: Path | None = None) -> str:
    """Explicit value > UCW_KIMI_MODEL > settings kimi.model > kimi-k3."""
    if model:
        return model
    env_raw = os.environ.get(MODEL_ENV, "").strip()
    if env_raw:
        return env_raw
    stored = _read_settings(project_root).get("kimi.model")
    if isinstance(stored, str) and stored.strip():
        return stored
    return DEFAULT_MODEL


# ---- invocation --------------------------------------------------------------

def _build_cmd(prompt: str, allowed_tools, add_dirs) -> list[str]:
    cmd = ["claude-kimi", "-p", prompt]
    if allowed_tools:
        if isinstance(allowed_tools, str):
            cmd += ["--allowedTools", allowed_tools]
        else:
            cmd += ["--allowedTools", ",".join(str(t) for t in allowed_tools)]
    if add_dirs:
        if isinstance(add_dirs, (str, Path)):
            add_dirs = [add_dirs]
        for d in add_dirs:
            cmd += ["--add-dir", str(d)]
    return cmd


def _fail(raw: str, error: str) -> dict:
    return {"ok": False, "data": None, "raw": raw, "error": error}


def kimi_invoke(
    prompt: str,
    allowed_tools: list[str] | str | None = None,
    add_dirs: list[str] | str | None = None,
    timeout: int | None = None,
    retries: int = 1,
    raw: bool = False,
    model: str | None = None,
) -> dict:
    """Run claude-kimi with *prompt* and return a result dict — never raises.

    Returns ``{ok: bool, data: object|None, raw: str, error: str|None}``:

      ok    — True iff the call succeeded AND stdout contained parsable JSON
              (or, with raw=True, any non-empty stdout)
      data  — the parsed JSON payload (always None in raw mode / on failure)
      raw   — stdout of the last attempt (empty if the process never ran)
      error — human-readable failure reason (None when ok is True)

    timeout=None and model=None resolve from UCW_KIMI_TIMEOUT_SECS /
    UCW_KIMI_MODEL env vars, then the project's .ucw/state/settings.json
    (kimi.timeout_secs / kimi.model), then 300 / kimi-k3. The resolved
    model is exported to the subprocess as KIMI_MODEL.

    Malformed output is retried up to *retries* extra times (the flaky-LLM
    case). raw=True runs exactly one attempt — there is no malformed output
    to retry, so the budget isn't burned. Timeouts and nonzero exits fail
    immediately — they're deterministic, retrying just burns the budget.
    """
    out = ""
    try:
        timeout = _resolve_timeout(timeout)
        env = dict(os.environ, KIMI_MODEL=str(_resolve_model(model)))
        cmd = _build_cmd(prompt, allowed_tools, add_dirs)
        attempts = 1 if raw else max(0, int(retries)) + 1
        for _ in range(attempts):
            try:
                cp = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return _fail(out, f"claude-kimi timed out after {timeout}s")
            except FileNotFoundError:
                return _fail(out, "claude-kimi not found on PATH")
            except OSError as exc:
                return _fail(out, f"failed to launch claude-kimi: {exc}")
            out = cp.stdout or ""
            if cp.returncode != 0:
                stderr = (cp.stderr or "").strip()
                detail = f": {stderr[:500]}" if stderr else ""
                return _fail(out, f"claude-kimi exited {cp.returncode}{detail}")
            if raw:
                if out.strip():
                    return {"ok": True, "data": None, "raw": out, "error": None}
                return _fail(out, "empty claude-kimi output in raw mode")
            data = extract_json_block(out)
            if data is not None:
                return {"ok": True, "data": data, "raw": out, "error": None}
        return _fail(
            out, f"no parsable JSON in claude-kimi output after {attempts} attempt(s)",
        )
    except Exception as exc:  # broad on purpose — the contract is NEVER raise
        return _fail(out, f"unexpected error invoking claude-kimi: {exc}")


# ---- standalone entry point --------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kimi_invoke", description=__doc__)
    parser.add_argument("prompt",
                        help="prompt passed to claude-kimi -p, or '-' to read "
                             "the prompt from stdin (for piped diffs etc.)")
    parser.add_argument("--allowed-tools",
                        help="comma-separated allowlist forwarded as --allowedTools")
    parser.add_argument("--add-dir", action="append", default=[], dest="add_dirs",
                        metavar="DIR", help="extra dir(s) forwarded as --add-dir")
    parser.add_argument("--timeout", type=int, default=None,
                        help="seconds before the subprocess is killed (default: "
                             f"{TIMEOUT_ENV} env > kimi.timeout_secs in "
                             f".ucw/state/settings.json > {DEFAULT_TIMEOUT_SECS})")
    parser.add_argument("--retries", type=int, default=1,
                        help="extra attempts on malformed output (default: 1; "
                             "ignored with --raw)")
    parser.add_argument("--raw", action="store_true",
                        help="skip JSON extraction — any non-empty stdout is "
                             "success, no retries")
    parser.add_argument("--model", default=None,
                        help="model id exported to claude-kimi as KIMI_MODEL "
                             f"(default: {MODEL_ENV} env > kimi.model in "
                             f".ucw/state/settings.json > {DEFAULT_MODEL})")
    args = parser.parse_args(argv)
    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    result = kimi_invoke(
        prompt,
        allowed_tools=args.allowed_tools,
        add_dirs=args.add_dirs,
        timeout=args.timeout,
        retries=args.retries,
        raw=args.raw,
        model=args.model,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
