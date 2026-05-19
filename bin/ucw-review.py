#!/usr/bin/env python3
"""Cross-audit review orchestrator — Cloudflare-style narrow-scope + disprove.

This is the deterministic plumbing layer; the LLM agents are invoked by
Claude itself when `commands/review.md` is dispatched. This script handles:

  scope         — decompose a diff into (file, concern) review scopes
  add-finding   — append a finding from a reviewer agent
  disprove      — record a disprover verdict for a finding
  reachability  — record a reachability verdict (security findings only)
  dedup         — merge near-duplicate findings (same file+line, similar title)
  gate          — exit 2 if any unack'd effective-critical findings remain
  status        — current SHA's findings + summary
  approve       — record human ack for a finding
  summary       — render markdown / json report
  list          — every finding across every reviewed SHA

Storage lives under .ucw/reviews/<sha>/. See memory/ucw_memory/findings.py
for the schema and severity-effective-after-disprove logic.

The "governance lives outside the model" pattern: agents only produce
findings as JSON. The CLI computes effective severity and approval state
from immutable persisted records — model output never directly sets the
gate result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "memory"))

from ucw_memory.findings import (
    CATEGORIES,
    DISPROVER_VERDICTS,
    REACHABILITY_VERDICTS,
    SEVERITIES,
    Finding,
    ReviewStore,
    _effective_severity,
    find_duplicates,
    head_sha,
)

# ---- scope computation -------------------------------------------------------

# Each concern → file globs that the concern applies to.
CONCERN_GLOBS: dict[str, list[str]] = {
    "correctness":      ["**/*"],
    "injection":        ["**/*.py", "**/*.sh", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"],
    "deserialization":  ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs", "**/*.java", "**/*.rb"],
    "auth":             ["**/auth/**", "**/middleware/**", "**/handlers/**", "**/api/**", "**/routes/**"],
    "performance":      ["**/*"],
    "data-loss":        ["**/migrations/**", "**/schema/**", "**/*.sql", "**/models/**"],
    "api-compat":       ["**/api/**", "**/routes/**", "**/*.proto", "**/openapi*", "**/swagger*"],
    "tests":            ["**/*"],
    "docs":             ["**/.ucw/knowledge/**", "README*", "docs/**"],
}


def _git(*args, cwd: Path | None = None, check: bool = True) -> str:
    import subprocess
    cp = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check,
    )
    return cp.stdout.strip()


def _glob_matches(pattern: str, path: str) -> bool:
    """Lightweight `**` glob matcher. fnmatch doesn't grok `**` so we
    convert it to a regex."""
    import re
    # Translate glob → regex
    re_parts: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i:i + 3] == "**/":
            re_parts.append(r"(?:.+/)?")
            i += 3
        elif pattern[i:i + 2] == "**":
            re_parts.append(r".*")
            i += 2
        elif pattern[i] == "*":
            re_parts.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            re_parts.append(r"[^/]")
            i += 1
        else:
            re_parts.append(re.escape(pattern[i]))
            i += 1
    return bool(re.fullmatch("".join(re_parts), path))


def compute_scopes(repo: Path, *, base: str | None = None, head: str = "HEAD") -> list[dict]:
    """List of {file, concern} pairs to fan out across.

    Default base: `main`. If we're currently ON `main` (diff empty), step
    back to `HEAD~1` so reviewing a branch that landed on main still works.
    Falls back to reviewing every tracked file if no diff base is sensible.
    """
    def _try_branch(name: str) -> str | None:
        try:
            _git("rev-parse", "--verify", name, cwd=repo, check=True)
            return name
        except Exception:
            return None

    if base is None:
        base = _try_branch("main")

    # If the base resolves to the same commit as HEAD, the diff is empty.
    # Step back one commit so we still get a meaningful review.
    if base is not None:
        try:
            base_sha = _git("rev-parse", base, cwd=repo)
            head_sha_str = _git("rev-parse", head, cwd=repo)
            if base_sha == head_sha_str:
                base = _try_branch("HEAD~1")
        except Exception:  # noqa: S110 — git failures here are non-fatal
            pass

    if base is None:
        # No usable base (first commit or out-of-git): review every tracked file
        try:
            files = _git("ls-files", cwd=repo).splitlines()
        except Exception:
            files = []
    else:
        try:
            files = _git("diff", "--name-only", f"{base}..{head}", cwd=repo).splitlines()
        except Exception:
            files = []

    # Filter to existing files (deletions don't need review)
    files = [f for f in files if f and (repo / f).exists()]

    scopes: list[dict] = []
    for f in files:
        for concern, globs in CONCERN_GLOBS.items():
            if any(_glob_matches(g, f) for g in globs):
                scopes.append({"file": f, "concern": concern})

    # Always emit at least one no-op scope so the report shows "0 findings"
    # rather than empty.
    if not scopes and files:
        scopes.append({"file": files[0], "concern": "correctness"})

    return scopes


# ---- commands ----------------------------------------------------------------

def _store(args: argparse.Namespace) -> ReviewStore:
    repo = Path(args.repo).resolve()
    sha = args.sha or head_sha(repo)
    return ReviewStore(repo, sha)


def cmd_scope(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    scopes = compute_scopes(repo, base=args.since, head=args.head)
    if args.persist:
        store = ReviewStore(repo, args.sha or head_sha(repo))
        store.write_scope(scopes)
    payload = {
        "sha": args.sha or head_sha(repo),
        "base": args.since,
        "scopes": scopes,
        "scope_count": len(scopes),
        "file_count": len({s["file"] for s in scopes}),
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_add_finding(args: argparse.Namespace) -> int:
    store = _store(args)
    raw = json.loads(args.json) if args.json else json.load(sys.stdin)
    if isinstance(raw, list):
        ids = []
        for item in raw:
            f = _coerce_finding(item)
            saved = store.add(f)
            ids.append(saved.id)
        json.dump({"added": ids}, sys.stdout)
    else:
        f = _coerce_finding(raw)
        saved = store.add(f)
        json.dump({"id": saved.id, "effective_severity": _effective_severity(saved)},
                  sys.stdout)
    sys.stdout.write("\n")
    return 0


def _coerce_finding(data: dict) -> Finding:
    """Accept partial input and fill required fields with sensible defaults."""
    if data.get("severity") not in SEVERITIES:
        raise ValueError(f"invalid severity: {data.get('severity')!r}")
    if data.get("category") not in CATEGORIES:
        raise ValueError(f"invalid category: {data.get('category')!r}")
    # Whitelist the dataclass fields we accept
    allowed = {
        "id", "severity", "category", "file", "line", "title", "detail",
        "reproducer", "finder_agent", "finder_model",
        "disprover_verdict", "disprover_agent", "disprover_model", "disprover_evidence",
        "reachability_verdict", "reachability_evidence",
        "duplicate_of", "sources", "sha", "base", "created_at", "superseded_by",
    }
    filtered = {k: v for k, v in data.items() if k in allowed}
    filtered.setdefault("id", "")
    filtered.setdefault("line", 0)
    filtered.setdefault("title", "")
    filtered.setdefault("detail", "")
    filtered.setdefault("file", "")
    return Finding(**filtered)


def cmd_disprove(args: argparse.Namespace) -> int:
    if args.verdict not in DISPROVER_VERDICTS:
        print(f"invalid verdict: {args.verdict}", file=sys.stderr)
        return 2
    store = _store(args)
    if store.get(args.id) is None:
        print(f"unknown finding: {args.id}", file=sys.stderr)
        return 2
    updated = store.update(
        args.id,
        disprover_verdict=args.verdict,
        disprover_agent=args.agent or "disprover",
        disprover_model=args.model or "",
        disprover_evidence=args.evidence or "",
    )
    json.dump({"id": args.id, "verdict": args.verdict,
               "effective_severity": _effective_severity(updated)},
              sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_reachability(args: argparse.Namespace) -> int:
    if args.verdict not in REACHABILITY_VERDICTS:
        print(f"invalid verdict: {args.verdict}", file=sys.stderr)
        return 2
    store = _store(args)
    if store.get(args.id) is None:
        print(f"unknown finding: {args.id}", file=sys.stderr)
        return 2
    updated = store.update(
        args.id,
        reachability_verdict=args.verdict,
        reachability_evidence=args.evidence or "",
    )
    json.dump({"id": args.id, "verdict": args.verdict,
               "effective_severity": _effective_severity(updated)},
              sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_dedup(args: argparse.Namespace) -> int:
    store = _store(args)
    findings = store.all()
    pairs = find_duplicates(findings, threshold=args.threshold)
    merged: list[dict] = []
    for loser, winner in pairs:
        # Combine sources, mark loser as duplicate
        combined_sources = list(set([
            *winner.sources, *loser.sources,
            winner.finder_agent, loser.finder_agent,
        ]))
        combined_sources = [s for s in combined_sources if s]
        store.update(winner.id, sources=combined_sources)
        store.update(loser.id, duplicate_of=winner.id)
        merged.append({"loser": loser.id, "winner": winner.id})
    json.dump({"merged": merged, "count": len(merged)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    store = _store(args)
    summary = store.gate_summary()
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_gate_text(summary)
    if summary["unack_critical"] > 0:
        return 2
    if args.strict and summary["by_severity"].get("major", 0) > 0:
        return 3
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    store = _store(args)
    if store.get(args.id) is None:
        print(f"unknown finding: {args.id}", file=sys.stderr)
        return 2
    actor = args.actor or os.environ.get("USER", "anonymous")
    approval = store.approve(args.id, actor=actor, reason=args.reason or "")
    json.dump(asdict(approval), sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    summary = store.gate_summary()
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_gate_text(summary)
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    store = _store(args)
    findings = store.all()
    if args.format == "json":
        json.dump(
            {
                "sha": store.sha,
                "findings": [f.as_dict() | {"effective_severity": _effective_severity(f)}
                             for f in findings],
                "gate": store.gate_summary(),
            },
            sys.stdout, indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(_render_markdown(store, findings))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    reviews_dir = repo / ".ucw" / "reviews"
    if not reviews_dir.is_dir():
        print("[]" if args.json else "(no reviews yet)")
        return 0
    all_rows: list[dict] = []
    for sha_dir in sorted(reviews_dir.iterdir()):
        if not sha_dir.is_dir():
            continue
        store = ReviewStore(repo, sha_dir.name)
        for f in store.all():
            all_rows.append(f.as_dict() | {
                "effective_severity": _effective_severity(f),
                "approved": store.is_approved(f.id),
            })
    if args.json:
        json.dump(all_rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for row in all_rows:
            print(f"{row['sha'][:8]} {row['id']} "
                  f"{row['effective_severity']:<8} {row['category']:<15} "
                  f"{row['file']}:{row['line']} {row['title'][:50]}")
    return 0


# ---- rendering ---------------------------------------------------------------

def _print_gate_text(summary: dict) -> None:
    by = summary["by_severity"]
    print(f"REVIEW GATE — sha {summary['sha'][:8]}")
    print("─" * 60)
    for sev in SEVERITIES:
        print(f"  {sev:<10} {by.get(sev, 0)}")
    if summary["unack_critical"] > 0:
        print(f"\n  ⛔ {summary['unack_critical']} unacknowledged critical finding(s)")
        for fid in summary["unack_critical_ids"]:
            print(f"     {fid}  (use: ucw-review.py approve {fid} --reason ...)")
    else:
        print("\n  ✓ no unack'd critical findings")


def _render_markdown(store: ReviewStore, findings: list[Finding]) -> str:
    summary = store.gate_summary()
    out: list[str] = [f"# Review report — `{store.sha[:8]}`", ""]
    by = summary["by_severity"]
    out.append("| Severity | Count |")
    out.append("| --- | ---: |")
    for sev in SEVERITIES:
        out.append(f"| {sev} | {by.get(sev, 0)} |")
    out.append("")
    if summary["unack_critical"]:
        out.append(f"> ⛔ **{summary['unack_critical']} unacknowledged critical finding(s)** — `/review approve <id>` to ack with reason.")
        out.append("")
    by_sev: dict[str, list[Finding]] = {s: [] for s in SEVERITIES}
    for f in findings:
        by_sev[_effective_severity(f)].append(f)
    for sev in SEVERITIES:
        items = by_sev[sev]
        if not items:
            continue
        out.append(f"## {sev.title()}")
        out.append("")
        for f in items:
            out.append(f"### `{f.id}` — {f.title}")
            out.append("")
            out.append(f"- **File:** `{f.file}:{f.line}`")
            out.append(f"- **Category:** `{f.category}`")
            out.append(f"- **Finder:** `{f.finder_agent}` ({f.finder_model})")
            if f.disprover_verdict:
                out.append(f"- **Disprover ({f.disprover_agent}/{f.disprover_model}):** `{f.disprover_verdict}`")
                if f.disprover_evidence:
                    out.append(f"  - {f.disprover_evidence}")
            if f.reachability_verdict:
                out.append(f"- **Reachability:** `{f.reachability_verdict}`")
                if f.reachability_evidence:
                    out.append(f"  - {f.reachability_evidence}")
            if store.is_approved(f.id):
                out.append("- **Approval:** ✓ approved")
            out.append("")
            out.append(f.detail)
            if f.reproducer:
                out.append("")
                out.append("```")
                out.append(f.reproducer)
                out.append("```")
            out.append("")
    return "\n".join(out)


# ---- argument parsing --------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=os.getcwd(), help="repo root (default: cwd)")
    p.add_argument("--sha", help="SHA to operate against (default: current HEAD)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-review", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scope", help="compute review scopes")
    _add_common(p)
    p.add_argument("--since", help="base ref (default: main, then HEAD~1)")
    p.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    p.add_argument("--persist", action="store_true", help="write scope.json to the review dir")
    p.set_defaults(func=cmd_scope)

    p = sub.add_parser("add-finding", help="append a finding from a reviewer agent")
    _add_common(p)
    p.add_argument("--json", help="finding(s) JSON (object or array); else read from stdin")
    p.set_defaults(func=cmd_add_finding)

    p = sub.add_parser("disprove", help="record a disprover verdict")
    _add_common(p)
    p.add_argument("id")
    p.add_argument("verdict", choices=DISPROVER_VERDICTS)
    p.add_argument("--evidence", help="reasoning text")
    p.add_argument("--agent", help="disprover agent name")
    p.add_argument("--model", help="disprover model name")
    p.set_defaults(func=cmd_disprove)

    p = sub.add_parser("reachability", help="record a reachability verdict")
    _add_common(p)
    p.add_argument("id")
    p.add_argument("verdict", choices=REACHABILITY_VERDICTS)
    p.add_argument("--evidence", help="reasoning text")
    p.set_defaults(func=cmd_reachability)

    p = sub.add_parser("dedup", help="merge near-duplicate findings")
    _add_common(p)
    p.add_argument("--threshold", type=float, default=0.0,
                   help="title-similarity required when locations match. "
                        "0 = merge any co-located pair (default). "
                        "1.0 = require identical titles.")
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("gate", help="exit 2 on unack'd criticals (3 on --strict + major)")
    _add_common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("approve", help="record human ack for a finding")
    _add_common(p)
    p.add_argument("id")
    p.add_argument("--reason", help="why this is acceptable")
    p.add_argument("--actor", help="who approved (default: $USER)")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("status", help="current SHA gate state")
    _add_common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("summary", help="render full report")
    _add_common(p)
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("list", help="every finding across every SHA")
    p.add_argument("--repo", default=os.getcwd())
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
