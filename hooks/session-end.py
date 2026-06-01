#!/usr/bin/env python3
"""SessionEnd hook — final memory flush, work-item close, daily digest.

- Drains `.ucw/state/distill-queue`, running the distiller for each pending
  transcript and writing facts into the project memory DB.
- Appends a one-line entry to `.ucw/sessions.log` for the dashboard.
- Resets the edit streak and phase state.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file, ucw_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "memory"))
sys.path.insert(0, str(Path.home() / ".claude" / "ucw" / "lib"))  # alt install location

try:
    from ucw_memory import (  # type: ignore[import-not-found]
        MemoryDB,
        extract_from_transcript,
        write_candidates_to_db,
    )
except ImportError:
    MemoryDB = None  # type: ignore[assignment,misc]


def _drain_distill_queue(payload: dict) -> tuple[int, int]:
    """Process each queued distill job. Returns (queue_size, facts_written)."""
    queue = state_file(payload, "distill-queue")
    if not queue.exists():
        return 0, 0
    try:
        lines = [ln for ln in queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return 0, 0

    if MemoryDB is None:
        # ucw_memory not importable; just clear the queue.
        try:
            queue.write_text("")
        except OSError:
            pass
        return len(lines), 0

    facts_written = 0
    db_path = ucw_dir(payload) / "memory.sqlite"
    try:
        with MemoryDB(db_path) as db:
            for line in lines:
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                _ts, session, transcript = parts[0], parts[1], parts[2]
                if not transcript or not Path(transcript).exists():
                    continue
                candidates = extract_from_transcript(Path(transcript))
                ids = write_candidates_to_db(
                    candidates, db, scope="project", source_session=session or None
                )
                facts_written += len(ids)
    except Exception as exc:  # broad: never let the hook crash
        log(payload, f"distill failed: {exc}")
    try:
        queue.write_text("")
    except OSError:
        pass
    return len(lines), facts_written


def _reset_state(payload: dict) -> None:
    for name in ("edit-streak", "phase"):
        sf = state_file(payload, name)
        if sf.exists():
            try:
                sf.unlink()
            except OSError:
                pass


def _append_session_log(payload: dict, jobs: int, facts: int) -> None:
    d = ucw_dir(payload)
    if not d.exists():
        return
    log_path = d / "sessions.log"
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            session = payload.get("session_id", "?")
            reason = payload.get("end_reason", payload.get("source", "?"))
            fh.write(
                f"{int(time.time())}\t{session}\t{reason}\t"
                f"distill_jobs={jobs}\tfacts_written={facts}\n"
            )
    except OSError:
        pass


def main() -> int:
    payload = read_payload()
    jobs, facts = _drain_distill_queue(payload)
    _append_session_log(payload, jobs, facts)
    _reset_state(payload)
    log(payload, f"session ended; processed {jobs} distill jobs, wrote {facts} facts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
