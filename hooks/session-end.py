#!/usr/bin/env python3
"""SessionEnd hook — final memory flush, work-item close, daily digest.

M1 baseline:
- Drains `.ucw/state/distill-queue` (just removes entries; M3+ will hand them
  to the distiller for real fact extraction).
- Appends a one-line entry to `.ucw/sessions.log` for the dashboard.
- Resets the edit streak.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file, ucw_dir  # noqa: E402


def _drain_distill_queue(payload: dict) -> int:
    """Returns count of pending distill jobs (currently we just clear them)."""
    queue = state_file(payload, "distill-queue")
    if not queue.exists():
        return 0
    try:
        lines = [ln for ln in queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
        queue.write_text("")
        return len(lines)
    except OSError:
        return 0


def _reset_state(payload: dict) -> None:
    for name in ("edit-streak", "phase"):
        sf = state_file(payload, name)
        if sf.exists():
            try:
                sf.unlink()
            except OSError:
                pass


def _append_session_log(payload: dict, distilled: int) -> None:
    d = ucw_dir(payload)
    if not d.exists():
        return
    log_path = d / "sessions.log"
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            session = payload.get("session_id", "?")
            reason = payload.get("end_reason", payload.get("source", "?"))
            fh.write(f"{int(time.time())}\t{session}\t{reason}\tdistill_pending={distilled}\n")
    except OSError:
        pass


def main() -> int:
    payload = read_payload()
    distilled = _drain_distill_queue(payload)
    _append_session_log(payload, distilled)
    _reset_state(payload)
    log(payload, f"session ended; distill queue drained ({distilled} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
