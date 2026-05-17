"""Smoke test — schema applies cleanly, basic CRUD works, FTS triggers fire."""
from __future__ import annotations

import sqlite3
import time

from ucw_memory.db import init_db


def test_schema_applies(tmp_path):
    db = tmp_path / "memory.sqlite"
    init_db(db)

    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for expected in {
            "facts", "embeddings", "instincts", "sessions",
            "work_items", "work_item_sessions", "schema_version",
        }:
            assert expected in tables, f"missing table: {expected}"

        assert "facts_fts" in tables

        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 1
    finally:
        conn.close()


def test_fact_insert_and_fts(tmp_path):
    db = tmp_path / "memory.sqlite"
    init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """INSERT INTO facts(scope, predicate, subject, object, reason, created_at)
               VALUES ('project', 'uses', 'demo-app', 'flask', 'because user said so', ?)""",
            (int(time.time()),),
        )
        conn.commit()

        row = conn.execute(
            "SELECT subject, object FROM facts_fts WHERE facts_fts MATCH 'flask'"
        ).fetchone()
        assert row is not None
        assert row[0] == "demo-app"
        assert row[1] == "flask"
    finally:
        conn.close()
