#!/usr/bin/env python3
"""Kimi cross-audit disprover — headless bridge around agents/disprover.md.

Given a finding id, this script:

  1. reads the finding via `ucw-review.py list --json` (id → file/line/title/…),
     or takes the finding object directly via `--finding-json` (arg or stdin)
     so a pipeline that already holds the row can skip the full list scan
  2. runs the disprover prompt body (agents/disprover.md, frontmatter stripped)
     through the claude-kimi bridge (bin/kimi_invoke.py)
  3. parses the {"verdict": ..., "evidence": ...} JSON the agent must emit
  4. records it via `ucw-review.py disprove <id> <verdict>` with attribution
     `--agent kimi-disprover --model kimi-k3`

Cross-audit invariant: the finder ran on a different model; Kimi K3 is the
opposing side. On ANY failure (finding missing, bridge error, unparsable
verdict, CLI error) this script warns on stderr and exits 0 WITHOUT
recording a verdict — a missing disprove verdict must stay missing, never
be fabricated.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = BIN_DIR.parent
UCW_REVIEW = BIN_DIR / "ucw-review.py"
DISPROVER_MD = REPO_ROOT / "agents" / "disprover.md"

sys.path.insert(0, str(BIN_DIR))
sys.path.insert(0, str(REPO_ROOT / "memory"))

from kimi_invoke import kimi_invoke
from ucw_memory.findings import DISPROVER_VERDICTS

AGENT_NAME = "kimi-disprover"
MODEL_NAME = "kimi-k3"
# Read-only investigation toolset. Deliberately NARROWER than the `tools:`
# frontmatter of agents/disprover.md: no Bash. The prompt embeds finding
# text a reviewed diff can influence, and a second vendor's model with
# pre-approved shell would turn that into a prompt-injection-to-exec path.
# Disproving is reading code, not running it.
ALLOWED_TOOLS = "Read,Grep,Glob"

# Timeout for the local ucw-review.py shell-outs (same 60s bound as
# ucw-kimi-opinion.py's shell-outs) — never left unbounded.
CLI_TIMEOUT = 60


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ---- inputs ------------------------------------------------------------------

def load_prompt_body(path: Path = DISPROVER_MD) -> str | None:
    """The disprover instructions with the YAML frontmatter stripped.

    The frontmatter (name/description/tools/model) is subagent wiring for
    Claude Code — it would only confuse a headless prompt. None on any
    read problem.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text.startswith("---"):
        end = text.find("\n---", 4)
        if end > 0:
            text = text[end + 4:]
    text = text.strip()
    return text or None


