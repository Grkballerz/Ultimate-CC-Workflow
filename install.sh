#!/usr/bin/env bash
# UCW installer — idempotent, reversible.
#
# Usage:
#   ./install.sh --profile {minimal|standard|full} [--dry-run] [--verbose]
#   ./install.sh --post-marketplace [--profile standard]
#   ./install.sh --reinstall-deps   (rebuild $UCW_HOME/venv from scratch)
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
REINSTALL_DEPS=0      # rebuild $UCW_HOME/venv from scratch
UCW_PYTHON=""         # set by install_memory_deps; read by register_mcp

log()   { printf '[ucw] %s\n' "$*"; }
debug() { if (( VERBOSE )); then printf '[ucw] - %s\n' "$*" >&2; fi; }
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
    --reinstall-deps)   REINSTALL_DEPS=1; shift ;;
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
    local backup
    backup="$CLAUDE_SETTINGS.ucw.bak.$(date +%s)"
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
  # Use our smart merger (preserves user hooks, idempotent on reinstall).
  python3 "$REPO_ROOT/bin/ucw-merge-settings.py" \
    --inplace "$CLAUDE_SETTINGS" "$fragment"
}

install_rules() {
  run mkdir -p "$CLAUDE_HOME/rules/ucw"
  run cp -r "$REPO_ROOT/rules/." "$CLAUDE_HOME/rules/ucw/"
  log "installed rules → $CLAUDE_HOME/rules/ucw"
}

install_commands() {
  # Symlink every commands/*.md into ~/.claude/commands/ so the user-level
  # command loader finds them. Currently this is just `ucw.md` (the umbrella);
  # if a future PR adds another top-level command, this still picks it up.
  run mkdir -p "$CLAUDE_HOME/commands"
  for f in "$REPO_ROOT/commands/"*.md; do
    [[ -e "$f" ]] || continue
    local dest
    dest="$CLAUDE_HOME/commands/$(basename "$f")"
    run ln -sf "$f" "$dest"
  done
  log "linked commands → $CLAUDE_HOME/commands"
}

install_agents() {
  # Flat-link every .md file under agents/ (recursive) into ~/.claude/agents/.
  # Claude Code discovers agents by their `name:` frontmatter, not by filename,
  # so we keep basenames; reviewer-correctness.md and friends stay distinct
  # because their basenames are unique.
  run mkdir -p "$CLAUDE_HOME/agents"
  while IFS= read -r -d '' f; do
    local dest
    dest="$CLAUDE_HOME/agents/$(basename "$f")"
    run ln -sf "$f" "$dest"
  done < <(find "$REPO_ROOT/agents" -type f -name '*.md' -print0)
  log "linked agents → $CLAUDE_HOME/agents (incl. reviewers/*)"
}

install_skills() {
  # Symlink each SKILL.md's parent directory so adjacent assets (resources/,
  # INSTRUCTIONS.md, etc.) come along. Skill name is the directory name,
  # which is unique across the repo.
  #
  # -n is critical for the directory target case: bare `ln -sf` on an
  # existing symlink-to-dir dereferences and writes INSIDE the linked
  # directory, creating self-referential symlinks on reinstall. `-n`
  # replaces the symlink itself, which is what we want.
  run mkdir -p "$CLAUDE_HOME/skills"
  while IFS= read -r -d '' skill_md; do
    local skill_dir
    skill_dir="$(dirname "$skill_md")"
    local dest
    dest="$CLAUDE_HOME/skills/$(basename "$skill_dir")"
    run ln -sfn "$skill_dir" "$dest"
  done < <(find "$REPO_ROOT/skills" -type f -name 'SKILL.md' -print0)
  log "linked skills → $CLAUDE_HOME/skills"
}

install_hooks() {
  run mkdir -p "$UCW_HOME/hooks"
  for f in "$REPO_ROOT/hooks/"*.py; do
    [[ -e "$f" ]] || continue
    local dest
    dest="$UCW_HOME/hooks/$(basename "$f")"
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
  # dashboard CLI lives at lib/ since it's package-style.
  # -n is critical: `ln -sf` on a directory dest dereferences existing
  # symlinks and writes inside; `-n` replaces the symlink itself, which
  # is what idempotent reinstall actually wants.
  run ln -sfn "$REPO_ROOT/dashboard" "$UCW_HOME/lib/dashboard"
  log "linked bin helpers → $UCW_HOME/bin"
  if ! echo ":$PATH:" | grep -q ":$UCW_HOME/bin:"; then
    log "tip: add to your shell: export PATH=\"\$HOME/.claude/ucw/bin:\$PATH\""
  fi
}

