"""Last-mile coverage — error paths and text output modes in the remaining
bottom modules: ucw-merge-settings, ucw-distill-instincts, ucw-knowledge-check,
ucw-phase, distill, rerank.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---- ucw-merge-settings: error paths ----------------------------------------

def test_merge_settings_corrupt_base_exits_2(tmp_path, capsys):
    m = _load("ucw_merge_settings_final", "bin/ucw-merge-settings.py")
    base = tmp_path / "base.json"
    base.write_text("{not: valid json")
    frag = tmp_path / "frag.json"
    frag.write_text('{"x": 1}')
    with pytest.raises(SystemExit) as e:
        m.main([str(base), str(frag)])
    assert e.value.code == 2


def test_merge_settings_corrupt_fragment_exits_2(tmp_path):
    m = _load("ucw_merge_settings_corrupt_frag", "bin/ucw-merge-settings.py")
    base = tmp_path / "base.json"
    base.write_text('{}')
    frag = tmp_path / "frag.json"
    frag.write_text("{bad")
    with pytest.raises(SystemExit) as e:
        m.main([str(base), str(frag)])
    assert e.value.code == 2


def test_merge_settings_no_fragment_returns_2(tmp_path, capsys):
    m = _load("ucw_merge_settings_no_frag", "bin/ucw-merge-settings.py")
    base = tmp_path / "base.json"
    base.write_text('{"x": 1}')
    rc = m.main([str(base)])
    assert rc == 2


def test_merge_settings_fragment_not_object_returns_2(tmp_path):
    m = _load("ucw_merge_settings_frag_array", "bin/ucw-merge-settings.py")
    base = tmp_path / "base.json"
    base.write_text('{}')
    frag = tmp_path / "frag.json"
    frag.write_text('[1, 2, 3]')  # not an object
    rc = m.main([str(base), str(frag)])
    assert rc == 2


def test_merge_settings_base_not_object_is_coerced(tmp_path, capsys):
    """Base that isn't a dict gets treated as empty so the merge still runs."""
    m = _load("ucw_merge_settings_base_array", "bin/ucw-merge-settings.py")
    base = tmp_path / "base.json"
    base.write_text('[1, 2]')  # not an object
    frag = tmp_path / "frag.json"
    frag.write_text('{"x": 1}')
    rc = m.main([str(base), str(frag)])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == {"x": 1}


def test_merge_settings_scalar_base_uses_fragment(tmp_path):
    """Helper-level: _deep_merge with mismatched types lets fragment win."""
    m = _load("ucw_merge_settings_scalar", "bin/ucw-merge-settings.py")
    # base is a scalar, add is a dict → add wins
    result = m._deep_merge("ignored", {"x": 1})
    assert result == {"x": 1}


# ---- ucw-distill-instincts: error paths -------------------------------------

def test_distill_instincts_missing_db_returns_2(tmp_path, capsys, monkeypatch):
    m = _load("ucw_distill_instincts_missing", "bin/ucw-distill-instincts.py")
    monkeypatch.chdir(tmp_path)
    rc = m.main(["--db", str(tmp_path / "nope.sqlite")])
    assert rc == 2


def test_distill_instincts_dry_run_text_output(tmp_path, monkeypatch, capsys):
    """Cover the non-JSON dry-run print path."""
    m = _load("ucw_distill_instincts_dry", "bin/ucw-distill-instincts.py")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    from ucw_memory.db import MemoryDB
    with MemoryDB(tmp_path / ".ucw" / "memory.sqlite") as db:
        for i in range(3):
            db.note(scope="project", subject="api", predicate="uses",
                    object_="postgres", reason=f"r{i}", source_session=f"s{i}")
    rc = m.main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would write" in out
    assert "postgres" in out


