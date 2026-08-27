# Roadmap

> Done / WIP / Planned. The scribe ticks items after every Land phase.

## Done
<!-- scribe-done-start -->
### Kimi K3 second-opinion integration (landed 2026-08-21, da9948f)
- [x] bin/kimi_invoke.py — subprocess wrapper around `claude-kimi -p` (JSON parse, 1 retry, 90s timeout, {ok,data,raw,error}, never raises)
- [x] tests/test_kimi_invoke.py — mocked subprocess: timeout, malformed JSON, happy path
- [x] bin/ucw-kimi-opinion.py — diff→Kimi review prompt→Finding dicts→ucw-review.py add-finding (finder_agent=kimi-second-opinion, finder_model=kimi-k3)
- [x] tests/test_kimi_review_lane.py — malformed→0 findings+warning; well-formed→correct add-finding args
- [x] commands/ucw.md review section — document --with-kimi as 10th parallel lane, default off
- [x] tests/test_review_agents.py — extend: --with-kimi documented; existing assertions unchanged
- [x] commands/ucw.md — new `opinion <question|--diff>` section, advisory banner, read-only
- [x] tests/test_opinion_command.py — dispatch-table + advisory-text assertions
- [x] bin/ucw-kimi-disprove.py — disprover prompt via kimi_invoke → ucw-review.py disprove --agent kimi-disprover --model kimi-k3
- [x] commands/ucw.md — opt-in --disprover-model kimi flag; default haiku unchanged
- [x] tests/test_review_agents.py — extend: disprover.md model still haiku; flag documented opt-in
- [x] bin/ucw-kimi-implement.py — claude-kimi -p with --allowedTools Read,Edit,Write,Grep,Glob (NO Bash), never commits
- [x] agents/implementer.md + commands/ucw.md plan section — [kimi] task tag rules + mandatory verify gate
- [x] tests/test_kimi_implementer_offload.py — docs mention tag+verify rule; allowed-tools excludes Bash
- [x] make validate green + CHANGELOG.md entry
- [x] bin/ucw-settings.py — list/get/set/unset over .ucw/state/settings.json; typed registry (kimi.review, kimi.disprover, kimi.offload, kimi.model, kimi.timeout_secs, review.default, ship.push, ship.pr, scribe.auto); precedence env UCW_<KEY> > project > default; + tests/test_settings_cli.py
- [x] commands/ucw.md settings section + dispatch row; kimi flags consult settings when absent; [kimi] tags honored only when kimi.offload=true (auto-mode kill-switch); + tests/test_settings_command.py

### Audit-fixes wave (landed 2026-08-23, af5c5e1)
- [x] A. bin/ucw-review.py: tighten CONCERN_GLOBS to code extensions; slim scope --persist output; add lane-done receipts + gate --expect-lanes + pending-disprove counts (QW1+WP2 core)
- [x] B. bin/ucw-verify.py + hooks/stop.py + Makefile + install.sh: venv/.bin tool probing, strict mode (setup-skip=fail in verify/land phase or auto>=2), loud skips, provision pytest+ruff, verify-report.json + last-verify.json cache (WP1+WP4 core)
- [x] C. bin/ucw-auto.py + bin/ucw-settings.py + hooks/_hook_common.py + dashboard/cli.py: auto.default_level + auto.retry_cap keys, status shows auto mode + review gate + distill zero-yield warning, fix /scribe hint (QW3+WP3 status)
- [x] D. memory/ucw_memory/retrieval.py + distill.py + hooks/user-prompt-submit.py: pin quota in recall, pinned-fact injection, distill regex resurrection + confidence fix (QW4+WP3)
- [x] E. bin/kimi_invoke.py: API-error-pattern detection in raw mode (quota 403 exits 0 bug)
- [x] F. commands/ucw.md + agents/*.md + README + drift test: pipeline reorder, ship file-deliverables + parallel gatekeepers + skip-redundant-review, nudge/recovery ladder, lane receipts docs, README sync + parametrized dispatch-drift test (QW1/2/5/6 + WP2/4 docs)
- [x] G. Full validate + CHANGELOG + per-fix spot verification

### Kimi transport fallback (landed 2026-08-27, 5feacb5)
- [x] bin/kimi_invoke.py — `kimi.transport` setting (auto | claude-kimi | kimi-cli); auto falls back from claude-kimi to the standalone kimi CLI on timeout/api_error, tool-less calls only, no fallback in reverse
<!-- scribe-done-end -->

## In progress
<!-- scribe-wip-start -->
<!-- scribe-wip-end -->

## Planned
<!-- scribe-planned-start -->
<!-- scribe-planned-end -->
