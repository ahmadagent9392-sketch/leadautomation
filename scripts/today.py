#!/usr/bin/env python3
"""What needs Ahmad today (Stage 6). Read only: it changes nothing. Run scripts/followups.py first
(the /today command does both).

Usage:
    python scripts/today.py
Also takes --backend supabase|sqlite, --db PATH and --demo (like desk.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import db

REPLY_TEXT = {
    "positive": "they are interested - answer them",
    "question": "they asked something - answer them",
    "objection": "they have a doubt - answer it, or let it go",
    "referral": "they named someone else - thank them, add the new person",
    "other": "read it and decide",
}


def _short(text: str | None, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def report(desk: db.Desk) -> list[tuple[str, list[str]]]:
    """[(section title, [lines])] - only sections with something in them."""
    today = desk.today()
    leads = {l["id"]: l for l in desk.store.list_leads()}
    messages = desk.store.list_messages()
    replies = desk.store.list_replies()
    pending = desk.store.list_follow_ups("pending")

    def name(lid: int) -> str:
        lead = leads.get(lid) or {}
        return f"#{lid} {lead.get('company_name') or '?'}"

    latest: dict[int, dict] = {}
    for m in messages:
        if m.get("direction") == "out":
            latest[m["opportunity_id"]] = m

    sections: list[tuple[str, list[str]]] = []

    # 1. replies that need Ahmad
    lines = []
    for r in replies:
        lead = leads.get(r["opportunity_id"])
        if r.get("handled_at") or not lead or lead["status"] in db.END_STATES:
            continue
        cat = r.get("category")
        if not cat:
            lines.append(f"{name(lead['id'])}: R{r['id']} not sorted yet -> run /sync")
        elif cat not in db.AUTO_HANDLED and cat != "bounce":
            ask = f" They ask: {_short(r['requested_action'], 60)}." if r.get("requested_action") else ""
            lines.append(f"{name(lead['id'])}: R{r['id']} [{cat}] {REPLY_TEXT.get(cat, 'read it')}.{ask} "
                         f"\"{_short(db.rp.strip_quoted(r['body']), 70)}\"  -> replies {lead['id']}, "
                         f"then reply-done {r['id']} --note \"...\"")
    if lines:
        sections.append(("Replies that need you", lines))

    # 2. bounced
    lines = []
    for r in replies:
        lead = leads.get(r["opportunity_id"])
        if r.get("category") == "bounce" and not r.get("handled_at") and lead and lead["status"] not in db.END_STATES:
            lines.append(f"{name(lead['id'])}: email bounced. Find another contact by hand "
                         f"(desk.py set-contact {lead['id']} ...) or close it "
                         f"(desk.py move {lead['id']} no_response --reason bounce)")
    if lines:
        sections.append(("Bounced emails", lines))

    # 3. drafts: approve, finish, send
    approve, finish, send = [], [], []
    for lid, m in latest.items():
        lead = leads.get(lid)
        if not lead or lead["status"] in db.END_STATES or m.get("sent_at"):
            continue
        state = desk.draft_state(m)
        touch = int(m.get("touch_number") or 1)
        what = "first message" if touch == 1 else f"follow-up {touch}"
        if state.startswith("ready for Ahmad") or state.startswith("CHANGED"):
            approve.append(f"{name(lid)}: {what} M{m['id']} ({m.get('channel')}) -> /approve {lid}")
        elif state == "approved by Ahmad":
            finish.append(f"{name(lid)}: {what} M{m['id']} approved, not in Gmail / file yet -> /approve {lid}")
        elif state == "in Gmail drafts":
            made = next((e["ts"] for e in reversed(desk.store.events_for(lid))
                         if e["type"] == "gmail_draft_created"
                         and (e.get("payload") or {}).get("message_id") == m["id"]), None)
            days = (today - desk.local_date(made)).days if made else 0
            send.append(f"{name(lid)}: {what} M{m['id']} waits in Gmail Drafts"
                        + (f" for {days} day(s)" if days else "") + ". Read it and press Send.")
        elif state == "approved, copy-paste file written":
            send.append(f"{name(lid)}: {what} M{m['id']} ({m.get('channel')}) - send the text from cards/ yourself, "
                        f"then: desk.py mark-sent M{m['id']}")
    if approve:
        sections.append(("Drafts waiting for your OK", approve))
    if finish:
        sections.append(("Approved, not ready to send yet", finish))
    if send:
        sections.append(("Waiting for you to press Send", send))

    # 4. follow-ups and reminders
    due, soon, nurture = [], [], []
    for f in pending:
        lead = leads.get(f["opportunity_id"])
        if not lead:
            continue
        on = str(f["due_on"])[:10]
        late = (today - db.qc.parse_date(on)).days
        if f["kind"] == "followup":
            m = latest.get(lead["id"])
            drafted = m and not m.get("sent_at") and int(m.get("touch_number") or 1) == f.get("touch_number")
            if late >= 0 and not drafted:
                due.append(f"{name(lead['id'])}: follow-up {f.get('touch_number')} due {on}"
                           + (f" ({late} day(s) late)" if late else "") + f" -> /draft {lead['id']}")
            elif 0 < -late <= 3:
                soon.append(f"{name(lead['id'])}: follow-up {f.get('touch_number')} on {on}")
        elif f["kind"] == "nurture" and late >= 0:
            nurture.append(f"{name(lead['id'])}: they said 'not now'. Time to try again (due {on}) -> /draft {lead['id']}")
    if due:
        sections.append(("Follow-ups due", due))
    if nurture:
        sections.append(("Try again ('not now' reminders)", nurture))
    if soon:
        sections.append(("Coming in the next 3 days", soon))

    # 5. stale: open lead, nothing happened for N days, no timer
    stale_days = int(db.fu.setting(desk.cadence, "stale_after_days"))
    with_timer = {f["opportunity_id"] for f in pending}
    last_activity: dict[int, str] = {}
    for lid, lead in leads.items():
        last_activity[lid] = max(str(lead.get("updated_at") or ""), str(lead.get("created_at") or ""))
    for m in messages:
        lid = m["opportunity_id"]
        for key in ("created_at", "sent_at"):
            if m.get(key):
                last_activity[lid] = max(last_activity.get(lid, ""), str(m[key]))
    for r in replies:
        last_activity[r["opportunity_id"]] = max(last_activity.get(r["opportunity_id"], ""), str(r["created_at"]))
    lines = []
    for lid, lead in leads.items():
        if lead["status"] in db.END_STATES or lid in with_timer or not last_activity.get(lid):
            continue
        quiet = (today - desk.local_date(last_activity[lid])).days
        if quiet >= stale_days:
            lines.append(f"{name(lid)} [{lead['status']}]: nothing for {quiet} days. Move it on, or close it "
                         f"(desk.py show {lid})")
    if lines:
        sections.append((f"Stale (no activity for {stale_days}+ days)", lines))

    # 6. what the desk closed by itself this week
    since = db.start_of_today_utc(desk.tz, desk.now() - timedelta(days=7)).isoformat(timespec="seconds")
    lines = [f"{name(e['opportunity_id'])}: {e['payload'].get('from')} -> {e['payload'].get('to')} "
             f"({e['payload'].get('reason')})"
             for e in desk.store.events_since("status_changed", since)
             if str(e.get("actor", "")).startswith("code:") and (e.get("payload") or {}).get("to") in db.END_STATES]
    if lines:
        sections.append(("Closed by the desk in the last 7 days", lines))
    return sections


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="What needs Ahmad today.")
    parser.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--demo", action="store_true", help="made-up businesses only (data/demo.db)")
    args = parser.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db, demo=args.demo)
        desk = db.Desk(store, **db.desk_options(args.demo))
        sections = report(desk)
        day = desk.today()
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()
    print(f"Today: {day:%A %d %B %Y}")
    if not sections:
        print("\nNothing needs you today.")
    for title, lines in sections:
        print(f"\n{title} ({len(lines)})")
        for line in lines:
            print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
