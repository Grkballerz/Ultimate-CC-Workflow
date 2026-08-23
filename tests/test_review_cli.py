"""End-to-end CLI tests for ucw-review.py.

These exercise the whole orchestrator pipeline minus the actual LLM calls —
the agents would invoke the same CLI commands that these tests do.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ucw_review", REPO_ROOT / "bin" / "ucw-review.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["ucw_review"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args, cwd: Path):
    subprocess.check_call(["git", *args], cwd=cwd, stdout=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git("init", "-b", "main", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@t.t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    _git("config", "commit.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    # second commit so HEAD~1 exists
    (tmp_path / "src" / "a.py").write_text("x = 1\ny = 2\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "second", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---- scope ------------------------------------------------------------------

def test_scope_returns_concerns_per_file(repo, capsys):
    mod = _load()
    rc = mod.main(["scope", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    # a.py should appear under multiple concerns
    files = {s["file"] for s in body["scopes"]}
    assert "src/a.py" in files
    concerns = {s["concern"] for s in body["scopes"] if s["file"] == "src/a.py"}
    assert "correctness" in concerns
    assert "injection" in concerns  # .py is in injection's glob list


def test_scope_persists_with_flag(repo, capsys):
    mod = _load()
    mod.main(["scope", "--repo", str(repo), "--persist"])
    capsys.readouterr()
    # the scope.json file should exist under .ucw/reviews/<sha>/
    reviews_dir = repo / ".ucw" / "reviews"
    assert reviews_dir.is_dir()
    sha_dirs = list(reviews_dir.iterdir())
    assert len(sha_dirs) == 1
    assert (sha_dirs[0] / "scope.json").exists()


def test_scope_persist_prints_compact_summary(repo, capsys):
    """--persist keeps the full scope list in the file, not on stdout."""
    mod = _load()
    rc = mod.main(["scope", "--repo", str(repo), "--persist"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "scopes" not in body  # full JSON stays in the persisted file
    assert body["scope_count"] > 0
    assert body["file_count"] > 0
    assert sum(body["lanes"].values()) == body["scope_count"]
    persisted = Path(body["persisted"])
    assert persisted.exists()
    on_disk = json.loads(persisted.read_text(encoding="utf-8"))
    assert len(on_disk["scopes"]) == body["scope_count"]


def test_scope_without_persist_still_dumps_full_scopes(repo, capsys):
    mod = _load()
    mod.main(["scope", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    assert body["scopes"]
    assert "persisted" not in body


def test_scope_steps_back_when_on_main(repo, capsys):
    """If current HEAD == main, fall back to HEAD~1 so diff isn't empty."""
    mod = _load()
    mod.main(["scope", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    assert body["scope_count"] > 0


def test_scope_with_explicit_since(repo, capsys):
    mod = _load()
    mod.main(["scope", "--repo", str(repo), "--since", "HEAD~1"])
    body = json.loads(capsys.readouterr().out)
    assert body["scope_count"] > 0


# ---- concern globs ----------------------------------------------------------

def test_code_lanes_exclude_markdown():
    """correctness/performance/tests must not fan out over review markdown."""
    mod = _load()
    for lane in ("correctness", "performance", "tests"):
        globs = mod.CONCERN_GLOBS[lane]
        assert not any(mod._glob_matches(g, "docs/review-report.md") for g in globs)
        assert not any(mod._glob_matches(g, "README.md") for g in globs)
        assert not any(mod._glob_matches(g, ".ucw/state/review-report.md") for g in globs)
        # but real source still matches
        assert any(mod._glob_matches(g, "src/a.py") for g in globs)
        assert any(mod._glob_matches(g, "web/app.tsx") for g in globs)
        assert any(mod._glob_matches(g, "scripts/deploy.sh") for g in globs)


def test_docs_lane_keeps_markdown():
    mod = _load()
    globs = mod.CONCERN_GLOBS["docs"]
    assert any(mod._glob_matches(g, "notes/review-report.md") for g in globs)
    assert any(mod._glob_matches(g, "README.md") for g in globs)


def test_scope_routes_markdown_to_docs_lane_only(repo, capsys):
    """End-to-end: a changed .md file lands in docs, not the code lanes."""
    (repo / "notes.md").write_text("# notes\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "add notes", cwd=repo)
    mod = _load()
    mod.main(["scope", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    concerns = {s["concern"] for s in body["scopes"] if s["file"] == "notes.md"}
    assert concerns == {"docs"}


# ---- add-finding ------------------------------------------------------------

def test_add_finding_via_json_arg(repo, capsys):
    mod = _load()
    rc = mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "major", "category": "correctness",
        "file": "src/a.py", "line": 5, "title": "x", "detail": "y",
        "finder_agent": "reviewer-correctness", "finder_model": "sonnet",
    })])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["id"].startswith("f-")
    assert out["effective_severity"] == "major"


def test_add_finding_rejects_unknown_severity(repo, capsys):
    mod = _load()
    with pytest.raises(ValueError):
        mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
            "severity": "bogus", "category": "correctness",
            "file": "x", "line": 1, "title": "t", "detail": "d",
        })])


