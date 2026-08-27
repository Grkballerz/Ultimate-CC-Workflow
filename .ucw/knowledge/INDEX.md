# Knowledge Index

> Auto-maintained by the UCW scribe. Always injected at SessionStart.
> One line per doc: title + 1-sentence hook.

- **STACK.md** — Python 3.11+
- **DESIGN.md** — Claude Code phased-workflow plugin; findings store, kimi_invoke bridge, settings registry are the key abstractions; gates fail loudly under strict conditions, review lanes emit receipts checked by --expect-lanes
- **CONVENTIONS.md** — ruff, ruff; external-model bridges are never-raise/graceful-degradation, tested with mocked subprocess only; setup-skip vs strict-failure gate convention; dispatch table in commands/ucw.md is the source of truth for tests
- **GLOSSARY.md** — _empty — scribe will fill in_
- **ROADMAP.md** — Kimi transport fallback (claude-kimi → standalone kimi CLI) landed 2026-08-27 (5feacb5); audit-fixes wave landed 2026-08-23 (af5c5e1)
- **PREFERENCES.md** — image_gen_tool=none, video_gen_tool=none, svg_tool=none, audio_tool=none, diagram_tool=Mermaid, deploy_target=none, database=none, package_manager=pip, docs_surface=plain, runtime=python3, linter=ruff, formatter=ruff, test_runner=pytest, typechecker=none, browser_automation=none, embedding_provider=none, scribe_mode=auto, notification_channel=none

_Last refreshed: 2026-08-27_