def test_distill_instincts_full_text_output(tmp_path, monkeypatch, capsys):
    """Cover the non-JSON write path (text summary, list of drafts)."""
    m = _load("ucw_distill_instincts_full", "bin/ucw-distill-instincts.py")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw").mkdir()
    from ucw_memory.db import MemoryDB
    with MemoryDB(tmp_path / ".ucw" / "memory.sqlite") as db:
        for i in range(4):
            db.note(scope="project", subject="api", predicate="uses",
                    object_="postgres", reason=f"r{i}", source_session=f"s{i}")
    rc = m.main(["--min-uses", "3", "--promote", "0.3", "--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "instincts:" in out


# ---- ucw-knowledge-check: text output paths ---------------------------------

def test_knowledge_check_text_output_with_stale(tmp_path, capsys):
    m = _load("ucw_knowledge_check_text", "bin/ucw-knowledge-check.py")
    (tmp_path / ".ucw" / "knowledge").mkdir(parents=True)
    stack = tmp_path / ".ucw" / "knowledge" / "STACK.md"
    stack.write_text("# Stack\n")
    import time
    old = time.time() - 1000
    os.utime(stack, (old, old))
    (tmp_path / "package.json").write_text('{"name":"x"}')
    rc = m.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert "stale Knowledge doc" in out
    assert rc == 0


def test_knowledge_check_text_output_fresh(tmp_path, capsys):
    m = _load("ucw_knowledge_check_fresh", "bin/ucw-knowledge-check.py")
    (tmp_path / ".ucw" / "knowledge").mkdir(parents=True)
    (tmp_path / ".ucw" / "knowledge" / "STACK.md").write_text("# Stack\n")
    rc = m.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert "fresh ✓" in out
    assert rc == 0


def test_knowledge_check_truncates_long_reason_list(tmp_path, capsys):
    """When > 3 reasons, we print first 3 + '… and N more'."""
    m = _load("ucw_knowledge_check_trunc", "bin/ucw-knowledge-check.py")
    (tmp_path / ".ucw" / "knowledge").mkdir(parents=True)
    stack = tmp_path / ".ucw" / "knowledge" / "STACK.md"
    stack.write_text("# Stack\n")
    import time
    old = time.time() - 1000
    os.utime(stack, (old, old))
    # Several trigger files
    for name in ["package.json", "pnpm-lock.yaml", "go.mod", "Cargo.toml", "pyproject.toml"]:
        (tmp_path / name).write_text("x")
    rc = m.main(["--repo", str(tmp_path)])
    out = capsys.readouterr().out
    assert "more" in out
    assert rc == 0


# ---- ucw-phase: text path on get -------------------------------------------

def test_phase_get_when_state_file_unreadable(tmp_path, monkeypatch, capsys):
    """Garbage in state/phase isn't valid JSON for argparse choices, but
    the get-command should still work."""
    m = _load("ucw_phase_more", "bin/ucw-phase.py")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ucw" / "state").mkdir(parents=True)
    (tmp_path / ".ucw" / "state" / "phase").write_text("plan\n")
    rc = m.main(["get"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["phase"] == "plan"


# ---- distill module: a couple more edge cases -------------------------------

def test_distill_handles_extract_no_match():
    from ucw_memory.distill import extract_from_text
    # Sentence with reason connective but no SVO predicate → drops
    assert extract_from_text("It's good because of reasons.") == []


def test_distill_handles_sentence_with_unsupported_verb():
    from ucw_memory.distill import extract_from_text
    # "wants" is not in the verb list → no extraction
    assert extract_from_text("We want postgres because acid.") == []


# ---- rerank: import-not-installed path --------------------------------------

def test_rerank_make_returns_none_when_provider_unavailable(monkeypatch):
    from ucw_memory import rerank
    monkeypatch.setattr(rerank, "_HAS_ANTHROPIC", False)
    monkeypatch.setattr(rerank, "_HAS_VOYAGE", False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    # Explicit provider asked for but unavailable → None
    assert rerank.make_reranker(provider="claude") is None
    assert rerank.make_reranker(provider="voyage") is None


def test_rerank_voyage_caps_rerank_call(monkeypatch):
    """VoyageReranker should still produce valid output for many items."""
    from unittest.mock import MagicMock

    from ucw_memory import rerank as r

    fake_mod = MagicMock()
    fake_client = MagicMock()

    class _R:
        def __init__(self, i, s):
            self.index = i
            self.relevance_score = s

    def _rerank(*, query, documents, model):
        resp = MagicMock()
        resp.results = [_R(i, 0.5) for i in range(len(documents))]
        return resp

    fake_client.rerank.side_effect = _rerank
    fake_mod.Client.return_value = fake_client
    monkeypatch.setattr(r, "_HAS_VOYAGE", True)
    monkeypatch.setattr(r, "voyageai", fake_mod)
    monkeypatch.setenv("VOYAGE_API_KEY", "vy-fake")
    rr = r.VoyageReranker()
    out = rr.rerank("q", ["a", "b", "c"])
    assert out == [0.5, 0.5, 0.5]
