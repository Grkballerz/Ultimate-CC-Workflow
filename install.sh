#!/usr/bin/env bash
# UCW installer — idempotent, reversible.
#
# Usage:
#   ./install.sh --profile {minimal|standard|full} [--dry-run] [--verbose]
#   ./install.sh --post-marketplace [--profile standard]
#   ./install.sh --uninstall
#
# Profiles:
#   minimal   memory MCP + core rules. No hooks, no gates.
#   standard  + planner/implementer/reviewer agents, verification gates, inner-loop hooks.
#   full      + security-reviewer, all language packs, dashboard, PR-watch.
#
# Idempotent: re-running is safe. Settings are merged into ~/.claude/settings.json
# via jq; existing keys are preserved. Hooks are symlinked, not copied, so a
# `git pull` in this repo updates the installed hooks automatically.
set -euo pipefail

UCW_HOME="${UCW_HOME:-$HOME/.claude/ucw}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CLAUDE_SETTINGS="$CLAUDE_HOME/settings.json"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE=""
POST_MARKETPLACE=0
UNINSTALL=0
DRY_RUN=0
VERBOSE=0

log()   { printf '[ucw] %s\n' "$*"; }
debug() { (( VERBOSE )) && printf '[ucw] · %s\n' "$*" >&2 || true; }
warn()  { printf '[ucw] WARN: %s\n' "$*" >&2; }
die()   { printf '[ucw] ERR:  %s\n' "$*" >&2; exit 1; }

run() {
  if (( DRY_RUN )); then
    printf '[ucw] [DRY] %s\n' "$*"
  else
    debug "$*"
    "$@"
  fi
}

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# ---- arg parsing ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)          PROFILE="${2:-}"; shift 2 ;;
    --post-marketplace) POST_MARKETPLACE=1; shift ;;
    --uninstall)        UNINSTALL=1; shift ;;
    --dry-run)          DRY_RUN=1; shift ;;
    --verbose|-v)       VERBOSE=1; shift ;;
    -h|--help)          usage 0 ;;
    *)                  die "unknown arg: $1" ;;
  esac
done

require() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

require jq

ensure_dirs() {
  run mkdir -p "$UCW_HOME" "$CLAUDE_HOME" "$UCW_HOME/lib"
}

backup_settings() {
  if [[ -f "$CLAUDE_SETTINGS" ]]; then
    local backup="$CLAUDE_SETTINGS.ucw.bak.$(date +%s)"
    run cp "$CLAUDE_SETTINGS" "$backup"
    debug "backed up settings to $backup"
  elif (( ! DRY_RUN )); then
    echo '{}' > "$CLAUDE_SETTINGS"
  fi
}

merge_settings() {
  local fragment="$1"
  [[ -f "$fragment" ]] || die "settings fragment not found: $fragment"
  if (( DRY_RUN )); then
    log "[DRY] would merge $fragment into $CLAUDE_SETTINGS"
    return
  fi
  local tmp
  tmp="$(mktemp)"
  jq -s '.[0] * .[1]' "$CLAUDE_SETTINGS" "$fragment" > "$tmp"
  mv "$tmp" "$CLAUDE_SETTINGS"
}

install_rules() {
  run mkdir -p "$CLAUDE_HOME/rules/ucw"
  run cp -r "$REPO_ROOT/rules/." "$CLAUDE_HOME/rules/ucw/"
  log "installed rules → $CLAUDE_HOME/rules/ucw"
}

install_hooks() {
  run mkdir -p "$UCW_HOME/hooks"
  for f in "$REPO_ROOT/hooks/"*.py; do
    [[ -e "$f" ]] || continue
    local dest="$UCW_HOME/hooks/$(basename "$f")"
    run ln -sf "$f" "$dest"
  done
  # _hook_common.py shared helper
  run ln -sf "$REPO_ROOT/hooks/_hook_common.py" "$UCW_HOME/hooks/_hook_common.py"
  log "linked hooks → $UCW_HOME/hooks (symlinks → repo, so git pull updates them)"
}

install_bin() {
  run mkdir -p "$UCW_HOME/bin"
  for f in "$REPO_ROOT/bin/"*.py "$REPO_ROOT/dashboard/statusline.sh"; do
    [[ -e "$f" ]] || continue
    local base
    base="$(basename "$f")"
    run ln -sf "$f" "$UCW_HOME/bin/$base"
  done
  # dashboard CLI lives at lib/ since it's package-style
  run ln -sf "$REPO_ROOT/dashboard" "$UCW_HOME/lib/dashboard"
  log "linked bin helpers → $UCW_HOME/bin"
  if ! echo ":$PATH:" | grep -q ":$UCW_HOME/bin:"; then
    log "tip: add to your shell: export PATH=\"\$HOME/.claude/ucw/bin:\$PATH\""
  fi
}

