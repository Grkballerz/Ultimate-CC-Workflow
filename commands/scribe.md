---
description: Manually refresh `.ucw/knowledge/*` against the current repo state. Normally runs automatically after each Land.
---

Invoke the **scribe** subagent with scope:
- Diff = changes since `.ucw/last-scribe-sha` (or full repo if absent)
- Mode = `PREFERENCES.scribe_mode` (default `auto`)

Print the list of files updated.
