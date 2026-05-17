---
description: Bootstrap UCW for the current project — detect stack, ask preferences, populate `.ucw/knowledge/`, initialize memory.
argument-hint: "[--category image|video|svg|audio|diagrams|deploy|...]"
---

Run the **onboarder** subagent.

If `.ucw/knowledge/PREFERENCES.md` already exists, treat this as an update —
only ask for missing or explicitly-requested categories ($ARGUMENTS).

When complete, print:
- Path to populated `.ucw/knowledge/` docs
- Memory DB initialization status
- Any MCP servers registered (Obsidian, Notion, …)
- One-paragraph "here's what I learned about your repo" summary for verification
