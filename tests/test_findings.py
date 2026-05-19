"""Tests for the findings store — schema, persistence, effective severity, dedup."""
from __future__ import annotations

from ucw_memory.findings import (
    Finding,
    ReviewStore,
    _effective_severity,
    find_duplicates,
    head_sha,
    jaccard,
    normalized_text,
    shingle_hash,
)

# ---- schema -----------------------------------------------------------------

def test_finding_round_trip(tmp_path):
    store = ReviewStore(tmp_path, "abc123")
    f = store.add(Finding(
        id="", severity="critical", category="injection",
        file="src/a.py", line=42, title="t", detail="d",
        finder_agent="reviewer-injection", finder_model="sonnet",
    ))
    assert f.id.startswith("f-")
    loaded = store.get(f.id)
    assert loaded is not None
    assert loaded.severity == "critical"
    assert loaded.file == "src/a.py"


def test_store_returns_only_latest_per_id(tmp_path):
    store = ReviewStore(tmp_path, "sha1")
    f = store.add(Finding(id="", severity="major", category="correctness",
                           file="x.py", line=1, title="t", detail="d"))
    store.update(f.id, severity="critical")
    rows = store.all()
    assert len(rows) == 1
    assert rows[0].severity == "critical"


def test_duplicate_findings_hidden_from_all(tmp_path):
    store = ReviewStore(tmp_path, "sha2")
    a = store.add(Finding(id="", severity="major", category="tests",
                           file="t.py", line=1, title="A", detail=""))
    b = store.add(Finding(id="", severity="major", category="tests",
                           file="t.py", line=1, title="B", detail=""))
    store.update(b.id, duplicate_of=a.id)
    rows = store.all()
    assert len(rows) == 1
    assert rows[0].id == a.id


# ---- effective severity -----------------------------------------------------

def test_severity_unchanged_when_no_verdicts():
    f = Finding(id="x", severity="major", category="correctness",
                 file="a", line=1, title="t", detail="d")
    assert _effective_severity(f) == "major"


def test_refuted_drops_two_levels():
    f = Finding(id="x", severity="critical", category="correctness",
                 file="a", line=1, title="t", detail="d",
                 disprover_verdict="refuted")
    assert _effective_severity(f) == "minor"


def test_refuted_clamped_at_nit():
    f = Finding(id="x", severity="minor", category="correctness",
                 file="a", line=1, title="t", detail="d",
                 disprover_verdict="refuted")
    assert _effective_severity(f) == "nit"


def test_needs_human_keeps_severity():
    f = Finding(id="x", severity="critical", category="injection",
                 file="a", line=1, title="t", detail="d",
                 disprover_verdict="needs-human")
    assert _effective_severity(f) == "critical"


def test_unreachable_security_drops_one_level():
    f = Finding(id="x", severity="critical", category="injection",
                 file="a", line=1, title="t", detail="d",
                 reachability_verdict="unreachable")
    assert _effective_severity(f) == "major"


def test_unreachable_non_security_unchanged():
    """Reachability only applies to security categories."""
    f = Finding(id="x", severity="critical", category="performance",
                 file="a", line=1, title="t", detail="d",
                 reachability_verdict="unreachable")
    assert _effective_severity(f) == "critical"


def test_refuted_and_unreachable_combine():
    f = Finding(id="x", severity="critical", category="injection",
                 file="a", line=1, title="t", detail="d",
                 disprover_verdict="refuted",
                 reachability_verdict="unreachable")
    # critical → drop 2 (refuted) → minor → drop 1 (unreachable) → nit
    assert _effective_severity(f) == "nit"


def test_unknown_severity_falls_back_to_minor():
    f = Finding(id="x", severity="bogus", category="correctness",
                 file="a", line=1, title="t", detail="d")
    assert _effective_severity(f) == "minor"


# ---- approvals --------------------------------------------------------------

def test_approve_finding(tmp_path):
    store = ReviewStore(tmp_path, "sha3")
    f = store.add(Finding(id="", severity="critical", category="injection",
                           file="a.py", line=1, title="t", detail="d"))
    assert not store.is_approved(f.id)
    store.approve(f.id, actor="alice", reason="hardcoded value")
    assert store.is_approved(f.id)
    approvals = store.approvals()
    assert len(approvals) == 1
    assert approvals[0].actor == "alice"


def test_approvals_persist_across_store_instances(tmp_path):
    s1 = ReviewStore(tmp_path, "sha4")
    f = s1.add(Finding(id="", severity="major", category="correctness",
                        file="a", line=1, title="t", detail="d"))
    s1.approve(f.id, "bob", "ok")
    s2 = ReviewStore(tmp_path, "sha4")  # re-open
    assert s2.is_approved(f.id)


# ---- gate -------------------------------------------------------------------

def test_gate_clean_when_no_findings(tmp_path):
    store = ReviewStore(tmp_path, "sha5")
    g = store.gate_summary()
    assert g["unack_critical"] == 0
    assert g["total"] == 0


def test_gate_flags_unacked_critical(tmp_path):
    store = ReviewStore(tmp_path, "sha6")
    f = store.add(Finding(id="", severity="critical", category="injection",
                           file="a", line=1, title="t", detail="d"))
    g = store.gate_summary()
    assert g["unack_critical"] == 1
    assert f.id in g["unack_critical_ids"]


