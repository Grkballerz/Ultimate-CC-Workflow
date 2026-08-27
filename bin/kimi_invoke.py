#!/usr/bin/env python3
"""Shared plumbing for invoking the claude-kimi bridge headlessly.

`claude-kimi -p "<prompt>"` runs Claude Code against Kimi and prints the
answer to stdout. This module wraps that call with:

  - subprocess timeout + bounded retries
  - lenient JSON extraction (last parsable balanced {...} or [...] block in
    stdout, so chatter around the payload doesn't break callers)
  - a raw mode that skips JSON extraction entirely (any non-empty stdout
    is success) for prose answers like reviews and summaries
  - API-error detection: claude-kimi prints auth/quota failures ("Failed
    to authenticate. API Error: 403 ...") to STDOUT and exits 0, so both
    modes screen short error-shaped output before classifying success and
    return ok=False, error="api_error" without retrying
  - a NEVER-raise contract: every failure mode (timeout, nonzero exit,
    malformed output, missing binary) comes back as ok=False with `error` set

Transports — the claude-kimi wrapper authenticates with the Kimi For
Coding API key; the standalone kimi CLI (~/.kimi-code/bin/kimi, on PATH
as `kimi`) authenticates separately via subscription OAuth. When the API
key hits a quota window (403s, headless hangs) the CLI keeps working, so
this module supports both behind a transport chain:

  claude-kimi — current behavior, unchanged: `claude-kimi -p "<prompt>"`
                with optional --allowedTools / --add-dir, model exported
                as KIMI_MODEL.
  kimi-cli    — `kimi -p "<prompt>"` with one --add-dir per entry. NO -m
                is passed: the CLI's model aliases live in its own
                config.toml, and forwarding our kimi-k3 id could break
                it. The CLI prefixes each stdout line with "• ", so
                leading bullets/whitespace are stripped per line before
                JSON extraction / raw return; stderr (session-resume
                noise) is ignored for content. The API-error detector
                applies here too.
  auto        — claude-kimi first; fall back to kimi-cli ONLY when
                (a) allowed_tools is None — the two transports have
                incompatible permission models (kimi CLI has no
                --allowedTools equivalent), so tool-scoped calls like
                the implementer offload must NEVER silently switch
                transports — AND (b) the claude-kimi attempt failed
                with timeout or api_error AND (c) `kimi` is on PATH.
                The fallback attempt gets the same timeout budget.
                There is no fallback from kimi-cli back to claude-kimi.

The transport resolves lazily: UCW_KIMI_TRANSPORT env > kimi.transport
in the project's .ucw/state/settings.json > auto. The result dict
carries a "transport" key naming the transport that produced the final
result (or was last tried).

Timeout and model resolve lazily when not given explicitly:

  timeout — UCW_KIMI_TIMEOUT_SECS env > kimi.timeout_secs in the project's
            .ucw/state/settings.json > 300
  model   — UCW_KIMI_MODEL env > kimi.model in settings.json > kimi-k3;
            the resolved id is exported to the claude-kimi subprocess as
            KIMI_MODEL (the kimi-cli transport never receives it)

The settings file is found with the same upward project-root walk
ucw-settings.py uses (nearest ancestor with a .ucw dir or .git entry).

Auth is the transport binary's problem — no secrets or endpoints live here.

Importable (`from kimi_invoke import kimi_invoke`) or standalone:

  kimi_invoke.py "summarize this repo" --allowed-tools Read,Grep --add-dir /x
  git diff | kimi_invoke.py - --raw       # '-' reads the prompt from stdin
  kimi_invoke.py "classify this" --transport kimi-cli
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_BRACKETS = {"{": "}", "[": "]"}

SETTINGS_RELPATH = Path(".ucw") / "state" / "settings.json"
TIMEOUT_ENV = "UCW_KIMI_TIMEOUT_SECS"
MODEL_ENV = "UCW_KIMI_MODEL"
TRANSPORT_ENV = "UCW_KIMI_TRANSPORT"
DEFAULT_TIMEOUT_SECS = 300
DEFAULT_MODEL = "kimi-k3"
DEFAULT_TRANSPORT = "auto"
TRANSPORTS = ("claude-kimi", "kimi-cli", "auto")

# The standalone kimi CLI prefixes every stdout line with "• " (bullet +
# space). Strip leading whitespace + bullet run(s) per line; lines without
# a bullet are left untouched (their indentation is preserved).
_BULLET_RE = re.compile(r"^\s*(?:•\s?)+")

# claude-kimi prints API/billing failures to STDOUT and exits 0 (observed
# live: "Failed to authenticate. API Error: 403 You've reached your usage
# limit..."). Substrings below mark such output as an error — but only when
# the whole output is short AND the match sits near the start, so a long
# legitimate answer that merely *discusses* rate limits is never flagged.
API_ERROR_PATTERNS = (
    "failed to authenticate",
    "api error: 4",
    "api error: 5",
    "usage limit",
    "rate limit",
    "quota",
    "invalid authentication",
)
API_ERROR_MAX_LEN = 800     # real answers run longer than error lines
API_ERROR_SCAN_WINDOW = 200  # pattern must appear this close to the start


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


def _resolve_transport(transport: str | None,
                       project_root: Path | None = None) -> str:
    """Explicit value > UCW_KIMI_TRANSPORT > settings kimi.transport > auto.

    An explicit caller/CLI value is returned as-is (kimi_invoke validates
    it); unknown env/settings values fall through to the next source, the
    same lenient posture _resolve_timeout takes on malformed env ints.
    """
    if transport:
        return transport
    env_raw = os.environ.get(TRANSPORT_ENV, "").strip()
    if env_raw in TRANSPORTS:
        return env_raw
    stored = _read_settings(project_root).get("kimi.transport")
    if isinstance(stored, str) and stored.strip() in TRANSPORTS:
        return stored.strip()
    return DEFAULT_TRANSPORT


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


def _build_kimi_cli_cmd(prompt: str, add_dirs) -> list[str]:
    """Command line for the standalone kimi CLI transport.

    Deliberately never passes -m: the CLI resolves model aliases from its
    own config.toml, and our claude-kimi id (kimi-k3) is not guaranteed to
    exist there. No --allowedTools equivalent exists either — which is why
    auto mode refuses to fall back for tool-scoped calls.
    """
    cmd = ["kimi", "-p", prompt]
    if add_dirs:
        if isinstance(add_dirs, (str, Path)):
            add_dirs = [add_dirs]
        for d in add_dirs:
            cmd += ["--add-dir", str(d)]
    return cmd


def _strip_bullets(text: str) -> str:
    """Remove the kimi CLI's leading "• " prefix (and variants) per line."""
    return "\n".join(_BULLET_RE.sub("", line) for line in text.splitlines())


