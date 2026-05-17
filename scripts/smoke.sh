#!/usr/bin/env bash
# End-to-end smoke test for UCW. Exercises every component in sequence:
#
#   1. Spin up a fake Next.js+pnpm project in a tmp dir
#   2. Detect the stack
#   3. Render Knowledge docs against detector output + fake preferences
#   4. Initialize a memory DB
#   5. Distill a synthetic transcript into facts
#   6. Recall those facts via the MCP server (over stdio JSON-RPC)
#   7. Run the audit against this repo
#   8. Print "all green" if every step exits 0
#
# Usage: ./scripts/smoke.sh [--keep-tmp]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT:$REPO_ROOT/memory${PYTHONPATH:+:$PYTHONPATH}"

KEEP_TMP=0
[[ "${1:-}" == "--keep-tmp" ]] && KEEP_TMP=1

PROJECT="$(mktemp -d)"
cleanup() {
  if [[ $KEEP_TMP -eq 0 ]]; then
    rm -rf "$PROJECT"
  else
    echo "[smoke] kept tmp project at $PROJECT"
  fi
}
trap cleanup EXIT

step() { printf '\n[smoke] %s\n' "$*"; }

# 1. fake project
step "1/8 build fake Next.js project at $PROJECT"
cat > "$PROJECT/package.json" <<'EOF'
{
  "name": "demo",
  "engines": { "node": ">=20" },
  "dependencies": { "next": "14.0.0", "react": "18.0.0", "typescript": "5.0.0" },
  "devDependencies": { "vitest": "1.0.0", "eslint": "8.0.0", "prettier": "3.0.0" }
}
EOF
echo "lockfileVersion: 6" > "$PROJECT/pnpm-lock.yaml"
echo "{}" > "$PROJECT/tsconfig.json"
echo "{}" > "$PROJECT/vercel.json"

# 2. detect
step "2/8 detect stack"
STACK_JSON="$(python3 "$REPO_ROOT/bin/ucw-detect-stack.py" "$PROJECT")"
echo "$STACK_JSON" | head -20

# 3. render knowledge
step "3/8 render Knowledge docs"
STATE_DIR="$PROJECT/.ucw/state"
mkdir -p "$STATE_DIR"
cat > "$STATE_DIR/init.json" <<EOF
{
  "stack": $STACK_JSON,
  "preferences": {
    "image_gen_tool": "Flux",
    "diagram_tool": "Mermaid",
    "deploy_target": "Vercel",
    "database": "Postgres",
    "package_manager": "pnpm",
    "docs_surface": "Plain .ucw/knowledge/",
    "embedding_provider": "Claude Haiku reranking",
    "scribe_mode": "auto"
  },
  "project_summary": "A demo Next.js app for the UCW smoke test.",
  "init_at": "2026-05-17"
}
EOF
python3 "$REPO_ROOT/bin/ucw-render-knowledge.py" \
  --state "$STATE_DIR/init.json" --project-root "$PROJECT"
echo "[smoke]   knowledge dir contents:"
ls "$PROJECT/.ucw/knowledge"

# 4. init memory
step "4/8 init memory DB"
python3 -m ucw_memory.server --init "$PROJECT/.ucw/memory.sqlite"

# 5. distill a synthetic transcript
step "5/8 distill synthetic transcript"
cat > "$PROJECT/transcript.jsonl" <<'EOF'
{"message":{"content":"We chose to use postgres because we need JSONB."}}
{"message":{"content":[{"type":"text","text":"The user prefers pnpm since the lockfile is smaller."}]}}
{"prompt":"We will use pytest because it's the team default."}
EOF
cd "$PROJECT"
python3 -m ucw_memory.cli distill "$PROJECT/transcript.jsonl"
cd "$REPO_ROOT"

# 6. recall via MCP JSON-RPC
step "6/8 recall 'postgres' via MCP server"
cd "$PROJECT"
RECALL_RESP="$(python3 -m ucw_memory.server --once \
  '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory.recall","arguments":{"query":"postgres"}}}')"
cd "$REPO_ROOT"
echo "$RECALL_RESP" | python3 -c '
import json, sys
resp = json.load(sys.stdin)
text = resp["result"]["content"][0]["text"]
data = json.loads(text)
print("  embedding_mode:", data["embedding_mode"])
print("  hits:", len(data["hits"]))
for h in data["hits"]:
    print("    [%.2f] %s" % (h["score"], h["text"]))
'

# 7. audit
step "7/8 audit"
python3 "$REPO_ROOT/bin/ucw-audit.py" --repo "$REPO_ROOT"

# 8. done
step "8/8 all green ✓"
