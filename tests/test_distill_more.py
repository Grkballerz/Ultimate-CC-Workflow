"""Additional distillation coverage — JSON parse failure, malformed events,
nested content."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ucw_memory.distill import extract_from_text, extract_from_transcript


def test_extract_handles_no_text_in_content_list():
    """Content blocks that aren't `type: "text"` should be ignored."""
    # Pass through the higher-level transcript walker
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.jsonl"
        path.write_text(json.dumps({
            "message": {"content": [
                {"type": "tool_use", "input": {"x": 1}},
                {"type": "text", "text": "We use redis because it's cached."},
            ]}
        }))
        cands = extract_from_transcript(path)
        assert any("redis" in c.object.lower() for c in cands)


def test_extract_handles_malformed_jsonl_line():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.jsonl"
        path.write_text("\n".join([
            "not-json",                                      # malformed
            "",                                              # blank
            json.dumps({"message": {"content": "We use bun because it's fast."}}),
            "{",                                             # truncated
        ]))
        cands = extract_from_transcript(path)
        assert any("bun" in c.object.lower() for c in cands)


def test_extract_handles_nonexistent_transcript():
    cands = extract_from_transcript(Path("/does/not/exist.jsonl"))
    assert cands == []


def test_extract_ignores_message_without_content():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.jsonl"
        path.write_text("\n".join([
            json.dumps({"message": {}}),
            json.dumps({"random": "thing"}),
            json.dumps({"message": {"content": None}}),
        ]))
        assert extract_from_transcript(path) == []


def test_extract_handles_content_dict_not_list():
    """`content` may be a string OR list — the walker should handle both."""
    cands = extract_from_text("We chose to use rust because it's safe.")
    assert any("rust" in c.object.lower() for c in cands)


def test_candidate_as_dict_round_trip():
    cands = extract_from_text("The user prefers pnpm because of lockfile size.")
    assert cands
    d = cands[0].as_dict()
    assert "subject" in d
    assert "predicate" in d
    assert "object" in d
    assert "reason" in d
    assert "confidence" in d


def test_extract_handles_empty_content_block():
    """A type:text block with empty text shouldn't crash extraction."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.jsonl"
        path.write_text(json.dumps({
            "message": {"content": [{"type": "text", "text": ""}]}
        }))
        assert extract_from_transcript(path) == []