install_memory_deps() {
  if (( DRY_RUN )); then
    log "[DRY] would set up Python env at $UCW_HOME/venv and install ucw-memory"
    return
  fi

  # If --reinstall-deps was passed, blow away the existing venv first so we
  # get a clean rebuild against the current source tree.
  if (( REINSTALL_DEPS )) && [[ -d "$UCW_HOME/venv" ]]; then
    log "removing $UCW_HOME/venv (--reinstall-deps)"
    rm -rf "$UCW_HOME/venv"
  fi

  # If a venv already exists and has ucw_memory importable, reuse it.
  if [[ -x "$UCW_HOME/venv/bin/python" ]] && \
     "$UCW_HOME/venv/bin/python" -c "import ucw_memory" 2>/dev/null; then
    UCW_PYTHON="$UCW_HOME/venv/bin/python"
    log "reusing existing $UCW_HOME/venv (ucw-memory already installed)"
    return
  fi

  # Strategy:
  #   1. If uv is available, use it (handles PEP 668 transparently)
  #   2. If system Python is externally-managed (PEP 668 / Debian / Ubuntu /
  #      Zorin / Homebrew 3.11+), create a dedicated venv at $UCW_HOME/venv
  #      and install there. The MCP server runs under this venv's python.
  #   3. Else, install into the system Python directly.
  #
  # Either way, UCW_PYTHON is set to the python that has ucw_memory installed.
  # register_mcp() uses it to write the absolute command path into mcp.json.

  if command -v uv >/dev/null 2>&1; then
    log "uv found — creating venv at $UCW_HOME/venv"
    if uv venv "$UCW_HOME/venv" 2>/dev/null && \
       uv pip install --quiet --python "$UCW_HOME/venv/bin/python" -e "$REPO_ROOT/memory"; then
      UCW_PYTHON="$UCW_HOME/venv/bin/python"
      log "installed ucw-memory into $UCW_HOME/venv"
      return
    fi
    warn "uv install failed — falling back to direct pip"
  fi

  if _python_is_externally_managed; then
    log "system Python is externally-managed (PEP 668) — creating venv at $UCW_HOME/venv"
    if ! python3 -m venv "$UCW_HOME/venv" 2>/dev/null; then
      warn "python3 -m venv failed (install python3-venv on Debian/Ubuntu/Zorin)"
      warn "ucw-memory was NOT installed; the MCP server will not start"
      return
    fi
    if "$UCW_HOME/venv/bin/pip" install --quiet --upgrade pip 2>/dev/null && \
       "$UCW_HOME/venv/bin/pip" install --quiet -e "$REPO_ROOT/memory" 2>/dev/null; then
      UCW_PYTHON="$UCW_HOME/venv/bin/python"
      log "installed ucw-memory into $UCW_HOME/venv"
      return
    fi
    warn "venv install failed — try: $UCW_HOME/venv/bin/pip install -e $REPO_ROOT/memory"
    return
  fi

  if command -v pip >/dev/null 2>&1; then
    log "installing ucw-memory via pip install -e ./memory"
    if pip install --quiet -e "$REPO_ROOT/memory" 2>/dev/null; then
      UCW_PYTHON="$(command -v python3)"
      return
    fi
    warn "pip install failed (run with --verbose to see why)"
    return
  fi

  warn "no Python package manager found — ucw-memory not installed"
  warn "manually: python3 -m venv $UCW_HOME/venv && $UCW_HOME/venv/bin/pip install -e $REPO_ROOT/memory"
}

_python_is_externally_managed() {
  # PEP 668: distros mark the stdlib path with an EXTERNALLY-MANAGED file.
  # https://peps.python.org/pep-0668/
  python3 - <<'PYEOF' 2>/dev/null
import sys, os
ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
for prefix in (sys.base_prefix, sys.prefix):
    for lib in ("lib", "lib64"):
        marker = os.path.join(prefix, lib, ver, "EXTERNALLY-MANAGED")
        if os.path.exists(marker):
            sys.exit(0)
sys.exit(1)
PYEOF
}

