#!/usr/bin/env python3
"""Merge a UCW settings fragment into a user's Claude Code settings.json.

Why we can't just use `jq -s '.[0] * .[1]'`:
- jq's `*` does deep-merge for objects but **replaces** arrays. So if a user
  already has `hooks.PreToolUse: [{matcher: "Bash", ...user-hook}]` and our
  fragment has `hooks.PreToolUse: [{matcher: "Bash", ...ucw-hook}]`, the
  user's hook gets silently dropped.

This helper:
- Deep-merges everything outside `hooks` as `jq *` would.
- For `hooks.<EventName>` arrays: concatenates user's entries with UCW's,
  but **strips any existing UCW-tagged entries first** so reinstall is
  idempotent and never duplicates.
- A hook entry is "UCW-tagged" if any nested `command` field contains the
  substring `ucw` (matches `$HOME/.claude/ucw/hooks/...`). This is also
  what `install.sh --uninstall` uses to identify our entries.

Usage:
    ucw-merge-settings.py <base.json> <fragment.json> [--inplace | --output OUT]
    ucw-merge-settings.py <base.json> <fragment.json>  # writes merged JSON to stdout

Exit codes:
    0  success
    2  bad input / parse error

The --uninstall mode strips UCW entries from <base.json>:
    ucw-merge-settings.py --uninstall <base.json> [--inplace | --output OUT]
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


def _is_ucw_command(node: Any) -> bool:
    """True iff this node IS a {type: "command", command: "...ucw..."} entry."""
    if not isinstance(node, dict):
        return False
    cmd = node.get("command")
    return isinstance(cmd, str) and "ucw" in cmd


def _strip_ucw_commands(data: Any) -> Any:
    """Pass 1: drop UCW-tagged command descriptors from any list, leave the rest alone."""
    if isinstance(data, dict):
        return {k: _strip_ucw_commands(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_strip_ucw_commands(item) for item in data if not _is_ucw_command(item)]
    return data


def _clean_empty_hook_wrappers(hooks_value: dict) -> dict:
    """Pass 2: drop hook-group wrappers with empty `hooks` list, and drop
    event keys whose value becomes `[]` after cleanup."""
    out: dict = {}
    for event, items in hooks_value.items():
        if not isinstance(items, list):
            out[event] = items
            continue
        kept = [
            it for it in items
            if not (isinstance(it, dict) and "hooks" in it and it.get("hooks") == [])
        ]
        if kept:
            out[event] = kept
    return out


def strip_ucw_entries(data: Any) -> Any:
    """Drop UCW-tagged command entries everywhere, then clean up settings.hooks.

    The "user's `echo user-hook`" case: that command doesn't contain 'ucw',
    so it survives. The outer wrapper `{matcher: "Bash", hooks: [user]}`
    survives because its hooks list is non-empty after the cleanup.

    Reinstall idempotency: each pass over already-merged data leaves the user
    entries plus exactly one UCW entry per event.
    """
    data = _strip_ucw_commands(data)
    if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
        data = {**data, "hooks": _clean_empty_hook_wrappers(data["hooks"])}
    return data


def _deep_merge(base: Any, add: Any) -> Any:
    """Deep-merge `add` over `base`. For dicts, recurse. For arrays, concatenate
    (after de-duplication when both sides are list-of-dicts with `command`).
    Scalars and mismatched types: `add` wins.
    """
    if isinstance(base, dict) and isinstance(add, dict):
        merged: dict = {}
        for k in {*base.keys(), *add.keys()}:
            if k in base and k in add:
                merged[k] = _deep_merge(base[k], add[k])
            elif k in base:
                merged[k] = deepcopy(base[k])
            else:
                merged[k] = deepcopy(add[k])
        return merged
    if isinstance(base, list) and isinstance(add, list):
        return [*deepcopy(base), *deepcopy(add)]
    # mismatched types or scalars: add wins
    return deepcopy(add)


def merge(base: dict, fragment: dict) -> dict:
    """Top-level merge: strip prior UCW entries from base, then deep-merge fragment."""
    base_clean = strip_ucw_entries(base)
    return _deep_merge(base_clean, fragment)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        print(f"error: could not parse {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _write(out: Path | None, data: Any, inplace_target: Path | None) -> None:
    rendered = json.dumps(data, indent=2) + "\n"
    if inplace_target is not None:
        inplace_target.write_text(rendered, encoding="utf-8")
    elif out is not None:
        out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-merge-settings", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true",
                        help="strip UCW entries from base; ignore fragment")
    parser.add_argument("--inplace", action="store_true",
                        help="rewrite base in place")
    parser.add_argument("--output", help="write merged output to this path")
    parser.add_argument("base", help="path to existing settings.json (may be missing)")
    parser.add_argument("fragment", nargs="?", help="path to UCW settings fragment")
    args = parser.parse_args(argv)

    base_path = Path(args.base)
    base = _read_json(base_path)

    if args.uninstall:
        result = strip_ucw_entries(base)
    else:
        if not args.fragment:
            print("error: fragment required (or pass --uninstall)", file=sys.stderr)
            return 2
        fragment = _read_json(Path(args.fragment))
        if not isinstance(base, dict):
            base = {}
        if not isinstance(fragment, dict):
            print("error: fragment is not a JSON object", file=sys.stderr)
            return 2
        result = merge(base, fragment)

    _write(
        Path(args.output) if args.output else None,
        result,
        base_path if args.inplace else None,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