def test_add_finding_rejects_unknown_category(repo, capsys):
    mod = _load()
    with pytest.raises(ValueError):
        mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
            "severity": "major", "category": "bogus",
            "file": "x", "line": 1, "title": "t", "detail": "d",
        })])


def test_add_finding_array_input(repo, capsys):
    mod = _load()
    payload = [
        {"severity": "major", "category": "correctness", "file": "a", "line": 1, "title": "t", "detail": "d"},
        {"severity": "minor", "category": "tests", "file": "b", "line": 2, "title": "t", "detail": "d"},
    ]
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps(payload)])
    body = json.loads(capsys.readouterr().out)
    assert len(body["added"]) == 2


# ---- disprove / reachability -----------------------------------------------

def test_disprove_flow(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "critical", "category": "injection",
        "file": "src/a.py", "line": 1, "title": "t", "detail": "d",
    })])
    fid = json.loads(capsys.readouterr().out)["id"]

    rc = mod.main(["disprove", "--repo", str(repo), fid, "refuted",
                   "--evidence", "false positive"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["verdict"] == "refuted"
    assert body["effective_severity"] == "minor"  # critical refuted → minor


def test_disprove_unknown_finding_returns_2(repo):
    mod = _load()
    rc = mod.main(["disprove", "--repo", str(repo), "f-nope", "refuted"])
    assert rc == 2


def test_disprove_invalid_verdict_rejected_by_argparse(repo):
    mod = _load()
    with pytest.raises(SystemExit):
        mod.main(["disprove", "--repo", str(repo), "f-x", "bogus"])


def test_reachability_flow(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "critical", "category": "injection",
        "file": "src/a.py", "line": 1, "title": "t", "detail": "d",
    })])
    fid = json.loads(capsys.readouterr().out)["id"]

    rc = mod.main(["reachability", "--repo", str(repo), fid, "unreachable",
                   "--evidence", "hardcoded"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["effective_severity"] == "major"  # critical injection + unreachable = major


# ---- dedup ------------------------------------------------------------------

def test_dedup_co_located_findings(repo, capsys):
    mod = _load()
    for title in ("shell exec", "command injection", "subprocess call"):
        mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
            "severity": "critical", "category": "injection",
            "file": "src/a.py", "line": 1, "title": title, "detail": "d",
        })])
        capsys.readouterr()

    rc = mod.main(["dedup", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["count"] == 2  # 3 in cluster → 2 merges into the winner


# ---- gate -------------------------------------------------------------------

def test_gate_clean_repo_exits_0(repo, capsys):
    mod = _load()
    rc = mod.main(["gate", "--repo", str(repo)])
    assert rc == 0


def test_gate_exits_2_on_unack_critical(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "critical", "category": "injection",
        "file": "x", "line": 1, "title": "t", "detail": "d",
    })])
    capsys.readouterr()
    rc = mod.main(["gate", "--repo", str(repo)])
    assert rc == 2


def test_gate_strict_exits_3_on_major(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "major", "category": "correctness",
        "file": "x", "line": 1, "title": "t", "detail": "d",
    })])
    capsys.readouterr()
    rc = mod.main(["gate", "--repo", str(repo), "--strict"])
    assert rc == 3


