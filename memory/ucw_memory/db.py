"""SQLite layer for UCW memory.

Pure stdlib. Embedding/reranking layers (Voyage, Claude) are optional and
plugged in by `retrieval.py`. Keeps the storage primitive simple and testable.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_ROOT.parent / "schema.sql"


def init_db(db_path: Path) -> Path:
    """Create or upgrade the DB at db_path. Returns the path."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    return db_path


@dataclass(frozen=True)
class Fact:
    id: int
    scope: str
    predicate: str
    subject: str
    object: str
    reason: str
    contextual_prefix: str | None
    confidence: float
    created_at: int
    ttl_seconds: int | None
    pinned: bool

    def as_text(self) -> str:
        """Render as a single line for context injection."""
        return f"{self.subject} {self.predicate} {self.object} (because {self.reason})"


class MemoryDB:
    """Thin wrapper. One instance per scope file."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            init_db(self.db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- writes -------------------------------------------------------------

    def note(
        self,
        *,
        scope: str,
        subject: str,
        predicate: str,
        object_: str,
        reason: str,
        contextual_prefix: str | None = None,
        source_session: str | None = None,
        confidence: float = 0.5,
        ttl_seconds: int | None = None,
        pinned: bool = False,
    ) -> int:
        if not reason.strip():
            raise ValueError("reason required (UCW quality gate)")
        cur = self.conn.execute(
            """INSERT INTO facts(
                   scope, predicate, subject, object, reason, contextual_prefix,
                   source_session, confidence, created_at, ttl_seconds, pinned
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scope, predicate, subject, object_, reason, contextual_prefix,
                source_session, confidence, int(time.time()), ttl_seconds,
                1 if pinned else 0,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def pin(self, fact_id: int) -> None:
        self.conn.execute("UPDATE facts SET pinned = 1 WHERE id = ?", (fact_id,))
        self.conn.commit()

    def forget(self, fact_id: int) -> None:
        """Soft delete — keeps the row for audit but excludes from retrieval."""
        self.conn.execute("UPDATE facts SET deleted = 1 WHERE id = ?", (fact_id,))
        self.conn.commit()

    def expire(self, now: int | None = None) -> int:
        """Drop unpinned facts past their TTL. Returns count affected."""
        now = now if now is not None else int(time.time())
        cur = self.conn.execute(
            """UPDATE facts
                  SET deleted = 1
                WHERE deleted = 0
                  AND pinned = 0
                  AND ttl_seconds IS NOT NULL
                  AND (created_at + ttl_seconds) < ?""",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount

    # ---- reads --------------------------------------------------------------

    def by_id(self, fact_id: int) -> Fact | None:
        row = self.conn.execute(
            "SELECT * FROM facts WHERE id = ? AND deleted = 0", (fact_id,)
        ).fetchone()
        return _row_to_fact(row) if row else None

    def pinned_facts(self, scope: str | None = None) -> list[Fact]:
        if scope:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE deleted = 0 AND pinned = 1 AND scope = ? ORDER BY created_at DESC",
                (scope,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE deleted = 0 AND pinned = 1 ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def fts_search(self, query: str, *, limit: int = 30, scope: str | None = None) -> list[tuple[Fact, float]]:
        """BM25 search via FTS5. Returns (fact, rank_score) pairs.

        The FTS5 `rank` column is negative (more negative = better); we flip it
        so higher is better, matching the vector-side convention.
        """
        if not query.strip():
            return []
        # Build a safe MATCH query: split into tokens, escape doublequotes, OR them.
        tokens = [t for t in _tokenize(query) if t]
        if not tokens:
            return []
        match_expr = " OR ".join(f'"{t}"' for t in tokens)
        scope_clause = "AND f.scope = ?" if scope else ""
        params: list = [match_expr]
        if scope:
            params.append(scope)
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT f.*, fts.rank AS rank
                  FROM facts_fts fts
                  JOIN facts f ON f.id = fts.rowid
                 WHERE facts_fts MATCH ?
                   AND f.deleted = 0
                   {scope_clause}
                 ORDER BY fts.rank
                 LIMIT ?""",
            params,
        ).fetchall()
        return [(_row_to_fact(r), -float(r["rank"])) for r in rows]

    def list_facts(self, scope: str | None = None, limit: int = 100, offset: int = 0) -> list[Fact]:
        scope_clause = "WHERE deleted = 0 AND scope = ?" if scope else "WHERE deleted = 0"
        params: list = [scope] if scope else []
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"SELECT * FROM facts {scope_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def stats(self) -> dict:
        rows = self.conn.execute(
            """SELECT scope, COUNT(*) AS n, SUM(pinned) AS pinned
                 FROM facts WHERE deleted = 0 GROUP BY scope"""
        ).fetchall()
        return {
            "by_scope": [dict(r) for r in rows],
            "total": sum(r["n"] for r in rows),
            "pinned": sum((r["pinned"] or 0) for r in rows),
            "db_path": str(self.db_path),
        }


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        scope=row["scope"],
        predicate=row["predicate"],
        subject=row["subject"],
        object=row["object"],
        reason=row["reason"],
        contextual_prefix=row["contextual_prefix"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        ttl_seconds=row["ttl_seconds"],
        pinned=bool(row["pinned"]),
    )


def _tokenize(query: str) -> Iterable[str]:
    """Cheap tokenizer for FTS5 MATCH. Keep alphanumerics + hyphens."""
    import re
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_\-]+", query) if len(t) >= 2]
