#!/usr/bin/env bash
# UCW installer — idempotent, reversible.
# Usage: ./install.sh --profile {minimal|standard|full} [--post-marketplace] [--uninstall]
set -euo pipefail

UCW_HOME="${UCW_HOME:-$HOME/.claude/ucw}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CLAUDE_SETTINGS="$CLAUDE_HOME/settings.json"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE=""
POST_MARKETPLACE=0
UNINSTALL=0

log()  { printf '[ucw] %s\n' "$*"; }
warn() { printf '[ucw] WARN: %s\n' "$*" >&2; }
die()  { printf '[ucw] ERR:  %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
UCW installer.

  ./install.sh --profile {minimal|standard|full}
  ./install.sh --post-marketplace            # lay down rules+hooks after /plugin install
  ./install.sh --uninstall                   # remove UCW from ~/.claude

Profiles:
  minimal   memory MCP + core rules. No hooks, no gates.
  standard  + planner/implementer/reviewer agents, verification gates, inner-loop hooks.
  full      + security-reviewer, all language packs, dashboard, PR-watch.
EOF
}

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --post-marketplace) POST_MARKETPLACE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

require() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

require jq

ensure_dirs() {
  mkdir -p "$UCW_HOME" "$CLAUDE_HOME"
}

backup_settings() {
  if [[ -f "$CLAUDE_SETTINGS" ]]; then
    cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.ucw.bak.$(date +%s)"
  else
    echo '{}' > "$CLAUDE_SETTINGS"
  fi
}

merge_settings() {
  local fragment="$1"
  [[ -f "$fragment" ]] || die "settings fragment not found: $fragment"
  local tmp
  tmp="$(mktemp)"
  jq -s '.[0] * .[1]' "$CLAUDE_SETTINGS" "$fragment" > "$tmp"
  mv "$tmp" "$CLAUDE_SETTINGS"
}

install_rules() {
  mkdir -p "$CLAUDE_HOME/rules/ucw"
  cp -r "$REPO_ROOT/rules/." "$CLAUDE_HOME/rules/ucw/"
  log "installed rules to $CLAUDE_HOME/rules/ucw"
}

install_hooks() {
  mkdir -p "$UCW_HOME/hooks"
  cp -r "$REPO_ROOT/hooks/." "$UCW_HOME/hooks/"
  chmod +x "$UCW_HOME/hooks"/*.py 2>/dev/null || true
  log "installed hooks to $UCW_HOME/hooks"
}

install_memory_deps() {
  if command -v uv >/dev/null 2>&1; then
    log "installing memory deps via uv"
    (cd "$REPO_ROOT/memory" && uv pip install --quiet -e . 2>/dev/null) || \
      warn "skipped memory deps (uv install failed — install manually later)"
  elif command -v pip >/dev/null 2>&1; then
    log "installing memory deps via pip"
    pip install --quiet sqlite-vec anthropic voyageai 2>/dev/null || \
      warn "skipped memory deps (pip install failed — install manually later)"
  else
    warn "no Python package manager found — memory MCP will not work until deps are installed"
  fi
}

register_mcp() {
  local mcp_config="$CLAUDE_HOME/mcp.json"
  [[ -f "$mcp_config" ]] || echo '{"mcpServers":{}}' > "$mcp_config"
  local tmp
  tmp="$(mktemp)"
  jq --argjson server "$(cat "$REPO_ROOT/mcp/ucw-memory.json")" \
     '.mcpServers["ucw-memory"] = $server.ucwMemory' \
     "$mcp_config" > "$tmp"
  mv "$tmp" "$mcp_config"
  log "registered ucw-memory MCP server"
}

apply_profile() {
  local profile="$1"
  local fragment="$REPO_ROOT/settings/$profile.json"
  [[ -f "$fragment" ]] || die "unknown profile: $profile"
  merge_settings "$fragment"
  log "applied $profile profile to $CLAUDE_SETTINGS"
}

uninstall() {
  log "removing UCW from $CLAUDE_HOME"
  rm -rf "$CLAUDE_HOME/rules/ucw" "$UCW_HOME/hooks"
  # Strip ucw entries from settings.json
  if [[ -f "$CLAUDE_SETTINGS" ]]; then
    local tmp
    tmp="$(mktemp)"
    jq 'del(.hooks?.SessionStart?[]?|select(.hooks[]?.command|test("ucw"))) | del(.. | objects | select(.command? and (.command | test("ucw"))))' \
      "$CLAUDE_SETTINGS" > "$tmp" 2>/dev/null || cp "$CLAUDE_SETTINGS" "$tmp"
    mv "$tmp" "$CLAUDE_SETTINGS"
  fi
  log "done. memory db at $UCW_HOME left intact — rm -rf $UCW_HOME to fully purge."
}

# ---- main ----
if [[ $UNINSTALL -eq 1 ]]; then
  uninstall
  exit 0
fi

if [[ -z "$PROFILE" && $POST_MARKETPLACE -eq 0 ]]; then
  usage
  exit 1
fi

ensure_dirs
backup_settings

if [[ $POST_MARKETPLACE -eq 1 ]]; then
  install_rules
  install_hooks
  apply_profile "${PROFILE:-standard}"
  log "post-marketplace setup complete."
  exit 0
fi

case "$PROFILE" in
  minimal|standard|full)
    install_rules
    [[ "$PROFILE" != "minimal" ]] && install_hooks
    install_memory_deps
    register_mcp
    apply_profile "$PROFILE"
    log "UCW $PROFILE profile installed. Start Claude Code and run /ucw init."
    ;;
  *)
    usage
    exit 1
    ;;
esac
