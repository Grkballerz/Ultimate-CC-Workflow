# ucw-memory

The memory subsystem for the Ultimate Claude Code Workflow.

- **FTS5** keyword retrieval (stdlib-only — works out of the box)
- **sqlite-vec** vector retrieval (optional, install `ucw-memory[vec]`)
- **Voyage** embeddings + reranking (optional, install `ucw-memory[voyage]`)
- **Claude Haiku** contextual prefixes and fallback reranking (optional, install `ucw-memory[claude]`)
- **MCP server** transport (optional, install `ucw-memory[mcp]`)

## Install

```
pip install -e .                # FTS5-only — no extra deps
pip install -e ".[all]"         # full stack: vec + voyage + claude + mcp
```

## CLI

```
ucw-memory init <path>           # create a fresh DB at path
ucw-memory note "fact" "reason"  # add a fact
ucw-memory recall "query"        # hybrid search, top 12
ucw-memory stats                 # counts, hit rate, embedding mode
```

## MCP server

```
python -m ucw_memory.server      # stdio JSON-RPC; configure in ~/.claude/mcp.json
```

See `mcp/ucw-memory.json` in the parent repo for the suggested config.
