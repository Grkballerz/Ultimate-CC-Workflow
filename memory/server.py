"""UCW Memory MCP server — M1 stub.

This is a placeholder that documents the tool surface and provides `memory.init`
so the onboarder can create the SQLite file with the schema. M3 will turn this
into a real MCP server (anthropic-mcp / mcp Python SDK) with recall, note, pin,
forget, list, merge, expire, stats.

Run standalone for now: `python -m ucw_memory.server --init <path>`.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw_memory.server")
    parser.add_argument("--init", metavar="PATH", help="initialize a memory DB at PATH and exit")
    args = parser.parse_args(argv)

    if args.init:
        init_db(Path(args.init))
        print(f"initialized {args.init}", file=sys.stderr)
        return 0

    # M3 will replace this block with `mcp.server.stdio.run(...)`.
    print(
        "UCW memory MCP server: M1 stub. "
        "Use --init to bootstrap a DB; full MCP transport lands in M3.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
