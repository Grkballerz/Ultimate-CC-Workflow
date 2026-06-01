"""Verify the review agent files exist, parse, and have sensible scopes.

The agent files are markdown with YAML frontmatter. We don't test their
LLM behavior (that requires keys + costs money) — we test that:

- All 9 narrow reviewers exist
- disprover + reachability agents exist
- Each has valid frontmatter (name, description, tools, model)
- Reviewers are scoped to a single concern in their name
- The disprover uses a different model from the security finders
  (cross-audit requires a different model by design)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"


CONCERNS = (
    "correctness", "injection", "deserialization", "auth",
    "performance", "data-loss", "api-compat", "tests", "docs",
)


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path} missing frontmatter"
    end = text.find("\n---", 4)
    assert end > 0, f"{path} unclosed frontmatter"
    front = text[4:end]
    meta = {}
    for line in front.splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def test_all_reviewer_files_exist():
    for concern in CONCERNS:
        path = AGENTS_DIR / "reviewers" / f"{concern}.md"
        assert path.exists(), f"missing reviewer: {concern}"


def test_disprover_and_reachability_exist():
    assert (AGENTS_DIR / "disprover.md").exists()
    assert (AGENTS_DIR / "reachability.md").exists()


def test_reviewer_names_match_concerns():
    for concern in CONCERNS:
        meta = _parse_frontmatter(AGENTS_DIR / "reviewers" / f"{concern}.md")
        assert meta.get("name") == f"reviewer-{concern}"


def test_reviewers_have_required_metadata():
    for concern in CONCERNS:
        meta = _parse_frontmatter(AGENTS_DIR / "reviewers" / f"{concern}.md")
        for key in ("name", "description", "tools", "model"):
            assert key in meta, f"{concern}.md missing {key}"


def test_reviewers_use_sonnet():
    for concern in CONCERNS:
        meta = _parse_frontmatter(AGENTS_DIR / "reviewers" / f"{concern}.md")
        assert meta["model"] == "sonnet", \
            f"reviewer-{concern} should use sonnet (model={meta['model']})"


def test_disprover_uses_different_model_from_reviewers():
    """Cross-audit demands a different model from the original finder."""
    disprover = _parse_frontmatter(AGENTS_DIR / "disprover.md")
    sample_reviewer = _parse_frontmatter(AGENTS_DIR / "reviewers" / "injection.md")
    assert disprover["model"] != sample_reviewer["model"], \
        "disprover MUST use a different model from reviewers (cross-audit invariant)"


def test_reachability_has_required_metadata():
    meta = _parse_frontmatter(AGENTS_DIR / "reachability.md")
    for key in ("name", "description", "tools", "model"):
        assert key in meta


def test_security_reviewers_mention_will_be_challenged():
    """Security-class reviewers should signal they'll be cross-audited."""
    for concern in ("injection", "deserialization", "auth"):
        path = AGENTS_DIR / "reviewers" / f"{concern}.md"
        meta = _parse_frontmatter(path)
        assert "security" in meta.get("description", "").lower(), \
            f"reviewer-{concern} should mark itself as security-class in description"


def test_disprover_documents_no_new_findings_rule():
    """The 'cannot generate new findings' rule is the whole point of disprover."""
    text = (AGENTS_DIR / "disprover.md").read_text(encoding="utf-8")
    assert "cannot generate new findings" in text.lower() or \
           "cannot generate new" in text.lower(), \
           "disprover.md must explicitly forbid generating new findings"


def test_reachability_documents_chain_split():
    """The reachability agent exists for chain splitting."""
    text = (AGENTS_DIR / "reachability.md").read_text(encoding="utf-8")
    assert "chain split" in text.lower() or "reachable" in text.lower()


def test_all_reviewers_describe_their_scope_narrowly():
    """Each reviewer should explicitly say it only handles its concern."""
    for concern in CONCERNS:
        path = AGENTS_DIR / "reviewers" / f"{concern}.md"
        text = path.read_text(encoding="utf-8")
        assert concern in text.lower(), \
            f"reviewer-{concern}.md doesn't mention its own concern"


def test_ucw_dispatcher_exists():
    """commands/ucw.md is the umbrella that dispatches every subcommand."""
    assert (REPO_ROOT / "commands" / "ucw.md").exists()


def test_review_subcommand_handles_bare_and_full():
    """`/ucw review` (no args) and `/ucw review --full` must run the full pipeline."""
    text = (REPO_ROOT / "commands" / "ucw.md").read_text(encoding="utf-8")
    # Look for explicit instruction that empty args == full inside the review section
    assert re.search(r"empty.*--full|--full.*empty|Empty args OR `--full`", text, re.IGNORECASE) is not None, \
        "commands/ucw.md must document that bare `/ucw review` triggers the full pipeline"


def test_review_subcommand_references_real_bins():
    text = (REPO_ROOT / "commands" / "ucw.md").read_text(encoding="utf-8")
    assert "ucw-review.py" in text


def test_review_subcommand_references_disprover_and_reachability():
    text = (REPO_ROOT / "commands" / "ucw.md").read_text(encoding="utf-8")
    assert "disprover" in text.lower()
    assert "reachability" in text.lower()


def test_review_subcommand_references_all_reviewers():
    text = (REPO_ROOT / "commands" / "ucw.md").read_text(encoding="utf-8")
    for concern in CONCERNS:
        assert f"reviewer-{concern}" in text, \
            f"commands/ucw.md doesn't reference reviewer-{concern}"
