"""Tests for the instinct distiller."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_distill_instincts", REPO_ROOT / "bin" / "ucw-distill-instincts.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_distill_instincts"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_object_token_strips_stopwords():
    mod = _load()
    assert mod._object_token("a postgres") == "postgres"
    assert mod._object_token("the redis cache") == "redis"
    assert mod._object_token("") is None


def test_slug_handles_punctuation():
    mod = _load()
    assert mod._slug("We chose to use Postgres 16!") == "we-chose-to-use-postgres-16"
    assert mod._slug("") == "instinct"


def test_aggregate_groups_by_token(tmp_path):
    mod = _load()
    from ucw_memory.db import MemoryDB
    db_path = tmp_path / "memory.sqlite"
    with MemoryDB(db_path) as db:
        # Three facts about postgres across two sessions
        db.note(scope="project", subject="app", predicate="uses", object_="postgres 16",
                reason="ACID + JSONB", source_session="s1")
        db.note(scope="project", subject="api", predicate="uses", object_="postgres",
                reason="durability", source_session="s2")
        db.note(scope="project", subject="api", predicate="uses", object_="postgres",
                reason="JSONB", source_session="s2")
        # One unrelated
        db.note(scope="project", subject="cache", predicate="is", object_="redis",
                reason="low latency", source_session="s3")

        cands = mod.aggregate(db)

    # Postgres group should aggregate three uses across two sessions
    pg = next(c for c in cands if c["trigger"] == "postgres")
    assert pg["uses"] == 3
    assert pg["sessions"] == 2

    # Redis group has one use
    redis = next(c for c in cands if c["trigger"] == "redis")
    assert redis["uses"] == 1


def test_upsert_instincts_idempotent(tmp_path):
    mod = _load()
    from ucw_memory.db import MemoryDB
    db_path = tmp_path / "memory.sqlite"
    with MemoryDB(db_path) as db:
        for i in range(3):
            db.note(scope="project", subject="api", predicate="uses", object_="postgres",
                    reason=f"reason-{i}", source_session=f"s{i}")
        cands = mod.aggregate(db)
        ins1, upd1 = mod.upsert_instincts(db, cands, min_uses=2)
        ins2, upd2 = mod.upsert_instincts(db, cands, min_uses=2)
    assert ins1 == 1
    assert upd1 == 0
    assert ins2 == 0
    assert upd2 == 1


def test_draft_skill_files_writes_template(tmp_path):
    mod = _load()
    candidates = [{
        "pattern": "we use postgres for durability",
        "trigger": "postgres",
        "predicate": "uses",
        "uses": 5,
        "sessions": 3,
        "confidence": 0.8,
        "example_reasons": ["ACID + JSONB", "durability"],
        "fact_ids": [1, 2, 3, 4, 5],
    }]
    written = mod.draft_skill_files(candidates, repo_root=tmp_path, threshold=0.7)
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "we use postgres for durability" in text
    assert "ACID + JSONB" in text
    assert "{{" not in text  # no unsubstituted placeholders


def test_draft_skill_files_skips_below_threshold(tmp_path):
    mod = _load()
    candidates = [{
        "pattern": "low confidence pattern",
        "trigger": "low",
        "predicate": "is",
        "uses": 2,
        "sessions": 1,
        "confidence": 0.4,
        "example_reasons": ["weak"],
        "fact_ids": [1, 2],
    }]
    written = mod.draft_skill_files(candidates, repo_root=tmp_path, threshold=0.7)
    assert written == []


def test_end_to_end_via_cli(tmp_path, monkeypatch, capsys):
    """Smoke test invoking main() against a tmp project."""
    mod = _load()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()

    from ucw_memory.db import MemoryDB
    db_path = tmp_path / ".ucw" / "memory.sqlite"
    with MemoryDB(db_path) as db:
        for i in range(4):
            db.note(scope="project", subject="x", predicate="uses",
                    object_="postgres", reason=f"r-{i}", source_session=f"s{i}")

    rc = mod.main(["--repo", str(tmp_path), "--min-uses", "3", "--promote", "0.3", "--json"])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert rc == 0
    assert body["instincts_inserted"] >= 1
    assert len(body["skills_drafted"]) >= 1
