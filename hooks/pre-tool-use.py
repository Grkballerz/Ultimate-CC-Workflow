#!/usr/bin/env python3
"""PreToolUse hook (Bash matcher) — block obviously destructive commands.

Reads stdin (JSON from Claude Code), writes a permission decision JSON to
stdout. Exit code 0 always; the decision goes through hookSpecificOutput.

This is the M1 stub. Patterns are minimal — M4 will expand the rule set and
make them user-tunable via PREFERENCES.
"""
from __future__ import annotations

import json
import re
import sys

# Each pattern: (regex, human reason). Order matters — first match wins.
DENY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-rf\s+/(\s|$)"),                "rm -rf / blocked by UCW policy"),
    (re.compile(r"\brm\s+-rf\s+~(\s|/|$)"),              "rm -rf ~ blocked by UCW policy"),
    (re.compile(r"\bgit\s+push\s+.*--force.*\b(main|master)\b"),
                                                          "force-push to main/master blocked — confirm with user first"),
    (re.compile(r"\bgit\s+commit\b.*--no-verify"),       "--no-verify on commit blocked — fix the hook failure instead"),
    (re.compile(r":\(\)\s*\{[^}]*:\|:&[^}]*\};\s*:"),    "fork bomb blocked"),
    (re.compile(r"\bdd\s+if=/dev/(zero|random|urandom).*of=/dev/(sd|nvme)"),
                                                          "raw block device write blocked"),
    (re.compile(r"\bmkfs\."),                            "mkfs blocked"),
    (re.compile(r"\b>\s*/dev/sd[a-z]"),                  "raw block device redirect blocked"),
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    command = (payload.get("tool_input", {}) or {}).get("command", "")
    if not isinstance(command, str):
        return 0

    for pattern, reason in DENY_PATTERNS:
        if pattern.search(command):
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                },
                sys.stdout,
            )
            return 0

    # No match — say nothing, let normal flow proceed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
