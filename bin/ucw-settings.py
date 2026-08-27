#!/usr/bin/env python3
"""Read or write per-project UCW settings.

Settings live at `.ucw/state/settings.json` in the current project.
Only keys in the typed registry below are accepted — the registry is a
closed set of behavior toggles, so no key ever stores a secret or
credential (secrets belong in env vars managed outside UCW).

Resolution order for reads (first hit wins):
  1. `UCW_<KEY>` env var — dots become underscores, uppercased
     (e.g. `kimi.offload` → `UCW_KIMI_OFFLOAD`)
  2. `.ucw/state/settings.json` project override
  3. registry default

An env var that fails validation for its key's type is ignored and
resolution falls through — same escape-hatch tolerance as ucw-auto.

Usage:
    ucw-settings.py [--repo <path>] list
    ucw-settings.py [--repo <path>] get <key>
    ucw-settings.py [--repo <path>] set <key> <value>
    ucw-settings.py [--repo <path>] unset <key>

`--repo` pins the project root explicitly (same convention as
ucw-review.py); without it the root is found by walking up from cwd
to the first directory containing `.ucw/` or `.git`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

STATE_FILENAME = "settings.json"

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Spec:
    type: str  # "bool" | "int" | "str" | "enum"
    default: object
    help: str
    choices: tuple[str, ...] | None = None


REGISTRY: dict[str, Spec] = {
    "kimi.review": Spec(
        "bool", False, "treat --with-kimi as default on /ucw review"),
    "kimi.disprover": Spec(
        "enum", "haiku", "default disprover route", choices=("haiku", "kimi")),
    "kimi.offload": Spec(
        "bool", False,
        "honor [kimi] plan-task tags — kill-switch that keeps unattended "
        "auto-mode runs from spending Kimi tokens"),
    "kimi.model": Spec(
        "str", "kimi-k3",
        "model id passed to the claude-kimi bridge (consulted by "
        "kimi_invoke when --model absent; exported to the subprocess "
        "as KIMI_MODEL)"),
    "kimi.timeout_secs": Spec(
        "int", 300,
        "per-invocation timeout in seconds for any headless Kimi call "
        "(consulted by kimi_invoke when --timeout absent)"),
    "kimi.transport": Spec(
        "enum", "auto",
        "Kimi bridge transport (consulted by kimi_invoke): claude-kimi = "
        "API-key wrapper, kimi-cli = standalone subscription CLI, auto = "
        "claude-kimi with kimi-cli fallback on timeout/api-error for "
        "tool-less calls",
        choices=("auto", "claude-kimi", "kimi-cli")),
    "review.default": Spec(
        "enum", "full",
        "default review depth (consulted by /ucw review dispatch when "
        "--full/--quick absent)",
        choices=("full", "quick")),
    "ship.push": Spec(
        "bool", True,
        "push the branch after commit (consulted by /ucw ship land step "
        "when --no-push absent)"),
    "ship.pr": Spec(
        "bool", False,
        "open a draft PR after push (consulted by /ucw ship land step "
        "when --pr absent)"),
    "scribe.auto": Spec(
        "bool", True,
        "run the scribe automatically after Land (consulted by /ucw ship "
        "scribe step)"),
    "auto.default_level": Spec(
        "int", 4,
        "auto-mode level 1-4 used by bare `/ucw auto on` (consulted by "
        "ucw-auto when the level argument is absent)"),
    "auto.retry_cap": Spec(
        "int", 3,
        "max consecutive verify-fail retries at auto-mode level 2+ "
        "(consulted by the stop-hook retry loop when `on --retry-cap` "
        "absent)"),
}


def _project_root(start: Path | None = None) -> Path:
    cwd = start or Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".ucw").is_dir() or (parent / ".git").exists():
            return parent
    return cwd


def _settings_path(project_root: Path | None = None) -> Path:
    root = project_root or _project_root()
    return root / ".ucw" / "state" / STATE_FILENAME


def _env_name(key: str) -> str:
    return "UCW_" + key.replace(".", "_").upper()


def _parse_value(spec: Spec, raw: str) -> object:
    """Parse a string (CLI arg or env var) into the spec's type.

    Raises ValueError with a human-readable message on bad input.
    """
    if spec.type == "bool":
        lowered = raw.strip().lower()
        if lowered in TRUTHY:
            return True
        if lowered in FALSY:
            return False
        raise ValueError(
            f"expected a boolean ({'/'.join(sorted(TRUTHY | FALSY))}), got: {raw!r}")
    if spec.type == "int":
        try:
            return int(raw.strip())
        except ValueError:
            raise ValueError(f"expected an integer, got: {raw!r}") from None
    if spec.type == "enum":
        value = raw.strip()
        if spec.choices is None:  # registry bug, not user error
            raise RuntimeError("enum spec without choices in registry")
        if value not in spec.choices:
            raise ValueError(
                f"expected one of {list(spec.choices)}, got: {raw!r}")
        return value
    # str — taken as-is
    return raw


def _stored_value_valid(spec: Spec, value: object) -> bool:
    """True when a JSON value read from settings.json matches the spec."""
    if spec.type == "bool":
        return isinstance(value, bool)
    if spec.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.type == "enum":
        if spec.choices is None:  # registry bug, not user error
            raise RuntimeError("enum spec without choices in registry")
        return isinstance(value, str) and value in spec.choices
    return isinstance(value, str)


def _read_settings(project_root: Path | None = None) -> dict:
    sp = _settings_path(project_root)
    if not sp.exists():
        return {}
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict, project_root: Path | None = None) -> Path:
    sp = _settings_path(project_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    return sp


def resolve(key: str, project_root: Path | None = None) -> tuple[object, str]:
    """Return (effective_value, source) for a known key.

    source is one of "env" | "project" | "default".
    """
    spec = REGISTRY[key]
    raw = os.environ.get(_env_name(key), "")
    if raw.strip():
        try:
            return _parse_value(spec, raw), "env"
        except ValueError:
            pass  # invalid env value → fall through
    data = _read_settings(project_root)
    if key in data and _stored_value_valid(spec, data[key]):
        return data[key], "project"
    return spec.default, "default"


def _reject_unknown(key: str) -> int:
    print(json.dumps({"error": f"unknown key: {key}",
                      "known": sorted(REGISTRY)}), file=sys.stderr)
    return 2


def _root_from(args: argparse.Namespace) -> Path | None:
    """Project root from --repo, or None to fall back to the cwd walk."""
    repo = getattr(args, "repo", None)
    return Path(repo) if repo else None


def cmd_list(args: argparse.Namespace) -> int:
    root = _root_from(args)
    rows = []
    for key in sorted(REGISTRY):
        spec = REGISTRY[key]
        value, source = resolve(key, root)
        row = {
            "key": key,
            "value": value,
            "source": source,
            "type": spec.type,
            "default": spec.default,
            "help": spec.help,
        }
        if spec.choices:
            row["choices"] = list(spec.choices)
        rows.append(row)
    print(json.dumps({"settings": rows}))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    if args.key not in REGISTRY:
        return _reject_unknown(args.key)
    value, source = resolve(args.key, _root_from(args))
    print(json.dumps({"key": args.key, "value": value, "source": source}))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    if args.key not in REGISTRY:
        return _reject_unknown(args.key)
    spec = REGISTRY[args.key]
    try:
        value = _parse_value(spec, args.value)
    except ValueError as exc:
        print(json.dumps({"error": f"invalid value for {args.key}: {exc}",
                          "type": spec.type}), file=sys.stderr)
        return 2
    root = _root_from(args)
    data = _read_settings(root)
    data[args.key] = value
    sp = _write_settings(data, root)
    print(json.dumps({"key": args.key, "value": value, "path": str(sp)}))
    return 0


def cmd_unset(args: argparse.Namespace) -> int:
    if args.key not in REGISTRY:
        return _reject_unknown(args.key)
    root = _root_from(args)
    data = _read_settings(root)
    existed = args.key in data
    if existed:
        del data[args.key]
        _write_settings(data, root)
    print(json.dumps({"key": args.key, "cleared": existed,
                      "default": REGISTRY[args.key].default}))
    return 0


def _add_repo(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--repo", default=None,
        help="project root (default: walk up from cwd to .ucw/ or .git)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ucw-settings")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list")
    _add_repo(p_list)
    p_list.set_defaults(func=cmd_list)

    p_get = sub.add_parser("get")
    p_get.add_argument("key")
    _add_repo(p_get)
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set")
    p_set.add_argument("key")
    p_set.add_argument("value")
    _add_repo(p_set)
    p_set.set_defaults(func=cmd_set)

    p_unset = sub.add_parser("unset")
    p_unset.add_argument("key")
    _add_repo(p_unset)
    p_unset.set_defaults(func=cmd_unset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
