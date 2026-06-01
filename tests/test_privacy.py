"""Edge cases for the privacy stripper."""
from __future__ import annotations

from ucw_memory.privacy import contains_private, strip_private


def test_no_tags_returns_original():
    assert strip_private("hello world") == "hello world"


def test_empty_string():
    assert strip_private("") == ""


def test_unclosed_tag_left_intact():
    # We don't try to be clever; only well-formed pairs are stripped.
    assert "<private>still here" in strip_private("a <private>still here")


def test_multiple_tags_in_one_string():
    text = "a <secret>x</secret> b <private>y</private> c"
    out = strip_private(text)
    assert "x" not in out
    assert "y" not in out
    assert "a" in out
    assert "b" in out
    assert "c" in out


def test_case_insensitive():
    assert "x" not in strip_private("<SECRET>x</SECRET>")
    assert "y" not in strip_private("<Private>y</Private>")


def test_contains_private():
    assert contains_private("<secret>x</secret>")
    assert contains_private("a <private>x</private> b")
    assert contains_private("<no-memory>x</no-memory>")
    assert not contains_private("plain text")


def test_dotall_multiline():
    text = """before
<secret>
multi
line
content
</secret>
after"""
    out = strip_private(text)
    assert "multi" not in out
    assert "content" not in out
    assert "before" in out
    assert "after" in out
