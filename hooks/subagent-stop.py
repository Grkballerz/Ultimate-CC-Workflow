#!/usr/bin/env python3
"""SubagentStop hook — record what a subagent decided so the parent can recall it.

For now we just log; M3+ will route important findings into memory via the
distiller, scoped to the parent session.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_common import log, read_payload, ucw_dir  # noqa: E402


def main() -> int:
    payload = read_payload()
    d = ucw_dir(payload)
    if not d.exists():
        return 0
    try:
        log_path = d / "subagents.log"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{int(time.time())}\t"
                f"{payload.get('agent_type', '?')}\t"
                f"{payload.get('agent_id', '?')}\n"
            )
        log(payload, f"subagent {payload.get('agent_type', '?')} stopped")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
