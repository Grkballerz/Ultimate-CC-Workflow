"""Findings store — the governance layer that lives outside the model.

Cloudflare's insight: probabilistic models give you probabilistic safety.
The audit trail, approval gates, and effective-severity computation must
live in deterministic code that the model cannot bypass.

Schema (see `Finding`):
- One JSONL row per finding under `.ucw/reviews/<sha>/findings.jsonl`
- Append-only; corrections are new rows (with `supersedes`)
- An `approvals.jsonl` log records human acks
- `summary.md` is regenerated from the JSONL on demand

The CLI lives in `bin/ucw-review.py`; this module owns the schema.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---- types -------------------------------------------------------------------

SEVERITIES = ("critical", "major", "minor", "nit")
CATEGORIES = (
    "correctness", "injection", "deserialization", "auth",
    "performance", "data-loss", "api-compat", "tests", "docs",
)
DISPROVER_VERDICTS = ("confirmed", "refuted", "needs-human")
REACHABILITY_VERDICTS = ("reachable", "unreachable", "unclear", "not-applicable")


@dataclass
class Finding:
    """A single finding from a narrow-scope reviewer agent.

    The `effective_severity` field is the only one the gate cares about.
    It's derived from raw `severity` + `disprover_verdict` + `reachability_verdict`
    by `_effective_severity()` below — never trust an agent to set it directly.
    """
    id: str
    severity: str
    category: str
    file: str
    line: int
    title: str
    detail: str
    reproducer: str | None = None

    # Provenance
    finder_agent: str = ""
    finder_model: str = ""

    # Cross-audit (a different agent's attempt to refute)
    disprover_verdict: str | None = None
    disprover_agent: str | None = None
    disprover_model: str | None = None
    disprover_evidence: str | None = None

    # Chain split (security findings only — reachability from untrusted input)
    reachability_verdict: str | None = None
    reachability_evidence: str | None = None

    # Dedup
    duplicate_of: str | None = None
    sources: list[str] = field(default_factory=list)

    # Governance
    sha: str = ""
    base: str = ""
    created_at: int = 0
    superseded_by: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Approval:
    finding_id: str
    actor: str
    reason: str
    approved_at: int


# ---- effective severity ------------------------------------------------------

def _effective_severity(f: Finding) -> str:
    """Compute the severity the gate should use.

    Rules:
      - Disprover refuted → drop two levels (critical→minor, major→nit, ...)
      - Disprover needs-human → keep raw severity but flag for review
      - Security finding (injection/deser/auth) AND reachability=unreachable
        → drop one level
      - Otherwise: raw severity
    """
    sev = f.severity
    if sev not in SEVERITIES:
        sev = "minor"

    levels = list(SEVERITIES)  # critical=0, nit=3
    idx = levels.index(sev)

    if f.disprover_verdict == "refuted":
        idx = min(len(levels) - 1, idx + 2)
    elif f.disprover_verdict == "needs-human":
        pass  # keep raw; human approval flow handles it

    if f.category in {"injection", "deserialization", "auth"}:
        if f.reachability_verdict == "unreachable":
            idx = min(len(levels) - 1, idx + 1)
        elif f.reachability_verdict in (None, "unclear"):
            # Don't lower severity if reachability hasn't been determined
            pass

    return levels[idx]


# ---- store -------------------------------------------------------------------

class ReviewStore:
    """Single SHA's findings + approvals on disk.

    Concurrent appends are safe at the OS level for single-line writes < PIPE_BUF;
    we keep each JSONL row to one line.
    """

    def __init__(self, root: Path, sha: str):
        self.root = Path(root)
        self.sha = sha
        self.dir = self.root / ".ucw" / "reviews" / sha
        self.dir.mkdir(parents=True, exist_ok=True)
        self.findings_path = self.dir / "findings.jsonl"
        self.approvals_path = self.dir / "approvals.jsonl"
        self.scope_path = self.dir / "scope.json"

    # ---- writes -------------------------------------------------------------

    def add(self, finding: Finding) -> Finding:
        if not finding.id:
            finding.id = self._new_id()
        if not finding.created_at:
            finding.created_at = int(time.time())
        finding.sha = finding.sha or self.sha
        with self.findings_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(finding.as_dict(), ensure_ascii=False) + "\n")
        return finding

    def update(self, finding_id: str, **changes: Any) -> Finding | None:
        """Append a new row that supersedes the existing one (audit-trail safe)."""
        current = self.get(finding_id)
        if current is None:
            return None
        merged = Finding(**current.as_dict())
        for k, v in changes.items():
            setattr(merged, k, v)
        # Mark prior as superseded
        prior = Finding(**current.as_dict())
        prior.superseded_by = merged.id  # same id; we replay-latest-wins
        # Just write the new row; the load logic picks latest by id.
        with self.findings_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(merged.as_dict(), ensure_ascii=False) + "\n")
        return merged

    def approve(self, finding_id: str, actor: str, reason: str) -> Approval:
        approval = Approval(
            finding_id=finding_id, actor=actor, reason=reason,
            approved_at=int(time.time()),
        )
        with self.approvals_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(approval), ensure_ascii=False) + "\n")
        return approval

    def write_scope(self, scopes: list[dict]) -> None:
        self.scope_path.write_text(
            json.dumps({"sha": self.sha, "scopes": scopes}, indent=2),
            encoding="utf-8",
        )

    # ---- reads --------------------------------------------------------------

    def all(self) -> list[Finding]:
        """Latest-version-wins replay of the append log, hiding duplicate_of."""
        latest: dict[str, Finding] = {}
        if not self.findings_path.exists():
            return []
        for line in self.findings_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                f = Finding(**data)
            except TypeError:
                continue
            latest[f.id] = f
        # Hide findings that got merged into another (duplicate_of set)
        return [f for f in latest.values() if not f.duplicate_of]

    def get(self, finding_id: str) -> Finding | None:
        for f in self.all():
            if f.id == finding_id:
                return f
        return None

    def approvals(self) -> list[Approval]:
        if not self.approvals_path.exists():
            return []
        out: list[Approval] = []
        for line in self.approvals_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Approval(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def is_approved(self, finding_id: str) -> bool:
        return any(a.finding_id == finding_id for a in self.approvals())

    # ---- gate ---------------------------------------------------------------

    def gate_summary(self) -> dict:
        """Compute the gate state. Used by `ucw-review gate`."""
        findings = self.all()
        for f in findings:
            f_eff = _effective_severity(f)
            # mutate copy attr for reporting (won't be persisted)
            f._effective_severity = f_eff
        approved = {a.finding_id for a in self.approvals()}
        unack_critical = [
            f for f in findings
            if f._effective_severity == "critical"
            and f.id not in approved
        ]
        return {
            "sha": self.sha,
            "total": len(findings),
            "by_severity": _bucket(findings),
            "unack_critical": len(unack_critical),
            "unack_critical_ids": [f.id for f in unack_critical],
        }

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        return "f-" + secrets.token_hex(4)


def _bucket(findings: Iterable[Finding]) -> dict:
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        sev = getattr(f, "_effective_severity", None) or _effective_severity(f)
        counts[sev] = counts.get(sev, 0) + 1
    return counts


# ---- dedup -------------------------------------------------------------------

def normalized_text(f: Finding) -> str:
    """Canonical string used for similarity hashing in dedup."""
    return f"{f.category}|{f.file}|{f.line}|{f.title}".lower()


def shingle_hash(text: str, *, n: int = 4) -> set[str]:
    """Character n-gram set for cheap Jaccard similarity."""
    text = "".join(c.lower() for c in text if c.isalnum() or c in " /:_-")
    if len(text) < n:
        return {text}
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicates(findings: list[Finding], threshold: float = 0.0) -> list[tuple[Finding, Finding]]:
    """Return pairs (loser, winner) — loser should be merged into winner.

    Two findings are considered the same defect when they share
    (file, line, category). Two reviewers phrasing the same observation
    differently is the common case — agents are not coordinated, so their
    titles will diverge even when their target does not.

    The `threshold` argument is title-similarity required for clusters that
    cross multiple lines (e.g. line 0 = file-level finding vs a specific
    line in the same file). At 0.0, every (file, line, category) collision
    merges. At 1.0, only exact title matches do.

    Pairs are returned (loser, winner) where the winner is the lowest id —
    deterministic so reruns don't churn.
    """
    pairs: list[tuple[Finding, Finding]] = []
    by_loc: dict[tuple[str, int, str], list[Finding]] = {}
    for f in findings:
        by_loc.setdefault((f.file, f.line, f.category), []).append(f)
    for cluster in by_loc.values():
        if len(cluster) < 2:
            continue
        cluster.sort(key=lambda x: x.id)
        winner = cluster[0]
        winner_shingles = shingle_hash(normalized_text(winner))
        for loser in cluster[1:]:
            if threshold <= 0.0:
                # Co-location is sufficient — merge unconditionally.
                pairs.append((loser, winner))
                continue
            sim = jaccard(winner_shingles, shingle_hash(normalized_text(loser)))
            if sim >= threshold:
                pairs.append((loser, winner))
    return pairs


# ---- digest helpers ----------------------------------------------------------

def head_sha(repo_root: Path) -> str:
    """Best-effort HEAD SHA; falls back to a content-derived id if not a git repo."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return out
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    # Fallback: deterministic content-hash of the repo path so the same
    # non-git dir always gets the same id. Non-cryptographic use.
    return "nogit-" + hashlib.sha1(  # noqa: S324 — content fingerprint, not security
        str(repo_root).encode()
    ).hexdigest()[:12]