register_mcp() {
  local mcp_config="$CLAUDE_HOME/mcp.json"
  if (( DRY_RUN )); then
    log "[DRY] would register ucw-memory in $mcp_config (command: ${UCW_PYTHON:-python3})"
    return
  fi
  [[ -f "$mcp_config" ]] || echo '{"mcpServers":{}}' > "$mcp_config"

  # Use the python that actually has ucw_memory installed, or fall back to
  # system python3 with PYTHONPATH so the user gets *something* working.
  local cmd="${UCW_PYTHON:-python3}"
  local pythonpath_env="{}"
  if [[ -z "${UCW_PYTHON:-}" ]]; then
    # Fallback path: rely on PYTHONPATH (won't load the [mcp] extras but
    # the stdlib server will still work).
    pythonpath_env='{"PYTHONPATH":"'"$REPO_ROOT/memory"'"}'
    warn "ucw-memory not properly installed; falling back to PYTHONPATH in mcp.json"
    warn "for a working install, ensure python3-venv is available and re-run"
  fi

  local tmp
  tmp="$(mktemp)"
  jq \
    --arg cmd "$cmd" \
    --arg ucw_home "$UCW_HOME" \
    --argjson extra_env "$pythonpath_env" \
    '.mcpServers["ucw-memory"] = {
        type: "stdio",
        command: $cmd,
        args: ["-m", "ucw_memory.server"],
        env: ({"UCW_MEMORY_HOME": $ucw_home} + $extra_env)
     }' \
     "$mcp_config" > "$tmp"
  mv "$tmp" "$mcp_config"
  log "registered ucw-memory MCP server (command: $cmd)"
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
  [[ -L "$UCW_HOME/bin/ucw-auto.py" ]] || { warn "auto-mode helper not linked"; ok=0; }
  [[ -L "$UCW_HOME/bin/ucw-pr-meta.py" ]] || { warn "PR meta helper not linked"; ok=0; }
  [[ -L "$CLAUDE_HOME/commands/ucw.md" ]] || { warn "/ucw command not linked"; ok=0; }
  [[ -L "$CLAUDE_HOME/agents/planner.md" ]] || { warn "planner agent not linked"; ok=0; }
  command -v jq >/dev/null 2>&1 || { warn "jq missing — settings merges will fail"; ok=0; }
  if [[ -f "$CLAUDE_HOME/mcp.json" ]]; then
    jq -e '.mcpServers["ucw-memory"]' "$CLAUDE_HOME/mcp.json" >/dev/null 2>&1 || {
      warn "ucw-memory MCP server not registered"; ok=0;
    }
  fi
  if (( ok )); then
    log "verify: ✓ install looks healthy"
  else
    warn "verify: some checks failed (see above)"
  fi
}

_remove_ucw_symlinks_in() {
  # Remove symlinks in $1 whose readlink target lives under $REPO_ROOT.
  # Leaves the user's own files alone.
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  while IFS= read -r -d '' link; do
    local target
    target="$(readlink -f "$link" 2>/dev/null || true)"
    if [[ -n "$target" && "$target" == "$REPO_ROOT"* ]]; then
      run rm -rf "$link"
    fi
  done < <(find "$dir" -maxdepth 1 -type l -print0)
}

uninstall() {
  log "removing UCW from $CLAUDE_HOME"
  run rm -rf "$CLAUDE_HOME/rules/ucw" "$UCW_HOME/hooks" "$UCW_HOME/bin" \
             "$UCW_HOME/lib" "$UCW_HOME/venv"
  # Remove only the symlinks we created in commands/agents/skills; preserve
  # any commands/agents/skills the user authored themselves.
  _remove_ucw_symlinks_in "$CLAUDE_HOME/commands"
  _remove_ucw_symlinks_in "$CLAUDE_HOME/agents"
  _remove_ucw_symlinks_in "$CLAUDE_HOME/skills"
  # Strip ucw entries from settings.json while preserving the user's own.
  if [[ -f "$CLAUDE_SETTINGS" && $DRY_RUN -eq 0 ]]; then
    python3 "$REPO_ROOT/bin/ucw-merge-settings.py" \
      --uninstall --inplace "$CLAUDE_SETTINGS" \
      2>/dev/null || warn "could not strip UCW entries from $CLAUDE_SETTINGS"
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
  install_commands
  install_agents
  install_skills
  apply_profile "${PROFILE:-standard}"
  verify_install
  log "post-marketplace setup complete."
  exit 0
fi

case "$PROFILE" in
  minimal|standard|full)
    install_rules
    install_bin
    install_commands
    install_agents
    # Skills are most useful with hooks (TDD loop, commit discipline) so
    # we ship them in standard/full only — minimal stays lean.
    [[ "$PROFILE" != "minimal" ]] && install_hooks
    [[ "$PROFILE" != "minimal" ]] && install_skills
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
