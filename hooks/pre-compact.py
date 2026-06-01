#!/usr/bin/env python3
"""PreCompact hook — write a pre-compaction digest so post-compact retains state.

Before the harness compacts the transcript, we capture key bits that would
otherwise be lost: current phase, open work item, last successful gate run.
These get re-injected on the next SessionStart.

M1 baseline: just persist phase + edit streak + a "pre-compact at <ts>" marker.
Real transcript summarization lands in M3 alongside the distiller.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, state_file


def main() -> int:
    payload = read_payload()
    digest = state_file(payload, "pre-compact-digest.md")
    digest.parent.mkdir(parents=True, exist_ok=True)

    phase_sf = state_file(payload, "phase")
    phase = phase_sf.read_text().strip() if phase_sf.exists() else "(none)"

    streak_sf = state_file(payload, "edit-streak")
    streak = streak_sf.read_text().strip() if streak_sf.exists() else "0"

    try:
        digest.write_text(
            f"# Pre-compact digest\n\n"
            f"- Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"- Phase at compact: {phase}\n"
            f"- Edit streak at compact: {streak}\n"
            f"- Trigger: {payload.get('trigger', 'unknown')}\n",
            encoding="utf-8",
        )
        log(payload, "wrote pre-compact digest")
    except OSError as exc:
        log(payload, f"could not write digest: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
