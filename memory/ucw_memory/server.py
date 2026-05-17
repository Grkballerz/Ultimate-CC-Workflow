"""UCW Memory MCP server (stdio, JSON-RPC 2.0).

Implements just enough of the MCP protocol — `initialize`, `tools/list`,
`tools/call` — to expose the memory toolset to Claude Code. Hand-rolled to
keep the dependency surface zero: the FTS5 path needs only the Python stdlib.

When `ucw-memory[mcp]` is installed and someone wants the official SDK,
swap this entry point for the `mcp` package — the underlying `MemoryDB` and
`retrieval.recall()` are unchanged.

Run:
    python -m ucw_memory.server                 # stdio (Claude Code spawns this)
    python -m ucw_memory.server --init <path>   # bootstrap a DB (no transport)
    python -m ucw_memory.server --once '<json>' # handle one JSON-RPC and exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .db import MemoryDB, init_db
from .retrieval import recall

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ucw-memory", "version": "0.1.0"}


# ---- DB resolution -----------------------------------------------------------

def _project_db() -> Path:
    """Find the per-project DB by walking up from $PWD looking for `.ucw/`."""
    cwd = Path(os.getcwd())
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir():
            return parent / ".ucw" / "memory.sqlite"
    return cwd / ".ucw" / "memory.sqlite"


def _global_db() -> Path:
    return Path(os.environ.get("UCW_MEMORY_HOME", Path.home() / ".claude" / "ucw")) / "memory.sqlite"


def _open_db(scope: str) -> MemoryDB:
    return MemoryDB(_global_db() if scope == "global" else _project_db())


# ---- Tools -------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {
    "memory.recall": {
        "description": "Hybrid memory search. Returns top-k facts ranked by FTS5+recency (vector + rerank when available).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":        {"type": "string"},
                "k":            {"type": "integer", "default": 12, "minimum": 1, "maximum": 50},
                "budget_chars": {"type": "integer", "default": 4000, "minimum": 200},
                "scope":        {"type": "string", "enum": ["project", "global", "all"], "default": "all"},
            },
            "required": ["query"],
        },
    },
    "memory.note": {
        "description": "Add a durable fact. `reason` is mandatory — bare conclusions are rejected by the quality gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject":    {"type": "string"},
                "predicate":  {"type": "string"},
                "object":     {"type": "string"},
                "reason":     {"type": "string"},
                "scope":      {"type": "string", "enum": ["project", "global"], "default": "project"},
                "confidence": {"type": "number", "default": 0.7, "minimum": 0, "maximum": 1},
                "pinned":     {"type": "boolean", "default": False},
            },
            "required": ["subject", "predicate", "object", "reason"],
        },
    },
    "memory.pin": {
        "description": "Pin a fact so it's always included in SessionStart context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer"},
                "scope":   {"type": "string", "enum": ["project", "global"], "default": "project"},
            },
            "required": ["fact_id"],
        },
    },
    "memory.forget": {
        "description": "Soft-delete a fact. Reversible from the SQLite WAL until vacuum.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "integer"},
                "scope":   {"type": "string", "enum": ["project", "global"], "default": "project"},
            },
            "required": ["fact_id"],
        },
    },
    "memory.list": {
        "description": "List facts in a scope, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope":  {"type": "string", "enum": ["project", "global"], "default": "project"},
                "limit":  {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "default": 0,  "minimum": 0},
            },
        },
    },
    "memory.stats": {
        "description": "Counts and DB paths for both scopes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "memory.init": {
        "description": "Initialize a memory DB. Idempotent — safe to call repeatedly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["project", "global"], "default": "project"},
            },
        },
    },
}


def tool_recall(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"]
    k = int(args.get("k", 12))
    budget = int(args.get("budget_chars", 4000))
    scope = args.get("scope", "all")

    if scope == "all":
        with _open_db("project") as pdb, _open_db("global") as gdb:
            p_result = recall(pdb, query, k=k, char_budget=budget)
            g_result = recall(gdb, query, k=k, char_budget=budget)
            merged = sorted(p_result.hits + g_result.hits, key=lambda h: h.score, reverse=True)[:k]
            embedding_mode = p_result.embedding_mode
    else:
        with _open_db(scope) as db:
            result = recall(db, query, k=k, char_budget=budget, scope=scope)
            merged = result.hits
            embedding_mode = result.embedding_mode

    return {
        "embedding_mode": embedding_mode,
        "hits": [
            {
                "id":          h.fact.id,
                "scope":       h.fact.scope,
                "pinned":      h.fact.pinned,
                "score":       round(h.score, 4),
                "sources":     list(h.sources),
                "text":        h.fact.as_text(),
                "confidence":  h.fact.confidence,
                "created_at":  h.fact.created_at,
            }
            for h in merged
        ],
    }


def tool_note(args: dict[str, Any]) -> dict[str, Any]:
    scope = args.get("scope", "project")
    with _open_db(scope) as db:
        fact_id = db.note(
            scope=scope,
            subject=args["subject"],
            predicate=args["predicate"],
            object_=args["object"],
            reason=args["reason"],
            confidence=float(args.get("confidence", 0.7)),
            pinned=bool(args.get("pinned", False)),
        )
    return {"id": fact_id, "scope": scope}


def tool_pin(args: dict[str, Any]) -> dict[str, Any]:
    scope = args.get("scope", "project")
    with _open_db(scope) as db:
        db.pin(int(args["fact_id"]))
    return {"id": int(args["fact_id"]), "pinned": True}


def tool_forget(args: dict[str, Any]) -> dict[str, Any]:
    scope = args.get("scope", "project")
    with _open_db(scope) as db:
        db.forget(int(args["fact_id"]))
    return {"id": int(args["fact_id"]), "deleted": True}


def tool_list(args: dict[str, Any]) -> dict[str, Any]:
    scope = args.get("scope", "project")
    with _open_db(scope) as db:
        facts = db.list_facts(
            scope=scope,
            limit=int(args.get("limit", 50)),
            offset=int(args.get("offset", 0)),
        )
    return {
        "facts": [
            {"id": f.id, "scope": f.scope, "pinned": f.pinned, "text": f.as_text(),
             "confidence": f.confidence, "created_at": f.created_at}
            for f in facts
        ]
    }


def tool_stats(_: dict[str, Any]) -> dict[str, Any]:
    with _open_db("project") as p, _open_db("global") as g:
        return {"project": p.stats(), "global": g.stats(), "embedding_mode": "fts-only"}


def tool_init(args: dict[str, Any]) -> dict[str, Any]:
    scope = args.get("scope", "project")
    db_path = _global_db() if scope == "global" else _project_db()
    init_db(db_path)
    return {"ok": True, "db_path": str(db_path), "scope": scope}


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "memory.recall": tool_recall,
    "memory.note":   tool_note,
    "memory.pin":    tool_pin,
    "memory.forget": tool_forget,
    "memory.list":   tool_list,
    "memory.stats":  tool_stats,
    "memory.init":   tool_init,
}


# ---- JSON-RPC plumbing -------------------------------------------------------

def _respond(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request. Returns None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {}) or {}
    is_notification = "id" not in req

    if method == "initialize":
        return _respond(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _respond(req_id, {
            "tools": [
                {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
                for name, spec in TOOLS.items()
            ]
        })

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _error(req_id, -32601, f"unknown tool: {name}")

        # Validate required fields against the tool's inputSchema.
        spec = TOOLS.get(name, {})
        required = spec.get("inputSchema", {}).get("required", []) or []
        missing = [field for field in required if field not in arguments]
        if missing:
            return _error(
                req_id, -32602,
                f"missing required argument(s): {', '.join(missing)}",
                {"tool": name, "required": required, "received": list(arguments.keys())},
            )

        try:
            result = handler(arguments)
        except ValueError as exc:
            # ValueError from the handler is a user-facing validation problem.
            return _error(req_id, -32602, str(exc))
        except Exception as exc:
            return _error(req_id, -32603, f"tool error: {exc}", traceback.format_exc())
        return _respond(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "isError": False,
        })

    if is_notification:
        return None

    return _error(req_id, -32601, f"unknown method: {method}")


def serve_stdio() -> int:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps(_error(None, -32700, f"parse error: {exc}")) + "\n")
            sys.stdout.flush()
            continue

        response = handle_request(req)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw_memory.server")
    parser.add_argument("--init", metavar="PATH", help="initialize a DB at PATH and exit")
    parser.add_argument("--once", metavar="JSON", help="handle one JSON-RPC request and exit (for testing)")
    args = parser.parse_args(argv)

    if args.init:
        init_db(Path(args.init))
        print(f"initialized {args.init}", file=sys.stderr)
        return 0

    if args.once:
        req = json.loads(args.once)
        response = handle_request(req)
        if response is not None:
            print(json.dumps(response))
        return 0

    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
