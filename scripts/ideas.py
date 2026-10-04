#!/usr/bin/env python3
"""Temporary problem patterns for /search-idea (Stage 7): config/ideas/<name>.yaml.

Usage:
    python scripts/ideas.py new NAME --idea "dental clinics that need booking automation" \
        --keywords "front desk,appointment setter,missed calls" [--review-keywords "never called back,..."] \
        [--owner-roles "owner,office manager"] [--replace]
    python scripts/ideas.py list
    python scripts/ideas.py show NAME
    python scripts/ideas.py promote NAME      only when Ahmad says yes: copy it into config/problems.yaml

The desk reads config/ideas/*.yaml next to problems.yaml, so research, ranking and cards work with idea leads.
Pattern id = "idea-NAME". Leads found by the idea are tagged idea=NAME.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

import db

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
DEFAULT_DISQUALIFIERS = ["staffing agency posting for a client", "enterprise over 1000 staff"]


def ideas_dir(config_dir: Path | None = None) -> Path:
    return (config_dir or db.ROOT / "config") / "ideas"


def _words(text: str | None) -> list[str]:
    return [w.strip() for w in (text or "").split(",") if w.strip()]


def _check_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not NAME_RE.fullmatch(name):
        raise db.DeskError(f"'{name}' is not a good idea name. Use 2-40 small letters, numbers and '-', "
                           "like dental-booking")
    return name


def _base_pattern(config_dir: Path | None) -> dict:
    patterns = db.load_yaml("problems", config_dir).get("patterns") or []
    if not patterns:
        raise db.ConfigError("config/problems.yaml has no patterns to copy signals from")
    return patterns[0]


def new_idea(name: str, *, idea: str, keywords: list[str], review_keywords: list[str] | None = None,
             owner_roles: list[str] | None = None, replace: bool = False, config_dir: Path | None = None,
             today: date | None = None) -> Path:
    name = _check_name(name)
    if not idea.strip():
        raise db.DeskError("--idea is required (the idea in Ahmad's words)")
    if not 3 <= len(keywords) <= 15:
        raise db.DeskError(f"give 3-15 keywords (now {len(keywords)})")
    path = ideas_dir(config_dir) / f"{name}.yaml"
    if path.exists() and not replace:
        raise db.DeskError(f"idea '{name}' already exists ({path.name}). Use --replace to change it.")
    pid = f"idea-{name}"
    if any(p.get("id") == pid for p in db.load_yaml("problems", config_dir).get("patterns") or []):
        raise db.DeskError(f"pattern '{pid}' is already in config/problems.yaml")
    base = _base_pattern(config_dir)
    pattern = {
        "id": pid,
        "idea": idea.strip(),
        "created": (today or date.today()).isoformat(),
        "temporary": True,
        "offer_id": base.get("offer_id"),
        "description": idea.strip(),
        "keywords": keywords,
        "review_keywords": review_keywords or [],
        "require_tool_names": False,
        "signals": base.get("signals") or [],
        "owner_roles": owner_roles or list(base.get("owner_roles") or []),
        "disqualifiers": DEFAULT_DISQUALIFIERS,
        "angles": base.get("angles") or ["observation"],
        "min_value_band": int(base.get("min_value_band") or 2),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# /search-idea pattern (temporary). Made {pattern['created']}.\n"
              f"# Save it for good only if Ahmad says yes: python scripts/ideas.py promote {name}\n")
    path.write_text(header + yaml.safe_dump(pattern, sort_keys=False, allow_unicode=True, width=110),
                    encoding="utf-8", newline="\n")
    return path


def load_idea(name: str, config_dir: Path | None = None) -> dict:
    path = ideas_dir(config_dir) / f"{_check_name(name)}.yaml"
    if not path.exists():
        raise db.NotFound(f"idea '{name}' not found (no {path.name} in config/ideas)")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_ideas(config_dir: Path | None = None) -> list[dict]:
    folder = ideas_dir(config_dir)
    out = []
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out.append({"name": path.stem, **data})
    return out


def promote(name: str, config_dir: Path | None = None, today: date | None = None) -> str:
    """Appends the idea to problems.yaml (comments there are kept). The idea file stays, marked promoted."""
    idea = load_idea(name, config_dir)
    pid = idea.get("id")
    problems_path = (config_dir or db.ROOT / "config") / "problems.yaml"
    current = db.load_yaml("problems", config_dir).get("patterns") or []
    if any(p.get("id") == pid for p in current):
        raise db.DeskError(f"'{pid}' is already in config/problems.yaml")
    pattern = {k: v for k, v in idea.items() if k not in ("temporary", "created", "idea", "promoted")}
    block = yaml.safe_dump([pattern], sort_keys=False, allow_unicode=True, width=110)
    block = "\n".join(("  " + line) if line else line for line in block.splitlines())
    text = problems_path.read_text(encoding="utf-8").rstrip("\n")
    text += f"\n\n  # from /search-idea \"{idea.get('idea', name)}\" ({(today or date.today()).isoformat()})\n{block}\n"
    problems_path.write_text(text, encoding="utf-8", newline="\n")
    path = ideas_dir(config_dir) / f"{_check_name(name)}.yaml"
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") +
                    f"\npromoted: '{(today or date.today()).isoformat()}'\n", encoding="utf-8", newline="\n")
    return pid


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Temporary problem patterns for /search-idea.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="make config/ideas/NAME.yaml")
    p.add_argument("name")
    p.add_argument("--idea", required=True, help="the idea in Ahmad's words")
    p.add_argument("--keywords", required=True, help="3-15 search words, comma separated")
    p.add_argument("--review-keywords", help="words in Google reviews that show the problem, comma separated")
    p.add_argument("--owner-roles", help="who owns the problem, comma separated")
    p.add_argument("--replace", action="store_true")
    sub.add_parser("list", help="all ideas")
    p = sub.add_parser("show", help="one idea")
    p.add_argument("name")
    p = sub.add_parser("promote", help="copy the idea into config/problems.yaml (Ahmad said yes)")
    p.add_argument("name")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "new":
            path = new_idea(args.name, idea=args.idea, keywords=_words(args.keywords),
                            review_keywords=_words(args.review_keywords), owner_roles=_words(args.owner_roles),
                            replace=args.replace)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            print(f"Saved {path.relative_to(db.ROOT).as_posix() if path.is_relative_to(db.ROOT) else path}")
            print(f"  pattern id     : {data['id']}")
            print(f"  keywords       : {', '.join(data['keywords'])}")
            print(f"  review keywords: {', '.join(data['review_keywords']) or '-'}")
        elif args.cmd == "list":
            ideas = list_ideas()
            if not ideas:
                print("No ideas yet. /search-idea \"IDEA\" makes one.")
            for i in ideas:
                state = f"promoted {i['promoted']}" if i.get("promoted") else "temporary"
                print(f"{i['name']:<28} {state:<20} {i.get('idea', '')}")
        elif args.cmd == "show":
            print(yaml.safe_dump(load_idea(args.name), sort_keys=False, allow_unicode=True))
        elif args.cmd == "promote":
            print(f"Added pattern '{promote(args.name)}' to config/problems.yaml. "
                  "Run: python scripts/config_check.py")
        return 0
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
