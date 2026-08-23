"""Extract durable facts from a Claude Code session transcript.

Claude Code writes JSONL transcripts — one JSON event per line. We walk the
file, pull user + assistant text content, strip privacy regions, and apply
heuristic patterns to surface candidate facts.

Facts with an explicit "reason" connective (because / since / so that / due to
/ so we / in order to) get full confidence. Strong decision statements without
one — "pinned TS to 5.x", "the fix was to add a mutex", bullet-point decision
lines — are still captured, at reduced confidence with a placeholder reason.
Weak verbs ("we use X") without a reason are dropped, as are questions and
hedged proposals ("maybe we should ...").

The v1 extractor was so conservative that live batches wrote zero facts; this
version broadens subjects (contractions, proper nouns, module paths) and verb
stems while keeping the precision guards. A future v2 will run a Haiku pass
for soft fact extraction; this regex layer will remain as a fast pre-filter.
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
    r"decided\s+to\s+use|"
    r"standardiz(?:e|ed|es)\s+on|"
    r"stopped\s+using|"
    r"sticking\s+with|"
    r"stick\s+with|"
    r"going\s+with|"
    r"went\s+with|"
    r"will\s+use|"
    r"are\s+using|"
    r"switched\s+to|"
    r"moved\s+to|"
    r"settled\s+on|"
    r"don't\s+use|"
    r"do\s+not\s+use|"
    r"never\s+use|"
    r"always\s+use|"
    r"decided(?:\s+on|\s+to)?|"
    r"prefers?|"
    r"chose|"
    r"switched|"
    r"pinned|"
    r"renamed|"
    r"using|"
    r"uses?|"
    r"picked|"
    r"avoids?"
)

# Subject alternation. Pronouns (incl. contractions) are matched
# case-insensitively via an inline group; proper-noun forms stay
# case-SENSITIVE so ordinary sentence-initial words ("The", "Maybe") don't
# masquerade as subjects. Covers: we're / I'm / it's / the user, module paths
# (bin/x.py), dotted names (Foo.bar), acronyms (TS, FTS5), CamelCase
# (TypeScript).
_SUBJECTS = (
    r"(?i:we're|we|i'm|it's|it|the\s+user|the\s+team|i)|"
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+|"          # module path: bin/x.py
    r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_]+)+|"  # dotted: Foo.bar, ucw-review.py
    r"[A-Z]{2,}[A-Za-z0-9_-]*|"                    # acronym: TS, GSAP, FTS5
    r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*"             # CamelCase: TypeScript
)

# Normalize contraction subjects so facts group cleanly in the DB.
_SUBJECT_NORMALIZE = {"we're": "we", "i'm": "i", "it's": "it"}

# SVO capture anywhere in a (pre-split) sentence. The object runs to the end
# of the sentence — dots inside versions ("5.x") and paths are fine because
# sentences are split on punctuation-followed-by-whitespace, not bare dots.
_DECISION_RE = re.compile(
    rf"(?:^|(?<=[\s,;:(]))(?P<subject>{_SUBJECTS})\s+"
    rf"(?i:(?P<predicate>{_VERBS}))\s+"
    rf"(?P<object>.+)$",
)

# Verb-first decision lines — bullet points and terse notes with an implied
# "we": "pinned TS to 5.x", "- switched to forks pool". Only unambiguous
# decision verbs; bare "use X" is excluded (too common in instructions).
_IMPERATIVE_VERBS = (
    r"pinned|"
    r"switched\s+to|"
    r"renamed|"
    r"standardized\s+on|"
    r"settled\s+on|"
    r"went\s+with|"
    r"going\s+with|"
    r"chose(?:\s+to\s+use)?|"
    r"picked(?!\s+up)|"
    r"decided\s+(?:on|to\s+use|to)|"
    r"stopped\s+using|"
    r"moved\s+to|"
    r"don't\s+use|"
    r"do\s+not\s+use|"
    r"never\s+use|"
    r"always\s+use"
)
_IMPERATIVE_RE = re.compile(
    rf"^(?i:(?P<predicate>{_IMPERATIVE_VERBS}))\s+(?P<object>.+)$",
)

# "the fix was to add a mutex" / "the workaround is to remap the sink".
_FIX_RE = re.compile(
    r"(?i:(?P<subject>the\s+(?:fix|workaround|solution|answer))\s+"
    r"(?P<tense>was|is)\s+to)\s+(?P<object>.+)$",
)

# Strong decision predicates may pass WITHOUT a reason connective (reduced
# confidence). Weak ones ("uses", "prefers") still require a reason.
_STRONG_PREDICATE_RE = re.compile(
    r"^(?:pinned|switched(?:\s+to)?|renamed|standardiz(?:e|ed|es)\s+on|"
    r"settled\s+on|went\s+with|going\s+with|chose(?:\s+to\s+use)?|picked|"
    r"decided(?:\s+on|\s+to(?:\s+use)?)?|stopped\s+using|moved\s+to|"
    r"don't\s+use|do\s+not\s+use|never\s+use|always\s+use|was\s+to|is\s+to)$",
)

# Precision guards: never mint a fact from a question or a hedged proposal.
_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|might|should|could|would|wondering|consider(?:ing)?|"
    r"what\s+if|not\s+sure|do\s+you|thinking\s+about|propos(?:e|ed|al|ing))\b",
    re.IGNORECASE,
)

_CONFIDENCE_WITH_REASON = 0.7
_CONFIDENCE_NO_REASON = 0.55
_IMPLICIT_REASON = "stated as a decision (no explicit reason given)"


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

_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+")


def _iter_sentences(text: str) -> Iterable[str]:
    """Yield sentences from flowing text, one bullet/line at a time.

    Sentences split on end-punctuation followed by whitespace — bare dots
    inside versions ("5.x"), paths ("bin/x.py"), and dotted names survive.
    """
    for line in text.splitlines():
        stripped = _BULLET_RE.sub("", line.strip())
        if not stripped:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", stripped):
            sentence = sentence.strip()
            if sentence:
                yield sentence


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


def _normalize_predicate(predicate: str) -> str:
    return re.sub(r"\s+", " ", predicate.strip().lower())


def _normalize_subject(subject: str) -> str:
    subject = re.sub(r"\s+", " ", subject.strip())
    lowered = subject.lower()
    if lowered in _SUBJECT_NORMALIZE:
        return _SUBJECT_NORMALIZE[lowered]
    if lowered in ("we", "i", "it", "the user", "the team"):
        return lowered
    return subject


def _parse_svo(conclusion: str) -> tuple[str, str, str] | None:
    """Try each SVO shape against the conclusion; return the first hit."""
    m = _FIX_RE.search(conclusion)
    if m:
        return (
            re.sub(r"\s+", " ", m.group("subject").lower()),
            f"{m.group('tense').lower()} to",
            m.group("object").strip(" ,.:;-"),
        )
    m = _DECISION_RE.search(conclusion)
    if m:
        # Hedged proposals ("maybe we should…", "we could switch to…") never
        # become facts — check the text preceding the match in this sentence.
        if _HEDGE_RE.search(conclusion[: m.start()]):
            return None
        return (
            _normalize_subject(m.group("subject")),
            _normalize_predicate(m.group("predicate")),
            m.group("object").strip(" ,.:;-"),
        )
    m = _IMPERATIVE_RE.match(conclusion)
    if m:
        # Verb-first line — implied "we" subject.
        return (
            "we",
            _normalize_predicate(m.group("predicate")),
            m.group("object").strip(" ,.:;-"),
        )
    return None


def extract_from_text(text: str, *, line_no: int = 0) -> list[Candidate]:
    """Run the heuristic extractor over a chunk of plaintext."""
    text = strip_private(text)
    if not text.strip():
        return []

    candidates: list[Candidate] = []
    seen: set[tuple[str, str, str]] = set()
    for sentence in _iter_sentences(text):
        # Questions never become facts.
        if sentence.endswith("?"):
            continue

        split = _split_at_reason(sentence)
        if split is not None:
            conclusion, reason = split
            confidence = _CONFIDENCE_WITH_REASON
        else:
            conclusion, reason = sentence.rstrip(" .!"), ""
            confidence = _CONFIDENCE_NO_REASON

        svo = _parse_svo(conclusion)
        if svo is None:
            continue
        subject, predicate, object_ = svo
        if not object_:
            continue
        if not reason:
            # Reason-less statements only survive for strong decision verbs
            # ("pinned", "switched to", …) — "we use X" alone stays dropped.
            if not _STRONG_PREDICATE_RE.match(predicate):
                continue
            reason = _IMPLICIT_REASON

        key = (subject.lower(), predicate, object_.lower())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(Candidate(
            subject=subject,
            predicate=predicate,
            object=object_,
            reason=reason,
            confidence=confidence,
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