def test_gate_json_output(repo, capsys):
    mod = _load()
    mod.main(["gate", "--repo", str(repo), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert "by_severity" in body


# ---- lane receipts ----------------------------------------------------------

def test_lane_done_writes_receipt_under_review_dir(repo, capsys):
    mod = _load()
    rc = mod.main(["lane-done", "correctness", "--repo", str(repo)])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert body["lane"] == "correctness"
    assert body["sha"]
    sha_dirs = list((repo / ".ucw" / "reviews").iterdir())
    assert len(sha_dirs) == 1
    receipts = (sha_dirs[0] / "lane-receipts.jsonl").read_text()
    assert json.loads(receipts.splitlines()[0])["lane"] == "correctness"


def test_gate_expect_lanes_all_done_exits_0(repo, capsys):
    mod = _load()
    for lane in ("correctness", "injection"):
        mod.main(["lane-done", lane, "--repo", str(repo)])
        capsys.readouterr()
    rc = mod.main(["gate", "--repo", str(repo), "--expect-lanes", "correctness,injection"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 expected lane(s) reported done" in out


def test_gate_expect_lanes_missing_exits_3_and_names_them(repo, capsys):
    mod = _load()
    mod.main(["lane-done", "correctness", "--repo", str(repo)])
    capsys.readouterr()
    rc = mod.main(["gate", "--repo", str(repo),
                   "--expect-lanes", "correctness,injection,tests", "--json"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert body["missing_lanes"] == ["injection", "tests"]


def test_gate_expect_lanes_empty_store_fails(repo, capsys):
    """Zero receipts + zero findings must NOT look clean — dead reviewers."""
    mod = _load()
    rc = mod.main(["gate", "--repo", str(repo), "--expect-lanes", "correctness,injection"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "correctness" in out
    assert "injection" in out


def test_gate_unack_critical_outranks_missing_lanes(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "critical", "category": "injection",
        "file": "x", "line": 1, "title": "t", "detail": "d",
    })])
    capsys.readouterr()
    rc = mod.main(["gate", "--repo", str(repo), "--expect-lanes", "correctness"])
    capsys.readouterr()
    assert rc == 2


def test_gate_without_expect_lanes_ignores_receipts(repo, capsys):
    """No --expect-lanes → receipts play no part (back-compat)."""
    mod = _load()
    rc = mod.main(["gate", "--repo", str(repo), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "missing_lanes" not in body


# ---- approve ----------------------------------------------------------------

def test_approve_clears_gate(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "critical", "category": "injection",
        "file": "x", "line": 1, "title": "t", "detail": "d",
    })])
    fid = json.loads(capsys.readouterr().out)["id"]

    assert mod.main(["gate", "--repo", str(repo)]) == 2
    capsys.readouterr()

    rc = mod.main(["approve", "--repo", str(repo), fid,
                   "--reason", "false positive", "--actor", "alice"])
    capsys.readouterr()
    assert rc == 0
    assert mod.main(["gate", "--repo", str(repo)]) == 0


def test_approve_unknown_finding_returns_2(repo):
    mod = _load()
    rc = mod.main(["approve", "--repo", str(repo), "f-nope"])
    assert rc == 2


# ---- status / summary / list ------------------------------------------------

def test_status_matches_gate(repo, capsys):
    mod = _load()
    mod.main(["status", "--repo", str(repo), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["unack_critical"] == 0


def test_summary_markdown_renders(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "major", "category": "correctness",
        "file": "src/a.py", "line": 1, "title": "off by one", "detail": "details",
        "finder_agent": "reviewer-correctness", "finder_model": "sonnet",
    })])
    capsys.readouterr()
    mod.main(["summary", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "Review report" in out
    assert "off by one" in out
    assert "src/a.py" in out


def test_summary_json(repo, capsys):
    mod = _load()
    mod.main(["summary", "--repo", str(repo), "--format", "json"])
    body = json.loads(capsys.readouterr().out)
    assert "findings" in body
    assert "gate" in body
    assert body["pending_disprove"] == 0


def test_summary_counts_pending_disprove(repo, capsys):
    """Findings without a disprover verdict are surfaced as pending."""
    mod = _load()
    fids = []
    for i in range(2):
        mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
            "severity": "major", "category": "correctness",
            "file": "src/a.py", "line": i + 1, "title": f"t{i}", "detail": "d",
        })])
        fids.append(json.loads(capsys.readouterr().out)["id"])
    mod.main(["disprove", "--repo", str(repo), fids[0], "confirmed"])
    capsys.readouterr()

    mod.main(["summary", "--repo", str(repo), "--format", "json"])
    body = json.loads(capsys.readouterr().out)
    assert body["pending_disprove"] == 1

    mod.main(["summary", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "Pending disprove:** 1" in out


def test_list_empty(repo, capsys):
    mod = _load()
    rc = mod.main(["list", "--repo", str(repo), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_list_after_adds(repo, capsys):
    mod = _load()
    mod.main(["add-finding", "--repo", str(repo), "--json", json.dumps({
        "severity": "major", "category": "correctness",
        "file": "a", "line": 1, "title": "t", "detail": "d",
    })])
    capsys.readouterr()
    mod.main(["list", "--repo", str(repo), "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert "effective_severity" in rows[0]
    assert "approved" in rows[0]


# ---- glob matcher -----------------------------------------------------------

def test_glob_double_star():
    mod = _load()
    assert mod._glob_matches("**/*.py", "src/a.py")
    assert mod._glob_matches("**/*.py", "deep/nested/path/x.py")
    assert mod._glob_matches("src/**", "src/a.py")
    assert not mod._glob_matches("src/**", "other/a.py")


def test_glob_single_star_no_slash():
    mod = _load()
    assert mod._glob_matches("*.py", "a.py")
    assert not mod._glob_matches("*.py", "sub/a.py")


def test_glob_question_mark():
    mod = _load()
    assert mod._glob_matches("a?.py", "a1.py")
    assert not mod._glob_matches("a?.py", "ab1.py")
