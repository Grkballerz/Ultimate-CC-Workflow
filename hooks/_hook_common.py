"""Shared utilities for UCW hook scripts.

All hooks share: stdin JSON parsing, project-root resolution, structured stdout
output, and best-effort logging to `.ucw/hooks.log` for debugging.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


def read_payload() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def write_output(output: dict[str, Any]) -> None:
    json.dump(output, sys.stdout)


def project_root(payload: dict[str, Any]) -> Path:
    cwd = Path(payload.get("cwd", os.getcwd()))
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists() or (parent / ".ucw").is_dir():
            return parent
    return cwd


def ucw_dir(payload: dict[str, Any]) -> Path:
    return project_root(payload) / ".ucw"


def log(payload: dict[str, Any], message: str) -> None:
    """Best-effort log line to .ucw/hooks.log. Never throws."""
    try:
        d = ucw_dir(payload)
        d.mkdir(parents=True, exist_ok=True)
        log_file = d / "hooks.log"
        with log_file.open("a", encoding="utf-8") as fh:
            event = payload.get("hook_event_name", "?")
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event}: {message}\n")
    except OSError:
        pass


def state_file(payload: dict[str, Any], name: str) -> Path:
    """Path under .ucw/state/. Caller is responsible for mkdir if writing."""
    return ucw_dir(payload) / "state" / name


# ---- auto-mode helpers ----------------------------------------------------
# Inlined rather than imported from bin/ucw-auto.py because (a) bin/ is a
# CLI shipping with a hyphen in the name (not importable) and (b) hooks
# should stay self-contained for testability. Schema must match the file
# bin/ucw-auto.py writes; tests in test_auto_mode_integration.py guard
# against drift.

_VALID_AUTO_LEVELS = (1, 2, 3, 4)


def auto_mode_level(payload: dict[str, Any]) -> int:
    """Return the current auto-mode level (0 = off).

    Resolution order: UCW_AUTO_MODE env → .ucw/state/auto-mode → 0.
    """
    env = os.environ.get("UCW_AUTO_MODE", "").strip().lower()
    if env in {"0", "off", "no", "false"}:
        return 0
    if env.isdigit() and int(env) in _VALID_AUTO_LEVELS:
        return int(env)
    if env == "on":
        return 4
    sf = state_file(payload, "auto-mode")
    if not sf.exists():
        return 0
    try:
        import json
        data = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    level = data.get("level")
    if isinstance(level, int) and level in _VALID_AUTO_LEVELS:
        return level
    return 0


def auto_retry_cap(payload: dict[str, Any]) -> int:
    """Return the retry cap (default 3). Used by Stop hook at level >= 2.

    Resolution order: UCW_AUTO_RETRY_CAP env → `retry_cap` in
    .ucw/state/auto-mode (`on --retry-cap`) → `auto.retry_cap` in
    .ucw/state/settings.json → 3. Must stay in agreement with
    bin/ucw-auto.py current_retry_cap().
    """
    env = os.environ.get("UCW_AUTO_RETRY_CAP", "").strip()
    if env.isdigit():
        cap = int(env)
        if 0 < cap < 100:
            return cap
    sf = state_file(payload, "auto-mode")
    if sf.exists():
        try:
            import json
            data = json.loads(sf.read_text(encoding="utf-8"))
            cap = data.get("retry_cap")
            if isinstance(cap, int) and 0 < cap < 100:
                return cap
        except (OSError, ValueError):
            pass
    settings = state_file(payload, "settings.json")
    if settings.exists():
        try:
            import json
            data = json.loads(settings.read_text(encoding="utf-8"))
            cap = data.get("auto.retry_cap") if isinstance(data, dict) else None
            if isinstance(cap, int) and not isinstance(cap, bool) and 0 < cap < 100:
                return cap
        except (OSError, ValueError):
            pass
    return 3


# ---- no-progress circuit breaker ------------------------------------------
# Both stop.py and post-tool-batch.py block when a verify gate fails. Without
# a release valve, a failure the agent CAN'T fix (e.g. a pre-existing lint
# error in a file the diff never touched, or a linter-version mismatch) traps
# the agent forever: it can't end its turn, and each fix attempt is itself an
# edit that re-arms the streak. The breaker detects "no progress" — the same
# failure, byte-for-byte (modulo whitespace), N times in a row — and releases
# so the agent can stop and hand the stuck failure to a human. Any change in
# the failure output resets the counter, so a genuinely-progressing fix never
# trips it early.

_SIG_FILE = "verify-fail-sig.json"


def break_after() -> int:
    """How many identical consecutive failures before the breaker releases.

    Default 3 (block twice, release on the third). Env override:
    UCW_VERIFY_BREAK_AFTER. Values < 1 are ignored.
    """
    env = os.environ.get("UCW_VERIFY_BREAK_AFTER", "").strip()
    if env.isdigit() and int(env) >= 1:
        return int(env)
    return 3


def failure_signature(result: dict[str, Any]) -> str:
    """Stable fingerprint of a verify failure: (failed_gate, normalized summary).

    Only whitespace is normalized — line numbers and messages are kept, since a
    change there means the agent made progress and the counter should reset.
    """
    gate = result.get("failed_gate") or "verify"
    summary = re.sub(r"\s+", " ", (result.get("summary") or "")).strip()
    raw = f"{gate}\n{summary[-1500:]}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _read_sig_state(payload: dict[str, Any]) -> dict[str, Any]:
    sf = state_file(payload, _SIG_FILE)
    if not sf.exists():
        return {}
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_sig_state(payload: dict[str, Any], data: dict[str, Any]) -> None:
    sf = state_file(payload, _SIG_FILE)
    sf.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (temp + replace) so a concurrent stop/batch write can't
    # observe or clobber a half-written file and lose a counter increment.
    tmp = sf.with_name(sf.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, sf)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def record_failure_signature(payload: dict[str, Any], context: str, sig: str) -> int:
    """Bump (or start) the consecutive-failure counter for `context`+`sig`.

    Returns the new count (>= 1). A different sig resets the count to 1.
    `context` separates callers that run different gate sets ("stop" vs "batch")
    so their signatures don't collide.
    """
    data = _read_sig_state(payload)
    entry = data.get(context) or {}
    count = int(entry.get("count", 0)) + 1 if entry.get("sig") == sig else 1
    data[context] = {"sig": sig, "count": count}
    _write_sig_state(payload, data)
    return count


def reset_failure_signature(payload: dict[str, Any], context: str) -> None:
    """Clear the counter for `context` (called on pass/skip/release)."""
    data = _read_sig_state(payload)
    if context in data:
        del data[context]
        _write_sig_state(payload, data)


def write_stuck_verify(
    payload: dict[str, Any], context: str, result: dict[str, Any], count: int
) -> None:
    """Record a released-but-still-failing gate to .ucw/state/stuck-verify.md
    so the human sees what the breaker gave up on."""
    sf = state_file(payload, "stuck-verify.md")
    sf.parent.mkdir(parents=True, exist_ok=True)
    gate = result.get("failed_gate") or "verify"
    cmd = result.get("command") or "<no command>"
    summary = (result.get("summary") or "").strip()[-1500:] or "(no output captured)"
    body = (
        f"# Stuck verify gate ({context})\n\n"
        f"The `{gate}` gate (`{cmd}`) failed **identically {count} times in a "
        f"row** with no change in output. UCW's circuit breaker released the "
        f"Stop block so the agent could hand it off rather than loop forever.\n\n"
        f"This is usually a **pre-existing failure** the current change didn't "
        f"cause (or can't fix): a linter-version mismatch, a failure in an "
        f"untouched file, or an out-of-scope issue. Review it, then either fix "
        f"it or scope it out:\n\n"
        f"- Drop a gate for this session: `UCW_VERIFY_GATES=tests` (or `lint,types`)\n"
        f"- Skip auto-verify entirely: `UCW_SKIP_AUTO_VERIFY=1`\n"
        f"- Raise the breaker threshold: `UCW_VERIFY_BREAK_AFTER=N`\n\n"
        f"## Last failure output\n\n```\n{summary}\n```\n"
    )
    try:
        sf.write_text(body, encoding="utf-8")
    except OSError:
        pass