def test_gate_clears_after_approval(tmp_path):
    store = ReviewStore(tmp_path, "sha7")
    f = store.add(Finding(id="", severity="critical", category="injection",
                           file="a", line=1, title="t", detail="d"))
    store.approve(f.id, "alice", "ok")
    assert store.gate_summary()["unack_critical"] == 0


def test_gate_uses_effective_severity(tmp_path):
    store = ReviewStore(tmp_path, "sha8")
    f = store.add(Finding(id="", severity="critical", category="injection",
                           file="a", line=1, title="t", detail="d"))
    store.update(f.id, disprover_verdict="refuted")
    g = store.gate_summary()
    # critical refuted → effective minor; no unack critical
    assert g["unack_critical"] == 0
    assert g["by_severity"]["critical"] == 0
    assert g["by_severity"]["minor"] == 1


# ---- dedup ------------------------------------------------------------------

def test_dedup_same_location_same_category(tmp_path):
    a = Finding(id="f-aaa", severity="critical", category="injection",
                 file="x.py", line=1, title="shell exec", detail="d")
    b = Finding(id="f-bbb", severity="critical", category="injection",
                 file="x.py", line=1, title="command injection", detail="d")
    pairs = find_duplicates([a, b])
    assert len(pairs) == 1
    loser, winner = pairs[0]
    # Lowest id wins
    assert winner.id == "f-aaa"
    assert loser.id == "f-bbb"


def test_dedup_skips_when_locations_differ(tmp_path):
    a = Finding(id="f-aaa", severity="major", category="injection",
                 file="x.py", line=1, title="t", detail="d")
    b = Finding(id="f-bbb", severity="major", category="injection",
                 file="x.py", line=99, title="t", detail="d")
    assert find_duplicates([a, b]) == []


def test_dedup_skips_when_categories_differ(tmp_path):
    a = Finding(id="f-aaa", severity="major", category="injection",
                 file="x.py", line=1, title="t", detail="d")
    b = Finding(id="f-bbb", severity="major", category="correctness",
                 file="x.py", line=1, title="t", detail="d")
    assert find_duplicates([a, b]) == []


def test_dedup_threshold_one_requires_identical_titles():
    a = Finding(id="f-aaa", severity="major", category="injection",
                 file="x.py", line=1, title="alpha beta gamma", detail="d")
    b = Finding(id="f-bbb", severity="major", category="injection",
                 file="x.py", line=1, title="alpha beta gamma", detail="d")
    c = Finding(id="f-ccc", severity="major", category="injection",
                 file="x.py", line=1, title="zeta eta theta", detail="d")
    pairs = find_duplicates([a, b, c], threshold=1.0)
    # Only a+b are title-identical; c is co-located but different title
    loser_ids = {loser.id for loser, _ in pairs}
    assert "f-bbb" in loser_ids
    assert "f-ccc" not in loser_ids


def test_dedup_default_threshold_merges_all_co_located():
    a = Finding(id="f-a", severity="major", category="injection",
                 file="x", line=1, title="A", detail="")
    b = Finding(id="f-b", severity="major", category="injection",
                 file="x", line=1, title="B", detail="")
    c = Finding(id="f-c", severity="major", category="injection",
                 file="x", line=1, title="C", detail="")
    pairs = find_duplicates([a, b, c])
    assert len(pairs) == 2  # b and c both merge into a


# ---- similarity helpers -----------------------------------------------------

def test_shingle_hash_short_input_returns_full():
    assert shingle_hash("ab") == {"ab"}


def test_jaccard_identical_is_one():
    s = shingle_hash("hello world")
    assert jaccard(s, s) == 1.0


def test_jaccard_disjoint_is_zero():
    assert jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_both_is_one():
    assert jaccard(set(), set()) == 1.0


def test_jaccard_empty_one_side_is_zero():
    assert jaccard(set(), {"x"}) == 0.0


def test_normalized_text_lowercases():
    f = Finding(id="x", severity="critical", category="Injection",
                 file="X.py", line=1, title="ShEll", detail="")
    assert "injection" in normalized_text(f)
    assert "shell" in normalized_text(f)


# ---- head_sha helper --------------------------------------------------------

def test_head_sha_falls_back_when_no_git(tmp_path):
    """In a non-git dir, head_sha returns a deterministic content-based id."""
    sha = head_sha(tmp_path)
    assert sha.startswith("nogit-")
    assert sha == head_sha(tmp_path)  # deterministic


def test_head_sha_returns_real_sha_in_git_repo(tmp_path):
    import subprocess
    subprocess.check_call(["git", "init", "-b", "main", "-q"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.email", "a@b.c"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "user.name", "a"], cwd=tmp_path)
    subprocess.check_call(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path)
    (tmp_path / "x").write_text("y")
    subprocess.check_call(["git", "add", "."], cwd=tmp_path)
    subprocess.check_call(["git", "commit", "-qm", "init"], cwd=tmp_path)
    sha = head_sha(tmp_path)
    assert len(sha) == 40  # full git sha
    assert not sha.startswith("nogit-")
