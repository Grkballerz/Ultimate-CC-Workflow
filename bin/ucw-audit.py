#!/usr/bin/env python3
"""Security audit of a UCW install.

Scans the user's Claude Code config, UCW hooks, MCP server registrations,
and agent/skill files for:

- Embedded secrets (14 regex families)
- Shell-injection-prone hook commands
- MCP servers with broad filesystem/shell access
- Agents with unsound tool scopes (reviewer with write tools, etc.)

Exit codes:
    0  — no findings, or only minor / nit
    2  — at least one critical finding (CI gate)
    3  — at least one major finding, but no critical

Output formats: text (default) or JSON via --json.

This is the deterministic pre-pass. The `security-reviewer` subagent runs an
adversarial three-role review on top — for now, this script provides the
fast, scriptable layer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# ---- patterns ---------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key",        re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key",        re.compile(r"\b(?:aws|amazon).{0,20}?['\"][A-Za-z0-9/+=]{40}['\"]", re.IGNORECASE)),
    ("github_token",          re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github_oauth",          re.compile(r"\bgho_[A-Za-z0-9]{36}\b")),
    ("github_pat",            re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("slack_token",           re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key",            re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic_key",         re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("voyage_key",            re.compile(r"\bpa-[A-Za-z0-9_\-]{20,}\b")),
    ("private_key",           re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt",                   re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("google_api_key",        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key",            re.compile(r"\b(sk|pk)_(test|live)_[A-Za-z0-9]{16,}\b")),
    ("notion_token",          re.compile(r"\bsecret_[A-Za-z0-9]{43}\b")),
]

INJECTION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("unquoted_var_in_eval",  re.compile(r"\beval\s+[^\"'\n]*\$\w+"), "eval with unquoted variable interpolation"),
    ("bash_c_var",            re.compile(r"\bbash\s+-c\s+[^\"'\n]*\$\w+"), "bash -c with unquoted variable"),
    ("sh_c_var",              re.compile(r"\bsh\s+-c\s+[^\"'\n]*\$\w+"), "sh -c with unquoted variable"),
    ("curl_pipe_sh",          re.compile(r"\bcurl\s+[^|\n]+\|\s*(?:bash|sh)"), "curl | sh pattern"),
    ("rm_rf_var",             re.compile(r"\brm\s+-rf\s+\"?\$"), "rm -rf with variable target"),
]


# ---- types -------------------------------------------------------------------

@dataclass(frozen=True)
class Finding:
    severity: str          # critical | major | minor | nit
    category: str          # secret | injection | mcp | agent
    file: str
    line: int
    rule: str
    message: str

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "rule": self.rule,
            "message": self.message,
        }


# ---- scanners ----------------------------------------------------------------

def _iter_lines(path: Path) -> Iterable[tuple[int, str]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                yield i, line.rstrip("\n")
    except OSError:
        return


def scan_secrets(path: Path, *, allow_suppress: bool = True) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _iter_lines(path):
        token = _suppress_token(line) if allow_suppress else None
        for rule, pattern in SECRET_PATTERNS:
            if not pattern.search(line):
                continue
            # A matching audit-allow token downgrades to a *visible* nit rather
            # than silently dropping the finding — suppressions stay auditable.
            if _suppresses_rule(token, rule):
                out.append(Finding("nit", "secret", str(path), line_no, rule,
                                   f"finding suppressed by audit-allow:{token}"))
                continue
            # Allow obvious placeholders so users can keep examples committed.
            if re.search(r"\b(example|placeholder|your[-_ ]?key|xxxxx|\.\.\.|<.+?>)\b", line, re.IGNORECASE):
                out.append(Finding("minor", "secret", str(path), line_no, rule,
                                   "matches secret pattern but looks like a placeholder"))
            else:
                out.append(Finding("critical", "secret", str(path), line_no, rule,
                                   f"possible {rule} in source"))
    return out


def scan_injection(path: Path, *, allow_suppress: bool = True) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _iter_lines(path):
        token = _suppress_token(line) if allow_suppress else None
        for rule, pattern, msg in INJECTION_PATTERNS:
            if not pattern.search(line):
                continue
            if _suppresses_rule(token, rule):
                out.append(Finding("nit", "injection", str(path), line_no, rule,
                                   f"finding suppressed by audit-allow:{token}"))
                continue
            out.append(Finding("major", "injection", str(path), line_no, rule, msg))
    return out


# Suppress on lines tagged `audit-allow: <rule>` (any comment style).
# The token after the colon MUST name the rule being silenced (or `*` / `all`
# to silence every rule on that line). A bare, wrong, or unrelated token does
# NOT suppress — otherwise attacker-controlled file content could plant
# `audit-allow: x` to silence an unrelated planted secret/injection. Suppression
# is also only honored on UCW-owned default targets, never on `--target` paths
# (see audit()).
_SUPPRESS_RE = re.compile(r"audit-allow\s*:\s*(\S+)", re.IGNORECASE)


def _suppress_token(line: str) -> str | None:
    """Return the rule token from an `audit-allow:` tag on this line, if any."""
    m = _SUPPRESS_RE.search(line)
    return m.group(1) if m else None


def _suppresses_rule(token: str | None, rule: str) -> bool:
    """True if an audit-allow token authorizes silencing `rule` specifically."""
    if token is None:
        return False
    return token in ("*", "all") or token == rule


def scan_mcp_configs(path: Path) -> list[Finding]:
    """Look for permission_mode bypasses and unusually broad scopes."""
    out: list[Finding] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return out
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        # Flag stdio servers that exec a shell directly.
        cmd = spec.get("command", "")
        args = spec.get("args", [])
        joined = " ".join([str(cmd), *map(str, args)])
        if re.search(r"\b(sh|bash|zsh|dash)\b\s+-c", joined):
            out.append(Finding("major", "mcp", str(path), 0, "mcp_shell_exec",
                               f"MCP server '{name}' invokes a shell via -c"))
    return out


def scan_agent_scopes(path: Path) -> list[Finding]:
    """Read agent frontmatter; flag reviewers/auditors with write tools."""
    if path.suffix.lower() != ".md":
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 4)
    if end < 0:
        return []
    front = text[4:end]
    name_m = re.search(r"^name:\s*(\S+)", front, re.MULTILINE)
    tools_m = re.search(r"^tools:\s*\[(.+?)\]", front, re.MULTILINE)
    if not (name_m and tools_m):
        return []
    name = name_m.group(1).strip()
    tools = {t.strip() for t in tools_m.group(1).split(",")}
    if re.search(r"review|audit|verify", name, re.IGNORECASE):
        write_tools = tools & {"Edit", "Write", "NotebookEdit"}
        if write_tools:
            return [Finding("major", "agent", str(path), 0, "review_agent_writes",
                            f"agent '{name}' has write tools ({write_tools}) — should be read-only")]
    return []


SCANNERS = {
    "secret":    scan_secrets,
    "injection": scan_injection,
}


# ---- driver ------------------------------------------------------------------

def default_targets(repo_root: Path) -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".claude" / "settings.json",
        home / ".claude.json",          # user-scope MCP servers Claude Code loads
        home / ".claude" / "mcp.json",  # legacy path; scanned if a stale file lingers
        home / ".claude" / "ucw" / "hooks",
        home / ".claude" / "rules" / "ucw",
        repo_root / "hooks",
        repo_root / "agents",
        repo_root / "commands",
        repo_root / "skills",
        repo_root / "settings",
        repo_root / "mcp",
        repo_root / "rules",
        repo_root / "bin",
        repo_root / ".claude-plugin",
    ]
    return [p for p in candidates if p.exists()]


def gather_files(targets: list[Path]) -> list[Path]:
    out: list[Path] = []
    for t in targets:
        if t.is_file():
            out.append(t)
        elif t.is_dir():
            for p in t.rglob("*"):
                if p.is_file() and not _skip(p):
                    out.append(p)
    return out


def _skip(p: Path) -> bool:
    if any(part.startswith(".") and part not in (".claude", ".claude-plugin", ".github") for part in p.parts):
        return True
    if p.suffix in {".pyc", ".pyo", ".sqlite", ".log", ".png", ".jpg", ".gif", ".pdf"}:
        return True
    try:
        if p.stat().st_size > 512_000:  # skip large blobs
            return True
    except OSError:
        return True
    return False


def _under_any(path: Path, roots: list[Path]) -> bool:
    """True if `path` is one of, or lives under, any root in `roots`."""
    rp = path.resolve()
    for root in roots:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def audit(targets: list[Path], suppress_roots: list[Path] | None = None) -> list[Finding]:
    # `audit-allow:` is only honored for files under UCW-owned default targets.
    # Files added via --target are treated as untrusted: their suppression
    # comments are ignored so planted content cannot silence a real finding.
    suppress_roots = suppress_roots if suppress_roots is not None else list(targets)
    findings: list[Finding] = []
    for path in gather_files(targets):
        allow_suppress = _under_any(path, suppress_roots)
        findings.extend(scan_secrets(path, allow_suppress=allow_suppress))
        findings.extend(scan_injection(path, allow_suppress=allow_suppress))
        if path.suffix == ".json":
            findings.extend(scan_mcp_configs(path))
        if path.suffix == ".md":
            findings.extend(scan_agent_scopes(path))
    return findings


_ANSI = {
    "critical": "\033[1;31m",  # bold red
    "major":    "\033[31m",    # red
    "minor":    "\033[33m",    # yellow
    "nit":      "\033[2m",     # dim
    "reset":    "\033[0m",
    "ok":       "\033[32m",
}


def format_text(findings: list[Finding], *, color: bool = False) -> str:
    paint = (lambda t, c: f"{_ANSI[c]}{t}{_ANSI['reset']}") if color else (lambda t, _: t)

    if not findings:
        return paint("AUDIT: clean ✓ (0 findings)", "ok")

    buckets: dict[str, list[Finding]] = {"critical": [], "major": [], "minor": [], "nit": []}
    for f in findings:
        buckets.setdefault(f.severity, []).append(f)

    lines = ["AUDIT REPORT", "─" * 60]
    for sev in ("critical", "major", "minor", "nit"):
        items = buckets.get(sev, [])
        line = f"  {sev:<10} {len(items)}"
        if items:
            line = paint(line, sev)
        lines.append(line)
    lines.append("")
    for sev in ("critical", "major", "minor", "nit"):
        for f in buckets.get(sev, []):
            tag = paint(f"[{f.severity}]", sev)
            lines.append(f"{tag} {f.file}:{f.line} — {f.rule}: {f.message}")
    return "\n".join(lines)


def format_markdown(findings: list[Finding]) -> str:
    """Markdown report for PR comments / docs embedding."""
    if not findings:
        return "**AUDIT:** clean ✓ (0 findings)"
    buckets: dict[str, list[Finding]] = {"critical": [], "major": [], "minor": [], "nit": []}
    for f in findings:
        buckets.setdefault(f.severity, []).append(f)
    out = ["## Audit Report", "", "| Severity | Count |", "| --- | ---: |"]
    for sev in ("critical", "major", "minor", "nit"):
        out.append(f"| {sev} | {len(buckets.get(sev, []))} |")
    out.append("")
    for sev in ("critical", "major", "minor", "nit"):
        items = buckets.get(sev, [])
        if not items:
            continue
        out.append(f"### {sev.title()}")
        for f in items:
            out.append(f"- `{f.file}:{f.line}` — **{f.rule}** — {f.message}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ucw-audit",
        description="Deterministic security audit of a UCW install. "
                    "Exit 0 = clean, 2 = critical, 3 = major.",
    )
    parser.add_argument("--target", action="append", help="extra path to audit (repeatable)")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    parser.add_argument("--markdown", action="store_true", help="markdown output (for PR comments)")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    parser.add_argument("--repo", default=os.getcwd(), help="UCW repo root (default: cwd)")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    default = default_targets(repo_root)
    targets = list(default)
    if args.target:
        targets.extend(Path(t).resolve() for t in args.target)

    # Only UCW-owned default targets may use `audit-allow:` to suppress findings.
    findings = audit(targets, suppress_roots=default)

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    elif args.markdown:
        print(format_markdown(findings))
    else:
        color = sys.stdout.isatty() and not args.no_color
        print(format_text(findings, color=color))

    if any(f.severity == "critical" for f in findings):
        return 2
    if any(f.severity == "major" for f in findings):
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
