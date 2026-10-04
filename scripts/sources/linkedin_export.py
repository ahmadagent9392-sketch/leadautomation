#!/usr/bin/env python3
"""Warm leads from Ahmad's OWN LinkedIn data export (Stage 7b). Never opens LinkedIn.

Usage:
    python scripts/sources/linkedin_export.py [--connections data/linkedin/Connections.csv]
                                              [--messages data/linkedin/messages.csv] [--max 40] [--min-warmth 3]
                                              [--dry-run] [--backend sqlite --db PATH]
Get the files: LinkedIn -> Settings -> Data privacy -> Get a copy of your data -> Connections (+ Messages).
Put them in data/linkedin/ (git-ignored; never uploaded anywhere).

What is saved (as found items, source 'linkedin', for /import-linkedin):
  name, position, company, connected date, profile URL (stored only, never opened),
  and from messages.csv only "talked N times, last on DATE, two-way yes/no".
Never saved: email addresses, message text.
Warmth = role (founder/owner/CEO... 3, director/head 2, manager 1) + fits a problem pattern (owner roles,
keywords) + agency words + talked before (two-way 3, one-way 1, in the last year +1).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

db = common.db
DEFAULT_DIR = db.ROOT / "data" / "linkedin"
ROLE_TOP = ("founder", "co-founder", "cofounder", "owner", "ceo", "chief executive", "managing director",
            "president", "principal", "proprietor", "partner", "managing partner")
ROLE_MID = ("director", "head of", "vp ", "vice president", "general manager", "coo", "cto", "cmo",
            "chief operating", "chief technology", "chief marketing")
ROLE_LOW = ("manager", "lead", "administrator")
AGENCY_WORDS = ("agency", "studio", "consultancy", "consulting", "digital", "marketing", "development shop")
PROFILE_RE = re.compile(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/[^/?#\s]+/?$", re.IGNORECASE)


def _has(text: str, words) -> list[str]:
    t = f" {text.lower()} "
    return [w for w in words if re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", t)]


def _read_csv(path: Path) -> list[dict]:
    """LinkedIn puts 'Notes:' lines above the header. Find the header row, then read the rest."""
    lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.lower().startswith(("first name,", "conversation id,"))), None)
    if start is None:
        raise db.DeskError(f"{Path(path).name}: no LinkedIn header row found (First Name,... or CONVERSATION ID,...)")
    return list(csv.DictReader(lines[start:]))


def _connected_on(text: str) -> str | None:
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_connections(path: Path) -> list[dict]:
    out = []
    for row in _read_csv(path):
        get = lambda k: (row.get(k) or "").strip()
        out.append({"name": " ".join(x for x in (get("First Name"), get("Last Name")) if x),
                    "url": get("URL"), "company": get("Company"), "position": get("Position"),
                    "connected_on": _connected_on(get("Connected On"))})
        # "Email Address" is never read on purpose: warm leads do not need it and it is not public proof
    return out


def parse_messages(path: Path) -> dict[str, dict]:
    """{profile url: {"messages": n, "two_way": bool, "last": "YYYY-MM-DD"}}. Message text is never kept."""
    rows = _read_csv(path)
    count: dict[str, int] = {}
    for r in rows:
        for u in _people(r):
            count[u] = count.get(u, 0) + 1
    if not count:
        return {}
    me = max(count, key=count.get)                   # Ahmad is in every conversation
    out: dict[str, dict] = {}
    for r in rows:
        sender = _norm(r.get("SENDER PROFILE URL"))
        day = (r.get("DATE") or "")[:10]
        for u in _people(r):
            if u == me:
                continue
            info = out.setdefault(u, {"messages": 0, "two_way": False, "last": None})
            info["messages"] += 1
            info["two_way"] = info["two_way"] or sender == u
            if re.match(r"^\d{4}-\d{2}-\d{2}$", day) and (info["last"] or "") < day:
                info["last"] = day
    return out


def _norm(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def _people(row: dict) -> set[str]:
    urls = {_norm(row.get("SENDER PROFILE URL"))}
    urls |= {_norm(u) for u in re.split(r"[,\s]+", row.get("RECIPIENT PROFILE URLS") or "")}
    return {u for u in urls if u}


def warmth(contact: dict, talk: dict | None, patterns: list[dict], today: date) -> tuple[int, list[str]]:
    position, company = contact.get("position") or "", contact.get("company") or ""
    score, why = 0, []
    if hits := _has(position, ROLE_TOP):
        score, why = 3, [f"role: {hits[0]}"]
    elif hits := _has(position, ROLE_MID):
        score, why = 2, [f"role: {hits[0].strip()}"]
    elif hits := _has(position, ROLE_LOW):
        score, why = 1, [f"role: {hits[0]}"]
    roles = sorted({str(r) for p in patterns for r in p.get("owner_roles") or []})
    if _has(position, roles):
        score += 1
        why.append("owns a problem in your patterns")
    keywords = common.all_keywords(patterns)
    if hits := common.keyword_hits(f"{position} {company}", keywords):
        score += 1
        why.append(f"keyword: {hits[0]}")
    if _has(f"{position} {company}", AGENCY_WORDS):
        score += 1
        why.append("agency")
    if talk:
        score += 3 if talk["two_way"] else 1
        why.append(f"talked {talk['messages']}x" + (" (two-way)" if talk["two_way"] else " (only you wrote)"))
        if talk.get("last") and (today - date.fromisoformat(talk["last"])).days <= 365:
            score += 1
            why.append(f"last message {talk['last']}")
    return score, why


def candidates(desk: db.Desk, connections: list[dict], talks: dict[str, dict], min_warmth: int,
               tally: common.Tally) -> list[dict]:
    patterns = desk.problems.get("patterns") or []
    out = []
    for c in connections:
        if not PROFILE_RE.match(c["url"] or ""):
            tally.add("no_profile_url")
            continue
        if not c["company"]:
            tally.add("no_company")
            continue
        talk = talks.get(_norm(c["url"]))
        score, why = warmth(c, talk, patterns, desk.today())
        if score < min_warmth:
            tally.add("not_warm")
            continue
        out.append({**c, "warmth": score, "why": why, "talk": talk})
    out.sort(key=lambda c: (-c["warmth"], c["name"].lower()))
    return out


def item_text(c: dict) -> str:
    talk = c.get("talk")
    lines = [f"Name: {c['name']}", f"Position: {c['position'] or 'unknown'}", f"Company: {c['company']}",
             f"Connected on: {c['connected_on'] or 'unknown'}",
             "Messages: " + (f"{talk['messages']}, last {talk['last'] or 'unknown'}, "
                             f"{'two-way' if talk['two_way'] else 'only Ahmad wrote'}" if talk else "none"),
             f"Warmth: {c['warmth']} ({'; '.join(c['why'])})",
             "Source: Ahmad's own LinkedIn export. Profile is NOT opened by the desk."]
    return "\n".join(lines)


def run(desk: db.Desk, connections_path: Path, messages_path: Path | None, *, max_items: int = 40,
        min_warmth: int = 3, dry_run: bool = False) -> tuple[common.Tally, list[dict]]:
    if not Path(connections_path).exists():
        raise db.DeskError(f"{connections_path} not found. Download your LinkedIn data and put Connections.csv "
                           "in data/linkedin/")
    talks = parse_messages(messages_path) if messages_path and Path(messages_path).exists() else {}
    tally = common.Tally()
    found = candidates(desk, parse_connections(connections_path), talks, min_warmth, tally)
    saved = []
    for c in found[:max_items]:
        result, raw_id = common.save_item(
            desk, source="linkedin", url=c["url"], title=f"{c['name']} - {c['position'] or '?'} at {c['company']}",
            text=item_text(c), posted_at=None, query="linkedin export", dry_run=dry_run,
            matched={"warmth": c["warmth"], "why": c["why"]},
            extra={"name": c["name"], "position": c["position"], "company": c["company"],
                   "connected_on": c["connected_on"], "warmth": c["warmth"], "why": c["why"],
                   "messages": (c["talk"] or {}).get("messages", 0), "two_way": bool((c["talk"] or {}).get("two_way")),
                   "last_message": (c["talk"] or {}).get("last")})
        tally.add(result)
        if result in ("saved", "dry_run"):
            saved.append({**c, "raw_id": raw_id})
    if len(found) > max_items:
        tally.counts["over_max"] = len(found) - max_items
    return tally, saved


def main(argv: list[str] | None = None) -> int:
    common.utf8_stdout()
    ap = argparse.ArgumentParser(description="Warm leads from your own LinkedIn export (never opens LinkedIn).")
    ap.add_argument("--connections", type=Path, default=DEFAULT_DIR / "Connections.csv")
    ap.add_argument("--messages", type=Path, default=DEFAULT_DIR / "messages.csv")
    ap.add_argument("--max", type=int, default=40, help="save at most this many (warmest first)")
    ap.add_argument("--min-warmth", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    ap.add_argument("--db", type=Path, default=None)
    args = ap.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        tally, saved = run(db.Desk(store), args.connections, args.messages, max_items=args.max,
                           min_warmth=args.min_warmth, dry_run=args.dry_run)
    except (db.DeskError, OSError, csv.Error) as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()
    for c in saved:
        rid = f"R{c['raw_id']}" if c.get("raw_id") else "(dry run)"
        print(f"  {rid:9} warmth {c['warmth']}  {c['name']} - {c['position'] or '?'} at {c['company']}  "
              f"[{'; '.join(c['why'])}]")
    print(tally.line("linkedin export").replace("no_profile_url", "no profile link")
          .replace("no_company", "no company").replace("not_warm", "not warm enough"))
    print("Emails and message text are never saved. Next: /import-linkedin keeps the real warm leads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
