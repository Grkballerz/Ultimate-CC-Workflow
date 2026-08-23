#!/usr/bin/env python3
"""Kimi second-opinion review lane — one whole-diff pass through claude-kimi.

The nine narrow-scope reviewers (agents/reviewers/*.md) are Claude subagents;
this script adds an independent cross-model opinion on the same diff:

  1. Collect the diff (`--since <ref>` → `git diff <ref>..HEAD`, else stdin)
  2. Build ONE review prompt in the reviewers' scope style (concern list
     scraped from agents/reviewers/*.md frontmatter + the shared severity
     rubric), asking Kimi for findings as a JSON array
  3. kimi_invoke() it — timeout, retries, and lenient JSON extraction are
     that module's job
  4. Persist each finding via `ucw-review.py add-finding` with
     finder_agent=kimi-second-opinion / finder_model=kimi-k3

Pipeline-safe by contract: kimi failure, unusable output, or a broken
add-finding round-trip prints a warning to stderr and exits 0 with zero
findings. This lane is advisory — it must never block the review pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = BIN_DIR.parent
sys.path.insert(0, str(BIN_DIR))
sys.path.insert(0, str(REPO_ROOT / "memory"))

from kimi_invoke import kimi_invoke
from ucw_memory.findings import CATEGORIES, SEVERITIES

FINDER_AGENT = "kimi-second-opinion"
FINDER_MODEL = "kimi-k3"

DEFAULT_SEVERITY = "minor"
DEFAULT_CATEGORY = "correctness"
MAX_DIFF_CHARS = 60_000

_SEVERITY_RUBRIC = """\
- critical — exploitable / causes data loss / breaks production
- major — wrong but recoverable / non-trivial regression
- minor — sloppy but harmless
- nit — style / preference"""


def _warn(msg: str) -> None:
    print(f"[ucw-kimi-opinion] warning: {msg}", file=sys.stderr)


# ---- prompt construction -----------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML-ish frontmatter parse (same shape the agent files use)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def _reviewer_scope_lines() -> list[str]:
    """One bullet per concern, described in the reviewer files' own words."""
    reviewers_dir = REPO_ROOT / "agents" / "reviewers"
    lines: list[str] = []
    for concern in CATEGORIES:
        desc = ""
        path = reviewers_dir / f"{concern}.md"
        try:
            if path.exists():
                meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
                desc = meta.get("description", "")
        except OSError:
            desc = ""
        lines.append(f"- {concern}: {desc}" if desc else f"- {concern}")
    return lines


def build_prompt(diff: str) -> str:
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"
    scope = "\n".join(_reviewer_scope_lines())
    return f"""You are an independent second-opinion code reviewer auditing a diff \
that a separate team of narrow-scope reviewers is also examining. Report \
defects only — no "consider X" suggestions, no style preferences beyond nit.

Concern categories (pick exactly one per finding):
{scope}

Severity rubric (be honest; most findings are minor or major):
{_SEVERITY_RUBRIC}

Respond with ONLY a JSON array (empty array if you find nothing). Each element:
{{"severity": "critical|major|minor|nit", "category": "<one category above>", \
"file": "<path from the diff>", "line": <int>, \
"title": "<one-line summary, <=72 chars>", \
"detail": "<2-5 sentences of concrete reasoning citing code>", \
"reproducer": "<optional: PoC steps or failing test>"}}

Do not assign ids or effective severity — the review CLI owns those.

--- DIFF ---
{diff}"""


# ---- diff acquisition --------------------------------------------------------

def _diff_since(repo: Path, since: str) -> str | None:
    try:
        cp = subprocess.run(
            ["git", "diff", f"{since}..HEAD"], cwd=repo,
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"git diff failed to run: {exc}")
        return None
    if cp.returncode != 0:
        _warn(f"git diff {since}..HEAD exited {cp.returncode}: "
              f"{(cp.stderr or '').strip()[:300]}")
        return None
    return cp.stdout


def _read_diff(args: argparse.Namespace) -> str | None:
    if args.since:
        return _diff_since(Path(args.repo).resolve(), args.since)
    if sys.stdin.isatty():
        # Never hang: an interactive terminal will never deliver a diff.
        _warn("no --since and stdin is a terminal — pass --since REF or pipe a diff in")
        return None
    try:
        return sys.stdin.read()
    except OSError as exc:
        _warn(f"failed to read diff from stdin: {exc}")
        return None


# ---- finding coercion --------------------------------------------------------

def _is_bare_finding(item: dict) -> bool:
    """True iff *item* is recognizably a finding object.

    The bar: a 'severity' key, or both 'title' and 'file'. Anything looser
    (e.g. a lone {'file': ...}) would coerce arbitrary objects into empty
    minor/correctness findings — skip those instead.
    """
    return "severity" in item or ("title" in item and "file" in item)