def _fail(raw: str, error: str) -> dict:
    return {"ok": False, "data": None, "raw": raw, "error": error}


def _is_api_error(out: str) -> bool:
    """True when stdout looks like an API/billing error line, not an answer.

    Exit-code checks miss these — claude-kimi reports them on stdout with
    exit 0. Two guards keep false positives out: the stripped output must
    be shorter than API_ERROR_MAX_LEN (error lines are terse, answers are
    not), and the pattern must occur within the first API_ERROR_SCAN_WINDOW
    characters (errors lead with the failure; answers that mention "rate
    limit" mid-prose don't).
    """
    text = out.strip()
    if not text or len(text) >= API_ERROR_MAX_LEN:
        return False
    head = text[:API_ERROR_SCAN_WINDOW].lower()
    return any(pattern in head for pattern in API_ERROR_PATTERNS)


def _run_attempts(cmd: list[str], binary: str, timeout: int, env: dict,
                  retries: int, raw: bool, postprocess=None) -> dict:
    """Shared attempt loop: run *cmd*, classify output, never raise upward.

    *postprocess*, when given, rewrites stdout before any classification
    (bullet stripping for the kimi CLI). The API-error screen, raw mode,
    and JSON extraction all see the postprocessed text — and it is what
    lands in the result's ``raw`` field.
    """
    out = ""
    attempts = 1 if raw else max(0, int(retries)) + 1
    for _ in range(attempts):
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return _fail(out, f"{binary} timed out after {timeout}s")
        except FileNotFoundError:
            return _fail(out, f"{binary} not found on PATH")
        except OSError as exc:
            return _fail(out, f"failed to launch {binary}: {exc}")
        out = cp.stdout or ""
        if postprocess is not None:
            out = postprocess(out)
        if cp.returncode != 0:
            stderr = (cp.stderr or "").strip()
            detail = f": {stderr[:500]}" if stderr else ""
            return _fail(out, f"{binary} exited {cp.returncode}{detail}")
        if _is_api_error(out):
            return _fail(out, "api_error")
        if raw:
            if out.strip():
                return {"ok": True, "data": None, "raw": out, "error": None}
            return _fail(out, f"empty {binary} output in raw mode")
        data = extract_json_block(out)
        if data is not None:
            return {"ok": True, "data": data, "raw": out, "error": None}
    return _fail(
        out, f"no parsable JSON in {binary} output after {attempts} attempt(s)",
    )


def _run_claude_kimi(prompt, allowed_tools, add_dirs, timeout, retries,
                     raw, model) -> dict:
    env = dict(os.environ, KIMI_MODEL=str(_resolve_model(model)))
    cmd = _build_cmd(prompt, allowed_tools, add_dirs)
    return _run_attempts(cmd, "claude-kimi", timeout, env, retries, raw)


