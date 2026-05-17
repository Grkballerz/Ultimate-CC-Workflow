#!/usr/bin/env python3
"""Render `.ucw/knowledge/*.md` from detected stack + user preferences.

The onboarder agent calls this after stack detection and the preference
interview. It's a pure function — input JSON in, files written out.

Usage:
    ucw-render-knowledge.py --state state.json --project-root . [--force]

State JSON shape:
    {
      "stack": { ... output of ucw-detect-stack.py ... },
      "preferences": {
        "package_manager": "pnpm", "runtime": "node@20",
        "test_runner": "vitest", "formatter": "prettier",
        "image_gen_tool": "Flux", "video_gen_tool": "Runway",
        "svg_tool": "Inkscape CLI", "audio_tool": "ElevenLabs",
        "diagram_tool": "Mermaid",
        "database": "postgres", "deploy_target": "vercel",
        "browser_automation": "Playwright",
        "docs_surface": "Plain `.ucw/knowledge/`",
        "embedding_provider": "Voyage",
        "scribe_mode": "auto",
        "notification_channel": "desktop only"
      },
      "project_summary": "optional one-liner from user",
      "init_at": "2026-05-17"
    }

Files written (under <project>/.ucw/knowledge/):
    INDEX.md, STACK.md, DESIGN.md, CONVENTIONS.md,
    GLOSSARY.md, ROADMAP.md, PREFERENCES.md
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "knowledge"

# Files we render. Order matters only for INDEX summary generation.
TEMPLATES = [
    ("STACK.md.tmpl",       "STACK.md"),
    ("CONVENTIONS.md.tmpl", "CONVENTIONS.md"),
    ("DESIGN.md.tmpl",      "DESIGN.md"),
    ("GLOSSARY.md.tmpl",    "GLOSSARY.md"),
    ("ROADMAP.md.tmpl",     "ROADMAP.md"),
    ("PREFERENCES.md.tmpl", "PREFERENCES.md"),
    ("INDEX.md.tmpl",       "INDEX.md"),  # last — depends on others
]


def _list_or_blank(items: list[str], sep: str = ", ") -> str:
    return sep.join(items) if items else "_(none detected)_"


def _bullet_list(items: list[str]) -> str:
    return "\n- ".join(items) if items else "_(none detected)_"


def build_substitutions(state: dict[str, Any]) -> dict[str, str]:
    stack = state.get("stack", {})
    prefs = state.get("preferences", {})

    languages = stack.get("languages", [])
    frameworks = stack.get("frameworks", [])
    package_managers = stack.get("package_managers", [])
    test_runners = stack.get("test_runners", [])
    linters = stack.get("linters", [])
    formatters = stack.get("formatters", [])
    datastores = stack.get("datastores", [])
    deploy_targets = stack.get("deploy_targets", [])
    runtimes = stack.get("runtimes", [])
    evidence_files = [e.get("file", "") for e in stack.get("evidence", [])]

    today = state.get("init_at") or datetime.date.today().isoformat()

    def pref(key: str, default: str = "_(not set — JIT prompt on first use)_") -> str:
        return prefs.get(key) or default

    # Build short summaries for INDEX
    stack_summary = ", ".join(languages + frameworks) or "_unknown_"
    pref_summary = ", ".join(f"{k}={v}" for k, v in prefs.items() if v) or "_unset_"

    return {
        # STACK
        "languages":       _bullet_list(languages),
        "frameworks":      _bullet_list(frameworks),
        "package_manager": _list_or_blank(package_managers) if not prefs.get("package_manager") else prefs["package_manager"],
        "build_tools":     _bullet_list(stack.get("build_tools", [])),
        "test_framework":  _list_or_blank(test_runners) if not prefs.get("test_runner") else prefs["test_runner"],
        "lint_format":     _list_or_blank(sorted(set(linters) | set(formatters))),
        "key_libraries":   _bullet_list(stack.get("key_libraries", [])),
        "deploy_target":   _list_or_blank(deploy_targets) if not prefs.get("deploy_target") else prefs["deploy_target"],
        "datastores":      _list_or_blank(datastores) if not prefs.get("database") else prefs["database"],
        "detected_from":   ", ".join(evidence_files) or "(no manifest files found)",
        "detected_at":     today,

        # CONVENTIONS — start with sensible defaults derived from stack
        "naming":          "_to be filled in as patterns emerge_",
        "layout_rules":    "_to be filled in as patterns emerge_",
        "test_patterns":   f"use `{test_runners[0]}` (auto-detected)" if test_runners else "_to be filled in_",
        "error_handling":  "_to be filled in_",
        "logging":         "_to be filled in_",
        "imports":         "_to be filled in_",
        "comments_policy": "default to no comments; add only when WHY is non-obvious",
        "commit_style":    "_to be filled in — scribe will infer from recent commits_",

        # DESIGN
        "project_summary":    state.get("project_summary") or "_one-paragraph description — please edit_",
        "abstractions":       "_to be filled in by scribe as code lands_",
        "module_layout":      "_run `tree -L 2 -I node_modules` and paste here_",
        "external_boundaries":"_to be filled in_",
        "cross_cutting":      "_to be filled in_",

        # PREFERENCES (one row per key — `deploy_target` is intentionally shared
        # with the STACK section above; STACK.md uses {{deploy_target}}, and
        # PREFERENCES.md also uses {{deploy_target}}, so the merge logic lives
        # there. `database` is a distinct key from STACK's `datastores`.)
        "image_gen_tool":      pref("image_gen_tool"),
        "video_gen_tool":      pref("video_gen_tool"),
        "svg_tool":            pref("svg_tool"),
        "audio_tool":          pref("audio_tool"),
        "diagram_tool":        pref("diagram_tool"),
        "database":            pref("database", _list_or_blank(datastores)),
        "browser_automation":  pref("browser_automation"),
        "docs_surface":        pref("docs_surface", "Plain `.ucw/knowledge/`"),
        "embedding_provider":  pref("embedding_provider", "Claude Haiku reranking (no Voyage key)"),
        "scribe_mode":         pref("scribe_mode", "auto"),
        "notification_channel": pref("notification_channel", "desktop only"),
        "runtime":             pref("runtime", _list_or_blank(runtimes)),
        "test_runner":         pref("test_runner", _list_or_blank(test_runners)),
        "formatter":           pref("formatter", _list_or_blank(formatters)),
        "init_at":             today,

        # INDEX
        "stack_summary":       stack_summary,
        "design_summary":      "architecture overview + ADR-lite log",
        "conventions_summary": ", ".join(linters + formatters) or "_to be filled in_",
        "glossary_summary":    "_empty — scribe will fill in_",
        "roadmap_summary":     "_empty — scribe will fill in_",
        "preferences_summary": pref_summary,
        "last_refreshed":      today,
    }


def render_one(template_path: Path, output_path: Path, subs: dict[str, str], force: bool) -> bool:
    """Returns True if written, False if skipped because already existed without --force."""
    if output_path.exists() and not force:
        return False
    template = template_path.read_text(encoding="utf-8")
    rendered = template
    for key, value in subs.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ucw-render-knowledge")
    parser.add_argument("--state", required=True, help="path to state JSON")
    parser.add_argument("--project-root", required=True, help="project root for output")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv[1:])

    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    subs = build_substitutions(state)

    knowledge_dir = Path(args.project_root) / ".ucw" / "knowledge"
    written: list[str] = []
    skipped: list[str] = []
    for tmpl_name, out_name in TEMPLATES:
        tmpl = TEMPLATE_DIR / tmpl_name
        out = knowledge_dir / out_name
        if render_one(tmpl, out, subs, args.force):
            written.append(out_name)
        else:
            skipped.append(out_name)

    result = {
        "written": written,
        "skipped": skipped,
        "knowledge_dir": str(knowledge_dir),
    }
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except BrokenPipeError:
        sys.exit(0)
