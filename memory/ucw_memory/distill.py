"""Extract durable facts from a Claude Code session transcript.

Claude Code writes JSONL transcripts — one JSON event per line. We walk the
file, pull user + assistant text content, strip privacy regions, and apply
heuristic patterns to surface candidate facts. Each fact must contain a
"reason" connective (because / since / so that / due to / so we / in order to)
to pass the quality gate — bare conclusions are dropped.

The v1 extractor is deliberately conservative: it only emits high-confidence,
well-formed facts. False negatives are fine — the user can `/ucw pin` anything
that should have been captured. False positives are expensive (memory rot).

A future v2 will run a Haiku pass for soft fact extraction; this regex layer
will remain as a fast pre-filter.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .privacy import strip_private

# --- patterns -----------------------------------------------------------------

# A "reason connective" — the substring that splits subject-conclusion from reason.
_REASON_RE = re.compile(
    r"\b(because|since|due to|so that|so we|in order to|in order that|to keep|to ensure)\b",
    re.IGNORECASE,
)

# Verb alternation — order matters: longest multi-word forms first so they win
# over single-word prefixes in regex alternation (regex is left-to-right).
_VERBS = (
    r"chose\s+to\s+use|"
    r"standardiz(?:e|ed|es)\s+on|"
    r"stopped\s+using|"
    r"going\s+with|"
    r"will\s+use|"
    r"are\s+using|"
    r"switched\s+to|"
    r"moved\s+to|"
    r"settled\s+on|"
    r"don't\s+use|"
    r"do\s+not\s+use|"
    r"never\s+use|"
    r"always\s+use|"
    r"decided(?:\s+on)?|"
    r"prefers?|"
    r"chose|"
    r"uses?|"
    r"picked|"
    r"avoids?"
)

# Sentence-level capture: subject + verb + everything up to the next sentence end.
# No leading anchor — distillation should find facts anywhere in flowing text.
_DECISION_RE = re.compile(
    rf"\b((?:we|i|the\s+user)\s+(?:{_VERBS})\b[^.\n!?]+)",
    re.IGNORECASE,
)

# SVO splitter for the conclusion half (post reason-split).
_SVO_RE = re.compile(
    rf"^\s*(?P<subject>we|i|the\s+user)\s+"
    rf"(?P<predicate>{_VERBS})\s+"
    rf"(?P<object>[^.\n!?]+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    subject: str
    predicate: str
    object: str
    reason: str
    confidence: float
    source_line: int

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "reason": self.reason,
            "confidence": self.confidence,
            "source_line": self.source_line,
        }


# --- transcript walking -------------------------------------------------------

def _iter_text(transcript_path: Path) -> Iterable[tuple[int, str]]:
    """Yield (line_no, plaintext) tuples from a Claude Code JSONL transcript.

    Claude Code transcript events vary in shape — we pull text from any field
    that looks like message content.
    """
    if not transcript_path.exists():
        return
    for line_no, raw in enumerate(transcript_path.open("r", encoding="utf-8"), start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for text in _extract_text(event):
            if text:
                yield line_no, text


def _extract_text(event: dict) -> list[str]:
    """Pull plaintext from common Claude Code event shapes."""
    out: list[str] = []
    msg = event.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if isinstance(text, str):
                        out.append(text)
    # User events may set `prompt` directly.
    prompt = event.get("prompt")
    if isinstance(prompt, str):
        out.append(prompt)
    return out


# --- extraction ---------------------------------------------------------------

def _split_at_reason(sentence: str) -> tuple[str, str] | None:
    """Split sentence into (conclusion, reason). Returns None if no reason connective."""
    m = _REASON_RE.search(sentence)
    if not m:
        return None
    conclusion = sentence[: m.start()].strip(" ,.:;-")
    reason = sentence[m.end():].strip(" ,.:;-")
    if not conclusion or not reason:
        return None
    return conclusion, reason


def _parse_svo(conclusion: str) -> tuple[str, str, str] | None:
    m = _SVO_RE.match(conclusion)
    if not m:
        return None
    return (m.group("subject").strip(), m.group("predicate").strip().lower(), m.group("object").strip())


def extract_from_text(text: str, *, line_no: int = 0) -> list[Candidate]:
    """Run the heuristic extractor over a chunk of plaintext."""
    text = strip_private(text)
    if not text.strip():
        return []

    candidates: list[Candidate] = []
    for m in _DECISION_RE.finditer(text):
        sentence = m.group(1).strip()
        split = _split_at_reason(sentence)
        if split is None:
            continue
        conclusion, reason = split
        svo = _parse_svo(conclusion)
        if svo is None:
            continue
        subject, predicate, object_ = svo
        candidates.append(Candidate(
            subject=subject,
            predicate=predicate,
            object=object_,
            reason=reason,
            confidence=0.7,
            source_line=line_no,
        ))
    return candidates


def extract_from_transcript(transcript_path: Path) -> list[Candidate]:
    """Walk a JSONL transcript and return all distilled candidates."""
    out: list[Candidate] = []
    for line_no, text in _iter_text(transcript_path):
        out.extend(extract_from_text(text, line_no=line_no))
    return out


# --- writing into MemoryDB ----------------------------------------------------

def write_candidates_to_db(
    candidates: list[Candidate],
    db,  # MemoryDB — typed loosely to avoid circular import
    *,
    scope: str = "project",
    source_session: str | None = None,
) -> list[int]:
    """Persist candidates to the DB. Returns list of inserted fact ids."""
    ids: list[int] = []
    for cand in candidates:
        try:
            fact_id = db.note(
                scope=scope,
                subject=cand.subject,
                predicate=cand.predicate,
                object_=cand.object,
                reason=cand.reason,
                source_session=source_session,
                confidence=cand.confidence,
            )
            ids.append(fact_id)
        except ValueError:
            # quality gate rejected — skip
            continue
    return ids
