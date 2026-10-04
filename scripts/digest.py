#!/usr/bin/env python3
"""Morning digest (Stage 8): docs/digest/YYYY-MM-DD.md from the database. Read only on the database.

Usage:
    python scripts/digest.py [--notes logs/notes-YYYY-MM-DD.md] [--out DIR]
Also takes --backend supabase|sqlite, --db PATH and --demo (like desk.py).

docs/digest/ is git-ignored: it has real business names. /daily-run writes the notes file (what it did, what Ahmad
must do by hand); this script adds the numbers from the database.
Also holds pipeline() and numbers(), used by the dashboard.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import cards as cards_mod
import db
import today as today_mod

DEFAULT_OUT = db.ROOT / "docs" / "digest"
NOT_REAL_REPLIES = ("bounce", "out_of_office")
NUMBER_NAMES = (("drafted", "Drafts written"), ("sent", "Messages sent"), ("replies", "Replies"),
                ("positive", "Positive replies"), ("meetings", "Meetings"), ("won", "Won"))


def pipeline(desk: db.Desk) -> dict[str, int]:
    """Leads per status, in status-flow order (only statuses that have leads)."""
    counts = {s: 0 for s in db.STATUSES}
    for lead in desk.store.list_leads():
        counts[lead["status"]] = counts.get(lead["status"], 0) + 1
    return {s: n for s, n in counts.items() if n}


def _after(ts, since: str) -> bool:
    return bool(ts) and str(ts) >= since


def numbers(desk: db.Desk, days: int | None = None) -> dict[str, int]:
    """Drafted, sent, replies, positive, meetings, won. days=None -> all time; 7 -> the last 7 days."""
    since = ("" if days is None else
             db.start_of_today_utc(desk.tz, desk.now() - timedelta(days=days - 1)).isoformat(timespec="seconds"))
    out = [m for m in desk.store.list_messages() if m.get("direction") == "out"]
    replies = [r for r in desk.store.list_replies() if (r.get("category") or "") not in NOT_REAL_REPLIES]
    moves = desk.store.events_since("status_changed", since or "2000-01-01T00:00:00+00:00")
    reached = lambda status: len({e["opportunity_id"] for e in moves if (e.get("payload") or {}).get("to") == status})
    return {
        "drafted": sum(1 for m in out if _after(m.get("created_at"), since)),
        "sent": sum(1 for m in out if _after(m.get("sent_at"), since)),
        "replies": sum(1 for r in replies if _after(r.get("created_at"), since)),
        "positive": sum(1 for r in replies if r.get("category") == "positive" and _after(r.get("created_at"), since)),
        "meetings": reached("meeting"),
        "won": reached("won"),
    }


def _since_today(desk: db.Desk) -> str:
    return db.start_of_today_utc(desk.tz, desk.now()).isoformat(timespec="seconds")


def _line(text) -> str:
    return " ".join(str(text or "").split())


def build(desk: db.Desk, notes: str | None = None) -> str:
    day = desk.today()
    since = _since_today(desk)
    leads = {l["id"]: l for l in desk.store.list_leads()}
    name = lambda lid: f"#{lid} {_line((leads.get(lid) or {}).get('company_name')) or '?'}"
    out = [f"# Morning digest - {day:%A %d %B %Y}", "",
           "Private: real business names. This folder is git-ignored. Nothing was sent.", ""]

    out.append("## Needs you today")
    sections = today_mod.report(desk)
    if not sections:
        out.append("- Nothing needs you today.")
    for title, lines in sections:
        out += ["", f"**{title} ({len(lines)})**"] + [f"- {_line(x)}" for x in lines]

    out += ["", "## New leads today"]
    added = [e for e in desk.store.events_since("lead_added", since) if e.get("opportunity_id") in leads]
    out += [f"- {name(e['opportunity_id'])} ({leads[e['opportunity_id']]['channel']}) - "
            f"{_line(leads[e['opportunity_id']].get('note'))[:100]}" for e in added] or ["- none"]

    out += ["", "## Research and ranking today"]
    moves = [e for e in desk.store.events_since("status_changed", since)
             if (e.get("payload") or {}).get("to") in ("researched", "verified", "qualified", "rejected")]
    out += [f"- {name(e['opportunity_id'])}: {e['payload'].get('from')} -> {e['payload'].get('to')} "
            f"({_line(e['payload'].get('reason'))[:100]})" for e in moves] or ["- none"]

    out += ["", "## Best cards now (top 5)"]
    top = cards_mod.collect(desk)[:5]
    out += [f"- {name(c['id'])} - priority {c['priority']} - {_line(c['why'])}" for c in top] or ["- no qualified leads"]

    out += ["", "## Drafts written today"]
    drafted = []
    for e in desk.store.events_since("draft_saved", since):
        mid = (e.get("payload") or {}).get("message_id")
        msg = desk.store.get_message(mid) if mid else None
        if msg:
            drafted.append(f"- {name(e['opportunity_id'])}: M{mid} ({msg.get('channel')}, touch "
                           f"{msg.get('touch_number') or 1}) - {desk.draft_state(msg)}")
    out += drafted or ["- none"]

    all_time, week = numbers(desk), numbers(desk, 7)
    out += ["", "## Numbers", "", "| | last 7 days | all time |", "|---|---|---|"]
    out += [f"| {label} | {week[key]} | {all_time[key]} |" for key, label in NUMBER_NAMES]
    counts = pipeline(desk)
    out += ["", "Pipeline: " + (", ".join(f"{s} {n}" for s, n in counts.items()) or "empty")]

    if notes and notes.strip():
        out += ["", "## Notes from the morning run", "", notes.strip()]
    return "\n".join(out) + "\n"


def write(desk: db.Desk, out_dir: Path = DEFAULT_OUT, notes: str | None = None) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{desk.today():%Y-%m-%d}.md"
    path.write_text(build(desk, notes), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Write the morning digest.")
    ap.add_argument("--notes", type=Path, default=None, help="notes file written by /daily-run")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--demo", action="store_true", help="made-up businesses (data/demo.db)")
    args = ap.parse_args(argv)
    notes = None
    if args.notes:
        if not args.notes.exists():
            print(f"WARNING: notes file {args.notes} not found - digest without notes.")
        else:
            notes = args.notes.read_text(encoding="utf-8")
    store = None
    try:
        store = db.open_store(args.backend, args.db, demo=args.demo)
        path = write(db.Desk(store, **db.desk_options(args.demo)), args.out, notes)
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()
    print(f"Digest written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