install_memory_deps() {
  if (( DRY_RUN )); then
    log "[DRY] would pip install -e $REPO_ROOT/memory"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    log "installing ucw-memory via uv pip install -e ./memory"
    (cd "$REPO_ROOT/memory" && uv pip install --quiet -e . 2>/dev/null) || \
      warn "uv install failed — falling back to PYTHONPATH"
  elif command -v pip >/dev/null 2>&1; then
    log "installing ucw-memory via pip install -e ./memory"
    pip install --quiet -e "$REPO_ROOT/memory" 2>/dev/null || \
      warn "pip install failed — falling back to PYTHONPATH"
  else
    warn "no Python package manager — ucw_memory.server will rely on PYTHONPATH"
  fi
  # Always export PYTHONPATH as a safety net for the MCP server.
  log "tip: if MCP can't find ucw_memory, set PYTHONPATH=$REPO_ROOT/memory in your shell"
}

register_mcp() {
  local mcp_config="$CLAUDE_HOME/mcp.json"
  if (( DRY_RUN )); then
    log "[DRY] would register ucw-memory in $mcp_config"
    return
  fi
  [[ -f "$mcp_config" ]] || echo '{"mcpServers":{}}' > "$mcp_config"
  local tmp
  tmp="$(mktemp)"
  jq --argjson server "$(cat "$REPO_ROOT/mcp/ucw-memory.json")" \
     '.mcpServers["ucw-memory"] = $server.ucwMemory' \
     "$mcp_config" > "$tmp"
  mv "$tmp" "$mcp_config"
  log "registered ucw-memory MCP server in $mcp_config"
}

apply_profile() {
  local profile="$1"
  local fragment="$REPO_ROOT/settings/$profile.json"
  [[ -f "$fragment" ]] || die "unknown profile: $profile"
  merge_settings "$fragment"
  log "applied $profile profile to $CLAUDE_SETTINGS"
}

verify_install() {
  if (( DRY_RUN )); then
    return
  fi
  local ok=1
  [[ -d "$CLAUDE_HOME/rules/ucw" ]] || { warn "rules dir missing"; ok=0; }
  [[ -L "$UCW_HOME/bin/ucw-audit.py" ]] || { warn "audit helper not linked"; ok=0; }
  command -v jq >/dev/null 2>&1 || { warn "jq missing — settings merges will fail"; ok=0; }
  if [[ -f "$CLAUDE_HOME/mcp.json" ]]; then
    jq -e '.mcpServers["ucw-memory"]' "$CLAUDE_HOME/mcp.json" >/dev/null 2>&1 || {
      warn "ucw-memory MCP server not registered"; ok=0;
    }
  fi
  (( ok )) && log "verify: ✓ install looks healthy" || warn "verify: some checks failed (see above)"
}

uninstall() {
  log "removing UCW from $CLAUDE_HOME"
  run rm -rf "$CLAUDE_HOME/rules/ucw" "$UCW_HOME/hooks" "$UCW_HOME/bin" "$UCW_HOME/lib"
  # Strip ucw entries from settings.json
  if [[ -f "$CLAUDE_SETTINGS" && $DRY_RUN -eq 0 ]]; then
    local tmp
    tmp="$(mktemp)"
    jq '
      walk(
        if type == "object" and has("command") and (.command | tostring | test("ucw"))
        then empty else . end
      )
    ' "$CLAUDE_SETTINGS" > "$tmp" 2>/dev/null || cp "$CLAUDE_SETTINGS" "$tmp"
    mv "$tmp" "$CLAUDE_SETTINGS"
  fi
  # Strip ucw-memory MCP entry
  if [[ -f "$CLAUDE_HOME/mcp.json" && $DRY_RUN -eq 0 ]]; then
    local tmp
    tmp="$(mktemp)"
    jq 'del(.mcpServers["ucw-memory"])' "$CLAUDE_HOME/mcp.json" > "$tmp" 2>/dev/null \
      || cp "$CLAUDE_HOME/mcp.json" "$tmp"
    mv "$tmp" "$CLAUDE_HOME/mcp.json"
  fi
  log "done. memory db at $UCW_HOME left intact — rm -rf $UCW_HOME to fully purge."
}

# ---- main -------------------------------------------------------------------
if (( UNINSTALL )); then
  uninstall
  exit 0
fi

if [[ -z "$PROFILE" && $POST_MARKETPLACE -eq 0 ]]; then
  usage 1
fi

ensure_dirs
backup_settings

if (( POST_MARKETPLACE )); then
  install_rules
  install_hooks
  install_bin
  apply_profile "${PROFILE:-standard}"
  verify_install
  log "post-marketplace setup complete."
  exit 0
fi

case "$PROFILE" in
  minimal|standard|full)
    install_rules
    install_bin
    [[ "$PROFILE" != "minimal" ]] && install_hooks
    install_memory_deps
    register_mcp
    apply_profile "$PROFILE"
    verify_install
    log "UCW $PROFILE profile installed. Start Claude Code and run /ucw init."
    ;;
  *)
    usage 1
    ;;
esac