def _run_kimi_cli(prompt, add_dirs, timeout, retries, raw) -> dict:
    # Plain inherited env — KIMI_MODEL is a claude-kimi contract, the CLI
    # must keep resolving its model from its own config.toml. stderr is
    # session-resume noise and never contributes content.
    cmd = _build_kimi_cli_cmd(prompt, add_dirs)
    return _run_attempts(cmd, "kimi", timeout, dict(os.environ), retries,
                         raw, postprocess=_strip_bullets)


def _should_fall_back(error: str | None) -> bool:
    """True only for the two claude-kimi failures worth retrying elsewhere.

    Timeout (headless hang during a quota window) and api_error (the 403
    quota line) are transport-specific outages — the OAuth-authenticated
    CLI may still work. Everything else (nonzero exit, malformed JSON,
    missing binary) is not evidence the other transport would do better.
    """
    if not error:
        return False
    return error == "api_error" or error.startswith("claude-kimi timed out")


def kimi_invoke(
    prompt: str,
    allowed_tools: list[str] | str | None = None,
    add_dirs: list[str] | str | None = None,
    timeout: int | None = None,
    retries: int = 1,
    raw: bool = False,
    model: str | None = None,
    transport: str | None = None,
) -> dict:
    """Run a Kimi transport with *prompt* and return a result dict — never raises.

    Returns ``{ok: bool, data: object|None, raw: str, error: str|None,
    transport: str}``:

      ok        — True iff the call succeeded AND stdout contained parsable
                  JSON (or, with raw=True, any non-empty stdout)
      data      — the parsed JSON payload (always None in raw mode / on failure)
      raw       — stdout of the last attempt (empty if the process never ran;
                  bullet-stripped on the kimi-cli transport)
      error     — human-readable failure reason (None when ok is True)
      transport — the transport that produced the final result (or was last
                  tried): "claude-kimi" or "kimi-cli"

    transport=None resolves from UCW_KIMI_TRANSPORT env, then kimi.transport
    in the project's .ucw/state/settings.json, then "auto". "auto" tries
    claude-kimi and falls back to kimi-cli only when allowed_tools is None
    (kimi CLI has no --allowedTools — tool-scoped calls never silently
    switch transports), the failure was a timeout or api_error, and the
    kimi binary is on PATH; the fallback gets the same timeout budget.

    timeout=None and model=None resolve from UCW_KIMI_TIMEOUT_SECS /
    UCW_KIMI_MODEL env vars, then the project's .ucw/state/settings.json
    (kimi.timeout_secs / kimi.model), then 300 / kimi-k3. The resolved
    model is exported to the claude-kimi subprocess as KIMI_MODEL (the
    kimi-cli transport never receives it).

    Malformed output is retried up to *retries* extra times (the flaky-LLM
    case). raw=True runs exactly one attempt — there is no malformed output
    to retry, so the budget isn't burned. Timeouts and nonzero exits fail
    immediately — they're deterministic, retrying just burns the budget.
    So do API/billing errors printed to stdout with exit 0 (quota, rate
    limit, auth): both modes screen for them before classifying success
    and fail with error="api_error" — retrying a quota error wastes a call.
    """
    tried = "claude-kimi"
    try:
        transport = _resolve_transport(transport)
        if transport not in TRANSPORTS:
            result = _fail("", f"unknown kimi transport: {transport!r}")
            result["transport"] = str(transport)
            return result
        timeout = _resolve_timeout(timeout)
        if transport == "kimi-cli":
            tried = "kimi-cli"
            result = _run_kimi_cli(prompt, add_dirs, timeout, retries, raw)
            result["transport"] = "kimi-cli"
            return result
        result = _run_claude_kimi(prompt, allowed_tools, add_dirs, timeout,
                                  retries, raw, model)
        result["transport"] = "claude-kimi"
        if (
            transport == "auto"
            and not result["ok"]
            and allowed_tools is None
            and _should_fall_back(result["error"])
            and shutil.which("kimi")
        ):
            tried = "kimi-cli"
            result = _run_kimi_cli(prompt, add_dirs, timeout, retries, raw)
            result["transport"] = "kimi-cli"
        return result
    except Exception as exc:  # broad on purpose — the contract is NEVER raise
        result = _fail("", f"unexpected error invoking {tried}: {exc}")
        result["transport"] = tried
        return result


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
    parser.add_argument("--transport", default=None, choices=TRANSPORTS,
                        help="transport override — beats everything "
                             f"(default: {TRANSPORT_ENV} env > kimi.transport "
                             f"in .ucw/state/settings.json > {DEFAULT_TRANSPORT})")
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
        transport=args.transport,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
