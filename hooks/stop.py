#!/usr/bin/env python3
"""Stop hook — fires when Claude finishes a turn.

Two jobs:
1. **Gate enforcement**: if `.ucw/state/phase` == "build" and the streak is
   above zero, block Stop until tests have been run.
2. **Memory distillation**: kick off the distiller against the latest
   transcript so durable facts get persisted. The actual distill is
   asynchronous-friendly — we just append a job marker; SessionEnd or the
   memory-curator picks it up.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file, write_output


def _current_phase(payload: dict) -> str | None:
    sf = state_file(payload, "phase")
    if not sf.exists():
        return None
    try:
        return sf.read_text().strip() or None
    except OSError:
        return None


def _edit_streak(payload: dict) -> int:
    sf = state_file(payload, "edit-streak")
    if not sf.exists():
        return 0
    try:
        return int(sf.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _queue_distill(payload: dict) -> None:
    """Append a marker file the distiller picks up on next SessionEnd."""
    queue = state_file(payload, "distill-queue")
    queue.parent.mkdir(parents=True, exist_ok=True)
    transcript = payload.get("transcript_path", "")
    session = payload.get("session_id", "")
    try:
        with queue.open("a", encoding="utf-8") as fh:
            fh.write(f"{int(time.time())}\t{session}\t{transcript}\n")
    except OSError:
        pass


def main() -> int:
    payload = read_payload()
    phase = _current_phase(payload)
    streak = _edit_streak(payload)

    _queue_distill(payload)

    if phase == "build" and streak > 0:
        log(payload, f"Stop blocked: phase=build, dirty edits = {streak}")
        # Claude Code's strict hook schema accepts hookSpecificOutput only for
        # PreToolUse / UserPromptSubmit / PostToolUse / PostToolBatch. Stop
        # gets decision + reason at the top level only.
        write_output({
            "decision": "block",
            "reason": (
                f"UCW: cannot Stop in Build phase with {streak} edits not verified. "
                f"Run the verifier (or `/ucw ship`) — the Verify phase gate must pass before "
                f"the session can end. Tip: `/ucw ship` runs verify + lands the change."
            ),
        })
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