def _coerce_findings(data: object) -> list[dict] | None:
    """Normalize Kimi's payload into add-finding-ready dicts.

    Returns None when the payload is structurally unusable (not an array of
    findings in any recognizable wrapping). A returned list may be empty —
    that is a legitimate "I looked and saw nothing" result. Entries that
    don't look like findings (see _is_bare_finding) are skipped with a
    warning, never padded into empty defaults.
    """
    if isinstance(data, dict):
        if isinstance(data.get("findings"), list):
            data = data["findings"]
        elif _is_bare_finding(data):
            data = [data]  # a single bare finding object
        else:
            return None
    if not isinstance(data, list):
        return None

    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            _warn(f"skipping non-object finding: {item!r:.120}")
            continue
        if not _is_bare_finding(item):
            _warn("skipping non-conforming finding object "
                  f"(needs 'severity' or 'title'+'file'): {item!r:.120}")
            continue
        severity = item.get("severity")
        if severity not in SEVERITIES:
            _warn(f"invalid severity {severity!r} — coercing to {DEFAULT_SEVERITY!r}")
            severity = DEFAULT_SEVERITY
        category = item.get("category")
        if category not in CATEGORIES:
            _warn(f"invalid category {category!r} — coercing to {DEFAULT_CATEGORY!r}")
            category = DEFAULT_CATEGORY
        try:
            line = int(item.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        payload = {
            "severity": severity,
            "category": category,
            "file": str(item.get("file") or ""),
            "line": line,
            "title": str(item.get("title") or "")[:200],
            "detail": str(item.get("detail") or ""),
            "finder_agent": FINDER_AGENT,
            "finder_model": FINDER_MODEL,
        }
        reproducer = item.get("reproducer")
        if reproducer:
            payload["reproducer"] = str(reproducer)
        out.append(payload)
    return out


# ---- persistence via ucw-review.py ------------------------------------------

def _submit(payload: dict, repo: str, sha: str | None) -> str | None:
    """Shell one finding into ucw-review.py add-finding. Returns id or None."""
    cmd = [sys.executable, str(BIN_DIR / "ucw-review.py"), "add-finding",
           "--repo", repo, "--json", json.dumps(payload)]
    if sha:
        cmd += ["--sha", sha]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"add-finding failed to run: {exc}")
        return None
    if cp.returncode != 0:
        _warn(f"add-finding exited {cp.returncode}: "
              f"{(cp.stderr or '').strip()[:300]}")
        return None
    try:
        return json.loads(cp.stdout).get("id")
    except (json.JSONDecodeError, AttributeError):
        _warn(f"add-finding output unparsable: {cp.stdout[:200]!r}")
        return None


# ---- entry point -------------------------------------------------------------

def _emit(ids: list[str]) -> None:
    json.dump({"added": ids, "count": len(ids)}, sys.stdout)
    sys.stdout.write("\n")


def _run(args: argparse.Namespace) -> int:
    diff = _read_diff(args)
    if diff is None or not diff.strip():
        if diff is not None:
            _warn("empty diff — nothing to review")
        _emit([])
        return 0

    result = kimi_invoke(build_prompt(diff),
                         timeout=args.timeout, retries=args.retries)
    if not result["ok"]:
        _warn(f"kimi second opinion unavailable: {result['error']}")
        _emit([])
        return 0

    findings = _coerce_findings(result["data"])
    if findings is None:
        _warn("kimi output unusable — expected a JSON array of finding objects, "
              f"got {type(result['data']).__name__}")
        _emit([])
        return 0
    if len(findings) > args.max_findings:
        _warn(f"kimi returned {len(findings)} findings — "
              f"keeping the first {args.max_findings}")
        findings = findings[:args.max_findings]

    ids: list[str] = []
    for payload in findings:
        fid = _submit(payload, args.repo, args.sha)
        if fid:
            ids.append(fid)
    _emit(ids)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-kimi-opinion", description=__doc__)
    parser.add_argument("--repo", default=os.getcwd(), help="repo root (default: cwd)")
    parser.add_argument("--sha", help="SHA to record findings against (default: current HEAD)")
    parser.add_argument("--since", metavar="REF",
                        help="base ref — review `git diff REF..HEAD`; omit to read a diff from stdin")
    parser.add_argument("--timeout", type=int, default=None,
                        help="seconds before claude-kimi is killed (default: resolved by "
                             "kimi_invoke — UCW_KIMI_TIMEOUT_SECS env, then "
                             ".ucw/state/settings.json kimi.timeout_secs, then 300)")
    parser.add_argument("--retries", type=int, default=1,
                        help="extra kimi attempts on malformed output (default: 1)")
    parser.add_argument("--max-findings", type=int, default=20,
                        help="cap on findings persisted per run (default: 20)")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:  # broad on purpose — this lane must never crash the pipeline
        _warn(f"unexpected error in kimi second-opinion lane: {exc}")
        _emit([])
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
