"""CLI surface for the UCW memory subsystem.

Useful for testing without spinning up the MCP transport.

    ucw-memory init [PATH]
    ucw-memory note <subject> <predicate> <object> --reason "<reason>"
    ucw-memory recall <query>
    ucw-memory pin <fact_id>
    ucw-memory forget <fact_id>
    ucw-memory list [--scope project|global] [--limit N]
    ucw-memory stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .db import MemoryDB, init_db
from .retrieval import recall


def _default_db(scope: str = "project") -> Path:
    if scope == "global":
        return Path(os.environ.get("UCW_MEMORY_HOME", Path.home() / ".claude" / "ucw")) / "memory.sqlite"
    # Walk up looking for .ucw
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir():
            return parent / ".ucw" / "memory.sqlite"
    return cwd / ".ucw" / "memory.sqlite"


def cmd_init(args: argparse.Namespace) -> int:
    db_path = Path(args.path) if args.path else _default_db(args.scope)
    init_db(db_path)
    print(json.dumps({"ok": True, "db_path": str(db_path)}))
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    db_path = _default_db(args.scope)
    with MemoryDB(db_path) as db:
        fact_id = db.note(
            scope=args.scope,
            subject=args.subject,
            predicate=args.predicate,
            object_=args.object,
            reason=args.reason,
            confidence=args.confidence,
            pinned=args.pinned,
        )
    print(json.dumps({"ok": True, "id": fact_id}))
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    db_path = _default_db(args.scope)
    with MemoryDB(db_path) as db:
        result = recall(db, args.query, k=args.k, char_budget=args.budget, scope=args.scope if args.strict_scope else None)
    payload = {
        "embedding_mode": result.embedding_mode,
        "hits": [
            {
                "id": h.fact.id, "score": round(h.score, 4),
                "sources": list(h.sources), "text": h.fact.as_text(),
                "scope": h.fact.scope, "pinned": h.fact.pinned,
            }
            for h in result.hits
        ],
        "context": result.as_context(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    with MemoryDB(_default_db(args.scope)) as db:
        db.pin(args.id)
    print(json.dumps({"ok": True, "id": args.id, "pinned": True}))
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    with MemoryDB(_default_db(args.scope)) as db:
        db.forget(args.id)
    print(json.dumps({"ok": True, "id": args.id, "deleted": True}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with MemoryDB(_default_db(args.scope)) as db:
        facts = db.list_facts(scope=args.scope if args.strict_scope else None, limit=args.limit)
    print(json.dumps([
        {"id": f.id, "scope": f.scope, "pinned": f.pinned, "text": f.as_text()}
        for f in facts
    ], indent=2))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with MemoryDB(_default_db(args.scope)) as db:
        print(json.dumps(db.stats(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-memory")
    parser.add_argument("--scope", default="project", choices=["project", "global"])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize a memory DB")
    p_init.add_argument("path", nargs="?", help="explicit DB path (otherwise inferred)")
    p_init.set_defaults(func=cmd_init)

    p_note = sub.add_parser("note", help="add a fact")
    p_note.add_argument("subject")
    p_note.add_argument("predicate")
    p_note.add_argument("object")
    p_note.add_argument("--reason", required=True)
    p_note.add_argument("--confidence", type=float, default=0.7)
    p_note.add_argument("--pinned", action="store_true")
    p_note.set_defaults(func=cmd_note)

    p_recall = sub.add_parser("recall", help="hybrid search")
    p_recall.add_argument("query")
    p_recall.add_argument("-k", type=int, default=12)
    p_recall.add_argument("--budget", type=int, default=4000)
    p_recall.add_argument("--strict-scope", action="store_true", help="only search current scope")
    p_recall.set_defaults(func=cmd_recall)

    p_pin = sub.add_parser("pin")
    p_pin.add_argument("id", type=int)
    p_pin.set_defaults(func=cmd_pin)

    p_forget = sub.add_parser("forget")
    p_forget.add_argument("id", type=int)
    p_forget.set_defaults(func=cmd_forget)

    p_list = sub.add_parser("list")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--strict-scope", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_stats = sub.add_parser("stats")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