def fetch_finding(repo: Path, finding_id: str, sha: str | None = None) -> dict | None:
    """Look the finding up through the same read path agents use:
    `ucw-review.py list --json`. None if the CLI fails or the id is unknown."""
    cmd = [sys.executable, str(UCW_REVIEW), "list", "--repo", str(repo), "--json"]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, check=False,
                            timeout=CLI_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        rows = json.loads(cp.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("id") != finding_id:
            continue
        if sha and not str(row.get("sha", "")).startswith(sha):
            continue
        return row
    return None


def load_finding_json(raw: str) -> dict | None:
    """Parse a finding object handed over directly via --finding-json.

    *raw* is either the JSON text itself or ``-`` to read it from stdin.
    None on any read/parse problem or when the payload is not an object —
    the caller warns and records nothing, same as an unknown id.
    """
    if raw == "-":
        try:
            raw = sys.stdin.read()
        except OSError:
            return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def build_prompt(body: str, finding: dict) -> str:
    """Disprover instructions + the finding under audit + headless overrides."""
    subject = {k: finding.get(k) for k in (
        "id", "sha", "severity", "category", "file", "line", "title",
        "detail", "reproducer", "finder_agent", "finder_model",
    )}
    return (
        f"{body}\n\n"
        "## The finding under audit\n\n"
        "```json\n"
        f"{json.dumps(subject, indent=2)}\n"
        "```\n\n"
        "IMPORTANT (headless run): do NOT invoke ucw-review.py yourself — "
        "the harness records your verdict. Output exactly one JSON object "
        "and nothing else:\n"
        '{"verdict": "refuted|confirmed|needs-human", "evidence": "<2-4 '
        'sentences citing file:line>"}\n'
    )


# ---- verdict handling --------------------------------------------------------

def parse_verdict(data: object) -> tuple[str, str] | None:
    """(verdict, evidence) from the agent's JSON payload, or None.

    Verdicts are normalized (case, underscores) but never guessed: anything
    outside DISPROVER_VERDICTS is a parse failure, not a default.
    """
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict", "")).strip().lower().replace("_", "-")
    if verdict not in DISPROVER_VERDICTS:
        return None
    evidence = str(data.get("evidence") or "").strip()
    return verdict, evidence


def record_verdict(
    repo: Path, finding_id: str, verdict: str, evidence: str,
    sha: str | None = None,
) -> bool:
    """Shell out to `ucw-review.py disprove` with kimi attribution."""
    cmd = [sys.executable, str(UCW_REVIEW), "disprove", "--repo", str(repo)]
    if sha:
        cmd += ["--sha", sha]
    cmd += [
        finding_id, verdict,
        "--evidence", evidence,
        "--agent", AGENT_NAME,
        "--model", MODEL_NAME,
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, check=False,
                            timeout=CLI_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    if cp.returncode != 0:
        return False
    sys.stdout.write(cp.stdout)  # forward the CLI's JSON confirmation
    return True


# ---- entry point -------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-kimi-disprove", description=__doc__)
    parser.add_argument("id", help="finding id to cross-audit (e.g. f-a1b2c3)")
    parser.add_argument("--repo", default=os.getcwd(), help="repo root (default: cwd)")
    parser.add_argument("--sha", help="SHA the finding was recorded under (default: any)")
    parser.add_argument("--finding-json", metavar="JSON",
                        help="the finding object itself as JSON ('-' reads stdin) — "
                             "skips the ucw-review.py list scan")
    parser.add_argument("--timeout", type=int, default=None,
                        help="seconds before claude-kimi is killed (default: "
                             "settings-resolved — UCW_KIMI_TIMEOUT_SECS / "
                             "kimi.timeout_secs, else 300)")
    parser.add_argument("--retries", type=int, default=1,
                        help="extra attempts on malformed kimi output (default: 1)")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()

    if args.finding_json:
        finding = load_finding_json(args.finding_json)
        if finding is None:
            _warn("unparsable --finding-json payload — no verdict recorded")
            return 0
        embedded_id = finding.get("id")
        if embedded_id is not None and embedded_id != args.id:
            _warn(f"--finding-json id {embedded_id!r} does not match "
                  f"{args.id} — no verdict recorded")
            return 0
    else:
        finding = fetch_finding(repo, args.id, sha=args.sha)
        if finding is None:
            _warn(f"finding {args.id} not found via ucw-review.py list — "
                  "no verdict recorded")
            return 0

    body = load_prompt_body()
    if body is None:
        _warn(f"cannot read disprover prompt at {DISPROVER_MD} — "
              "no verdict recorded")
        return 0

    result = kimi_invoke(
        build_prompt(body, finding),
        allowed_tools=ALLOWED_TOOLS,
        add_dirs=[repo],
        timeout=args.timeout,
        retries=args.retries,
    )
    if not result["ok"]:
        _warn(f"claude-kimi failed: {result['error']} — no verdict recorded")
        return 0

    parsed = parse_verdict(result["data"])
    if parsed is None:
        preview = json.dumps(result["data"])[:200]
        _warn(f"unparsable disprover verdict from kimi: {preview} — "
              "no verdict recorded")
        return 0
    verdict, evidence = parsed

    if not record_verdict(repo, args.id, verdict, evidence, sha=args.sha):
        _warn(f"ucw-review.py disprove failed for {args.id} — "
              "no verdict recorded")
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
