# Security Policy

UCW runs hooks, executes the memory MCP server, and writes files in your
home directory. We take its security posture seriously.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems. Instead:

1. Email the maintainers (see `.claude-plugin/plugin.json` for contact).
2. Include reproduction steps, expected vs. actual behavior, and the affected
   UCW version.
3. We aim to acknowledge within 72 hours and have a fix or mitigation within
   2 weeks for high-severity issues.

## Built-in safeguards

UCW ships with a deterministic security scanner you can run any time:

```bash
python3 bin/ucw-audit.py --repo .
```

It scans for:

- **14 secret families** — AWS, GitHub (token / OAuth / PAT), Anthropic,
  OpenAI, Voyage, Slack, Stripe, Notion, JWTs, private keys, Google API keys
- **5 shell-injection patterns** — `eval $VAR`, `bash -c $VAR`, `curl | sh`,
  `rm -rf $VAR`, and similar
- **MCP server misconfiguration** — servers that invoke a shell via `-c`
- **Agent permission scopes** — reviewer/auditor/verifier agents with write
  tools

Exit codes: `0` clean, `2` critical findings (CI gate), `3` major findings.

## Hook safety

Hooks are user-defined commands that fire on every Claude Code lifecycle
event. UCW hooks:

- Are committed to this repo and reviewed in normal PRs.
- Never `eval` or shell-interpolate untrusted input.
- Wrap risky operations in try/except and log to `.ucw/hooks.log` instead of
  raising — a crashing hook should never block the user.
- Pre-tool-use hooks **deny** clearly destructive patterns (`rm -rf /`,
  `git push --force` to main, `--no-verify`, fork bombs, raw block-device
  writes) with a clear reason.

If you suspect a hook is misbehaving, set `UCW_VERBOSE=1` and re-run the
operation; hook scripts will append details to `.ucw/hooks.log`.

## Memory privacy

The distiller strips three tag families before writing anything to disk:

- `<secret>…</secret>` — never persisted, scrubbed even from logs
- `<private>…</private>` — kept in-session only
- `<no-memory>…</no-memory>` — synonym for `<private>`

Privacy stripping is applied in `memory/ucw_memory/privacy.py` and runs *before*
the heuristic extractor sees the text. If you find a path where a privacy tag
is bypassed, file a security report.

## What UCW does NOT do

- It does not call out to any third-party service unless you opted in:
  - Voyage embeddings only if `VOYAGE_API_KEY` is set.
  - Claude Haiku reranking only if `ANTHROPIC_API_KEY` is set (and you
    explicitly opted into the reranker via PREFERENCES).
- It does not exfiltrate your code or facts. Memory is local SQLite.
- It does not modify `~/.claude/settings.json` without a backup
  (`*.ucw.bak.<timestamp>`).
- It does not run as root or escalate privileges.

## Defense-in-depth recommendations

- Run `python3 bin/ucw-audit.py --repo . --strict` in CI to catch any drift.
- Keep `~/.claude/mcp.json` reviewed — every MCP server can run code.
- Don't commit transcripts or `.ucw/memory.sqlite` to git.
