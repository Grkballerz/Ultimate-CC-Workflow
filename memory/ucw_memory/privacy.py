"""Strip privacy-marked regions before any text goes near the distiller.

Tags supported (case-insensitive):
    <secret>...</secret>      — never write to disk, scrub from logs too
    <private>...</private>    — exclude from distillation
    <no-memory>...</no-memory> — synonym for <private>

The tags themselves are dropped from the output along with their content.
"""
from __future__ import annotations

import re

_PRIVACY_TAGS = ("secret", "private", "no-memory")
_PATTERNS = [
    re.compile(rf"<{tag}>.*?</{tag}>", re.IGNORECASE | re.DOTALL) for tag in _PRIVACY_TAGS
]


def strip_private(text: str) -> str:
    """Remove all privacy-tagged spans from text."""
    for pat in _PATTERNS:
        text = pat.sub("", text)
    return text


def contains_private(text: str) -> bool:
    """True iff any privacy tag is present (used as a fast skip in distill)."""
    return any(pat.search(text) for pat in _PATTERNS)
