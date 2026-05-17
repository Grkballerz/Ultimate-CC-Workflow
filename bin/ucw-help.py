#!/usr/bin/env python3
"""Discover what UCW gives you. Run with no args for an overview, or
`ucw-help.py <topic>` for a specific area.

    ucw-help.py                    # overview
    ucw-help.py bin                # list all CLI helpers
    ucw-help.py agents             # list subagents
    ucw-help.py commands           # list slash commands
    ucw-help.py hooks              # list hook scripts
    ucw-help.py skills             # list available skills
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Find the UCW install root — either via env var or by walking up."""
    if "UCW_HOME" in os.environ:
        ucw_home = Path(os.environ["UCW_HOME"])
        # Installed path uses symlinks; resolve to discover the source repo
        bin_link = ucw_home / "bin" / "ucw-help.py"
        if bin_link.is_symlink():
            return Path(os.readlink(bin_link)).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


REPO = _repo_root()


def _read_frontmatter(path: Path) -> tuple[dict, str]:
    """Parse a markdown file with YAML-ish frontmatter. Returns (meta, body)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    front = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    meta: dict = {}
    for line in front.splitlines():
        m = re.match(r"^(\w+):\s*(.+)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta, body


def cmd_overview(_args: argparse.Namespace) -> int:
    counts = {
        "agents":   len(list((REPO / "agents").glob("*.md"))),
        "commands": len(list((REPO / "commands").glob("*.md"))),
        "hooks":    len(list((REPO / "hooks").glob("*.py"))),
        "skills":   len(list((REPO / "skills").rglob("SKILL.md"))),
        "bin":      len(list((REPO / "bin").glob("*.py"))),
    }
    print("UCW — Ultimate Claude Code Workflow")
    print("─" * 60)
    print(f"  install:   {REPO}")
    print()
    print(f"  agents:    {counts['agents']:>3}    `ucw-help agents`")
    print(f"  commands:  {counts['commands']:>3}    `ucw-help commands`")
    print(f"  hooks:     {counts['hooks']:>3}    `ucw-help hooks`")
    print(f"  skills:    {counts['skills']:>3}    `ucw-help skills`")
    print(f"  bin tools: {counts['bin']:>3}    `ucw-help bin`")
    print()
    print("  Quick checks:")
    print(f"    {REPO / 'bin' / 'ucw-audit.py'} --repo .")
    print(f"    {REPO / 'dashboard' / 'cli.py'} status")
    print(f"    {REPO / 'scripts' / 'smoke.sh'}")
    return 0


def cmd_bin(_args: argparse.Namespace) -> int:
    print("UCW CLI helpers (in $REPO/bin/):")
    for p in sorted((REPO / "bin").glob("*.py")):
        try:
            first_doc_line = ""
            with p.open() as fh:
                txt = fh.read(2048)
                m = re.search(r'"""(.*?)(?:\n|""")', txt, re.DOTALL)
                if m:
                    first_doc_line = m.group(1).strip().split("\n")[0]
            print(f"  {p.name:<28} {first_doc_line}")
        except OSError:
            continue
    return 0


def cmd_agents(_args: argparse.Namespace) -> int:
    print("UCW subagents (in $REPO/agents/):")
    for p in sorted((REPO / "agents").rglob("*.md")):
        meta, _ = _read_frontmatter(p)
        name = meta.get("name") or p.stem
        desc = (meta.get("description") or "").split(".")[0]
        model = meta.get("model", "")
        marker = f" ({model})" if model else ""
        print(f"  {name:<22}{marker:<10}{desc[:80]}")
    return 0


def cmd_commands(_args: argparse.Namespace) -> int:
    print("UCW slash commands (in $REPO/commands/):")
    for p in sorted((REPO / "commands").glob("*.md")):
        meta, _ = _read_frontmatter(p)
        name = p.stem
        desc = meta.get("description", "")
        hint = meta.get("argument-hint", "")
        print(f"  /{name:<12}{hint:<28}{desc[:80]}")
    return 0


def cmd_hooks(_args: argparse.Namespace) -> int:
    print("UCW hooks (in $REPO/hooks/):")
    for p in sorted((REPO / "hooks").glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")[:1024]
            m = re.search(r'"""(.*?)"""', txt, re.DOTALL)
            doc = (m.group(1) if m else "").strip().split("\n")[0]
        except OSError:
            doc = ""
        print(f"  {p.name:<28} {doc}")
    return 0


def cmd_skills(_args: argparse.Namespace) -> int:
    print("UCW skills (in $REPO/skills/):")
    found = list(sorted((REPO / "skills").rglob("SKILL.md")))
    if not found:
        print("  (none yet — run /distill to promote instincts into skills)")
        return 0
    for p in found:
        meta, _ = _read_frontmatter(p)
        name = meta.get("name") or p.parent.name
        desc = (meta.get("description") or "")[:80]
        print(f"  {name:<28} {desc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-help",
                                     description="Discover what UCW gives you.")
    sub = parser.add_subparsers(dest="topic")
    for name, fn in [("bin", cmd_bin), ("agents", cmd_agents),
                     ("commands", cmd_commands), ("hooks", cmd_hooks),
                     ("skills", cmd_skills)]:
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)

    args = parser.parse_args(argv)
    if args.topic is None:
        return cmd_overview(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
