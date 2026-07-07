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


# ---- subagents must not be told to call MCP tools they don't have -----------
# Subagents only get the tools in their frontmatter `tools:` list. An agent
# body that instructs "call mcp__ucw-memory__memory.init" while its frontmatter
# grants no MCP tools is structurally impossible — the step silently never
# happens (real bug: /ucw init left .ucw/memory.sqlite uncreated because the
# onboarder can't reach the MCP server). The repo uses TWO spellings for these
# tools — fully-qualified (`mcp__ucw-memory__memory.recall`) and shorthand
# (`memory.recall(goal)`) — so the guard matches both. Mentions are allowed
# only on lines that NEGATE the call ("you have no MCP tools — never attempt
# ..."), hand it to the main session, or invoke the CLI binary instead
# (matched by its path, `venv/bin/ucw-memory`, NOT the bare name — the bare
# name is a substring of `mcp__ucw-memory__...` and would self-exempt).

_MCP_REF = re.compile(r"mcp__[\w-]+__[\w.*-]+")
_MEMORY_SHORTHAND_REF = re.compile(
    r"\bmemory\.(recall|note|pin|forget|list|stats|init|merge|expire|distill)\b"
)
_NEGATION_MARKERS = (
    "never", "no mcp", "not ", "main session", "only reachable",
    "venv/bin/ucw-memory",
)


def _agent_files():
    yield from AGENTS_DIR.glob("*.md")
    yield from (AGENTS_DIR / "reviewers").glob("*.md")


def test_agents_never_instructed_to_call_unavailable_mcp_tools():
    for path in _agent_files():
        meta = _parse_frontmatter(path)
        tools = meta.get("tools", "")
        if "mcp__" in tools or tools.strip() in ("*", "'*'", '"*"'):
            continue  # agent legitimately has MCP access
        body = path.read_text(encoding="utf-8")
        in_fence = False
        for i, line in enumerate(body.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                # Fenced blocks are illustrative examples (sample output,
                # sample findings) — not instructions to the agent. All the
                # real bugs this guards against were prose instructions.
                continue
            if not (_MCP_REF.search(line) or _MEMORY_SHORTHAND_REF.search(line)):
                continue
            low = line.lower()
            assert any(m in low for m in _NEGATION_MARKERS), (
                f"{path.name}:{i} references an MCP tool but the agent's "
                f"frontmatter grants no MCP tools — a subagent can't call it. "
                f"Use the ucw-memory CLI via Bash, or defer to the main "
                f"session. Line: {line.strip()!r}"
            )


def test_onboarder_inits_memory_via_cli():
    """Regression: /ucw init must produce .ucw/memory.sqlite. The onboarder
    has no MCP tools, so the init must go through the ucw-memory CLI."""
    body = (AGENTS_DIR / "onboarder.md").read_text(encoding="utf-8")
    assert "ucw-memory" in body and "init" in body
    assert "by calling `mcp__ucw-memory__memory.init`" not in body


def test_repo_oracle_recalls_memory_via_cli():
    body = (AGENTS_DIR / "repo-oracle.md").read_text(encoding="utf-8")
    assert "ucw-memory" in body and "recall" in body
