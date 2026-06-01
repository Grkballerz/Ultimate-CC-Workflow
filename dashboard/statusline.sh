#!/usr/bin/env bash
# UCW status line for Claude Code.
#
# Configure in ~/.claude/settings.json:
#   {"statusLine": {"type": "command",
#                   "command": "$HOME/.claude/ucw/bin/statusline.sh"}}
#
# Output format (single line, kept under 80 chars):
#   ucw │ phase: build │ streak: 3 │ mem: 42 (3 pinned) │ stale: 1
#
# Designed to be fast (<100ms): reads files only, no DB query.
set -euo pipefail

# Find .ucw walking up from $CLAUDE_PROJECT_DIR (Claude Code sets this) or $PWD.
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
UCW=""
while [[ "$ROOT" != "/" ]]; do
  if [[ -d "$ROOT/.ucw" ]]; then
    UCW="$ROOT/.ucw"
    break
  fi
  ROOT="$(dirname "$ROOT")"
done

if [[ -z "$UCW" ]]; then
  echo "ucw │ no .ucw/ (run /ucw init)"
  exit 0
fi

# Phase
phase="$(cat "$UCW/state/phase" 2>/dev/null || echo "—")"
phase="${phase%$'\n'}"
[[ -z "$phase" ]] && phase="—"

# Edit streak
streak="$(cat "$UCW/state/edit-streak" 2>/dev/null || echo "0")"
streak="${streak%$'\n'}"

# Memory counts via sqlite3 (if available)
mem_total="?"
mem_pinned="?"
if command -v sqlite3 >/dev/null 2>&1 && [[ -f "$UCW/memory.sqlite" ]]; then
  mem_total="$(sqlite3 "$UCW/memory.sqlite" 'SELECT COUNT(*) FROM facts WHERE deleted=0' 2>/dev/null || echo "?")"
  mem_pinned="$(sqlite3 "$UCW/memory.sqlite" 'SELECT COUNT(*) FROM facts WHERE deleted=0 AND pinned=1' 2>/dev/null || echo "?")"
fi

# Stale-doc count (cheap mtime check, no python call)
stale=0
if [[ -d "$UCW/knowledge" ]]; then
  for doc in STACK.md DESIGN.md CONVENTIONS.md; do
    [[ -f "$UCW/knowledge/$doc" ]] || continue
    doc_mtime=$(stat -c %Y "$UCW/knowledge/$doc" 2>/dev/null || stat -f %m "$UCW/knowledge/$doc" 2>/dev/null || echo 0)
    # Look for any source file newer than the doc
    case "$doc" in
      STACK.md)    pattern=("$(dirname "$UCW")/package.json" "$(dirname "$UCW")/pyproject.toml" "$(dirname "$UCW")/go.mod" "$(dirname "$UCW")/Cargo.toml") ;;
      DESIGN.md)   pattern=("$(dirname "$UCW")/src" "$(dirname "$UCW")/lib" "$(dirname "$UCW")/internal") ;;
      *)           continue ;;
    esac
    for p in "${pattern[@]}"; do
      [[ -e "$p" ]] || continue
      mt=$(stat -c %Y "$p" 2>/dev/null || stat -f %m "$p" 2>/dev/null || echo 0)
      if (( mt > doc_mtime + 1 )); then
        stale=$((stale + 1))
        break
      fi
    done
  done
fi

# Color the streak red if >= 5 (streak-breaker threshold)
if [[ "$streak" =~ ^[0-9]+$ ]] && (( streak >= 5 )); then
  streak_disp=$'\033[31m'"$streak"$'\033[0m'
else
  streak_disp="$streak"
fi

# Stale warning in yellow
if (( stale > 0 )); then
  stale_disp=$'\033[33m'"stale: $stale"$'\033[0m'
else
  stale_disp="stale: 0"
fi

printf 'ucw │ phase: %s │ streak: %s │ mem: %s (%s pinned) │ %s\n' \
  "$phase" "$streak_disp" "$mem_total" "$mem_pinned" "$stale_disp"
