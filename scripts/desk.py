#!/usr/bin/env python3
"""Opportunity Desk command line: add and track leads by hand.

Usage:
    python scripts/desk.py init
    python scripts/desk.py add-lead --url URL --note TEXT --channel email [--company NAME]
    python scripts/desk.py list [--status S]
    python scripts/desk.py show ID
    python scripts/desk.py move ID STATUS --reason TEXT
    python scripts/desk.py history ID
    python scripts/desk.py block EMAIL_OR_DOMAIN --reason TEXT
    python scripts/desk.py blocked

Research (Stage 3, used by the researcher and checker agents):
    python scripts/desk.py start-research ID
    python scripts/desk.py add-evidence ID --claim T --url U --quote Q --source-type S --topic T --grade G
                                           [--date YYYY-MM-DD] [--snapshot SHA] [--depends-on E1,E2]
    python scripts/desk.py set-contact ID --title T [--name N] [--role-type R] [--email E --email-status S
                                          --evidence EID] [--profile-url U]
    python scripts/desk.py set-research ID [--pattern P] [--why-now T] [--unknowns "a; b"] [--channel C]
                                           [--company-name N] [--domain D] [--industry I] [--size S] [--country C]
    python scripts/desk.py verify-evidence EID --grade G --note T [--claim T] [--snapshot SHA]

Ranking (Stage 4, used by /cards; then run scripts/rank.py):
    python scripts/desk.py set-score ID --fit 0-3 --value 1-3 --fit-why T --value-why T [--disqualifier T ...]

Every command also takes --backend supabase|sqlite (default: DESK_BACKEND in .env, else supabase)
and --db PATH (sqlite only, default data/desk.db).
Exit code: 0 = OK, 1 = refused or error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import db

REFUSED = (db.InvalidMove, db.DuplicateLead, db.Blocked, db.CapReached, db.StatusConflict)
GRADE_HELP = "CONFIRMED_FACT, STRONG_SIGNAL, WEAK_SIGNAL, INFERENCE, UNKNOWN"


def _table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    for row in [headers, ["-" * w for w in widths], *rows]:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)).rstrip())


def _short(text: str | None, n: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 3] + "..."


def cmd_init(desk: db.Desk, args) -> int:
    missing = desk.store.check()
    name = type(desk.store).__name__.replace("Store", "")
    if missing:
        print(f"ERROR: {name} is missing tables/columns: {', '.join(missing)}")
        print("Fix: Supabase dashboard -> SQL Editor -> paste supabase/schema.sql -> Run. Then run init again.")
        return 1
    print(f"{name} OK, {len(db.TABLES)} tables found.")
    return 0


def cmd_add_lead(desk: db.Desk, args) -> int:
    lead_id = desk.add_lead(args.url, args.note, args.channel, args.company)
    lead = desk.get(lead_id)
    print(f"Added lead #{lead_id}: {lead['company_name']} ({lead['channel']}), status new.")
    if not lead["company_domain"]:
        print("Note: this URL is a platform page, so the company website is not known yet. "
              "Use --company NAME if you know the company.")
    return 0


def cmd_list(desk: db.Desk, args) -> int:
    if args.status and args.status not in db.STATUSES:
        raise db.DeskError(f"unknown status '{args.status}'. Statuses: {', '.join(db.STATUSES)}")
    leads = desk.store.list_leads(args.status)
    if not leads:
        print("No leads." if not args.status else f"No leads with status '{args.status}'.")
        return 0
    _table([[l["id"], l["status"], _short(l["company_name"], 30), l["channel"],
             "" if l.get("priority") is None else l["priority"],
             db.to_local(l["created_at"], desk.tz)[:10]] for l in leads],
           ["ID", "STATUS", "COMPANY", "CHANNEL", "PRIORITY", "ADDED"])
    print(f"\n{len(leads)} lead(s).")
    return 0


def cmd_show(desk: db.Desk, args) -> int:
    lead = desk.get(args.id)
    print(f"Lead #{lead['id']}  [{lead['status']}]")
    print(f"  Company : {lead['company_name']}  (domain: {lead['company_domain'] or 'unknown'})")
    print(f"  URL     : {lead['source_url']}")
    print(f"  Channel : {lead['channel']}")
    print(f"  Note    : {lead['note']}")
    if lead.get("pattern_id"):
        print(f"  Pattern : {lead['pattern_id']}")
    if lead.get("why_now"):
        print(f"  Why now : {lead['why_now']}")
    for u in lead.get("unknowns") or []:
        print(f"  Unknown : {u}")
    info = lead.get("rank_info") or {}
    if lead.get("fit") is not None or lead.get("value_band") is not None:
        print(f"  Fit     : {lead.get('fit')}  ({info.get('fit_why', '')})")
        print(f"  Value   : {lead.get('value_band')}  ({info.get('value_why', '')})")
    for d in info.get("disqualifiers") or []:
        print(f"  Disqual.: {d}")
    if lead.get("priority") is not None:
        print(f"  Priority: {lead['priority']}  = {info.get('why', '')}")
    for w in info.get("warnings") or []:
        print(f"  Warning : {w}")
    print(f"  Added   : {db.to_local(lead['created_at'], desk.tz)}   Updated: {db.to_local(lead['updated_at'], desk.tz)}")
    if lead.get("closed_reason"):
        print(f"  Closed  : {lead['closed_reason']}")
    moves = db.allowed_moves(lead["status"])
    print(f"  Next    : {', '.join(moves) if moves else '(closed)'}")

    people = desk.store.people_for(lead["company_id"])
    print(f"\nPeople ({len(people)})")
    for p in people:
        email = f"{p['email']} ({p.get('email_status')})" if p.get("email") else "no email"
        role = f" [{p['role_type']}]" if p.get("role_type") else ""
        print(f"  - P{p['id']} {p.get('full_name') or '?'}, {p.get('title') or '?'}{role} <{email}>"
              + (f"  {p['profile_url']}" if p.get("profile_url") else ""))
    evidence = desk.store.evidence_for(lead["id"])
    print(f"\nEvidence ({len(evidence)})")
    for e in evidence:
        mark = "checked" if e.get("verified") else "not checked"
        print(f"  E{e['id']} [{e['grade']}] ({e.get('topic') or '?'}, {e.get('source_type') or '?'}, {mark}) "
              f"{_short(e['claim'], 70)}")
        if e.get("quote"):
            print(f"      \"{_short(e['quote'], 90)}\"")
        print(f"      {e['url']}  {e.get('observed_at') or 'no date'}")
        if e.get("depends_on"):
            print(f"      based on: {', '.join('E' + str(d) for d in e['depends_on'])}")
        if e.get("verifier_note"):
            print(f"      checker: {_short(e['verifier_note'], 100)}")
    events = desk.store.events_for(lead["id"])
    print(f"\nLast events (see 'history {lead['id']}' for all)")
    for ev in events[-5:]:
        print(f"  {db.to_local(ev['ts'], desk.tz)}  {_event_text(ev)}")
    return 0


def _event_text(ev: dict) -> str:
    p = ev.get("payload") or {}
    if ev["type"] == "status_changed":
        return f"{p.get('from')} -> {p.get('to')}  ({ev['actor']}: {p.get('reason')})"
    if ev["type"] == "lead_added":
        return f"lead added  ({ev['actor']}, {p.get('channel')})"
    return f"{ev['type']}  ({ev['actor']}) {p}"


def cmd_move(desk: db.Desk, args) -> int:
    old, new = desk.move(args.id, args.status, args.reason)
    print(f"Lead #{args.id}: {old} -> {new}. Saved in history.")
    return 0


def cmd_history(desk: db.Desk, args) -> int:
    lead = desk.get(args.id)
    print(f"History of lead #{lead['id']} ({lead['company_name']}), now [{lead['status']}]")
    for ev in desk.store.events_for(lead["id"]):
        print(f"  {db.to_local(ev['ts'], desk.tz)}  {_event_text(ev)}")
    return 0


def cmd_block(desk: db.Desk, args) -> int:
    value, kind, added, affected = desk.block(args.value, args.reason)
    print(f"Blocked {kind} {value}." if added else f"{value} was already on the block list.")
    if affected:
        print("WARNING: open leads use this domain. Never contact them. Consider moving them:")
        for l in affected:
            print(f"  #{l['id']} {l['company_name']} [{l['status']}]  ->  move {l['id']} opted_out --reason \"blocked\"")
    return 0


def cmd_blocked(desk: db.Desk, args) -> int:
    rows = desk.store.list_blocks()
    if not rows:
        print("Block list is empty.")
        return 0
    _table([[r["value"], r["kind"], _short(r["reason"], 40), db.to_local(r["created_at"], desk.tz)[:10]]
            for r in rows], ["VALUE", "KIND", "REASON", "ADDED"])
    return 0


# ---------- research (Stage 3) ----------
def _eid(text: str) -> int:
    try:
        return int(str(text).strip().upper().removeprefix("E"))
    except ValueError:
        raise db.DeskError(f"'{text}' is not an evidence id (like 12 or E12)") from None


def _ids(text: str | None) -> list[int]:
    return [_eid(x) for x in (text or "").split(",") if x.strip()]


def cmd_start_research(desk: db.Desk, args) -> int:
    rnd = desk.start_research(args.id)
    print(f"Research round {rnd} of {db.MAX_RESEARCH_ROUNDS} started for lead #{args.id}.")
    return 0


def cmd_add_evidence(desk: db.Desk, args) -> int:
    eid, grade, warnings = desk.add_evidence(
        args.id, claim=args.claim, url=args.url, quote=args.quote, grade=args.grade,
        source_type=args.source_type, topic=args.topic, observed_at=args.date, snapshot=args.snapshot,
        depends_on=_ids(args.depends_on))
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"Saved evidence E{eid} [{grade}] for lead #{args.id}.")
    return 0


def cmd_set_contact(desk: db.Desk, args) -> int:
    pid = desk.set_contact(args.id, title=args.title, name=args.name, role_type=args.role_type,
                           email=args.email, email_status=args.email_status, profile_url=args.profile_url,
                           evidence_id=_eid(args.evidence) if args.evidence else None)
    print(f"Saved contact P{pid} for lead #{args.id}.")
    if args.profile_url and "linkedin" in args.profile_url.lower():
        print("Note: LinkedIn link saved as text only. Ahmad opens it by hand.")
    return 0


def cmd_set_research(desk: db.Desk, args) -> int:
    unknowns = None if args.unknowns is None else args.unknowns.split(";")
    changed = desk.set_research(args.id, pattern=args.pattern, why_now=args.why_now, unknowns=unknowns,
                                channel=args.channel, company_name=args.company_name, domain=args.domain,
                                industry=args.industry, size=args.size, country=args.country)
    print(f"Saved research notes for lead #{args.id}: {', '.join(changed)}.")
    return 0


def cmd_verify_evidence(desk: db.Desk, args) -> int:
    eid = _eid(args.evidence_id)
    before, final, reasons = desk.verify_evidence(eid, args.grade, args.note, args.claim, args.snapshot)
    print(f"Checked E{eid}: {before['grade']} -> {final}.")
    for r in reasons:
        print(f"  lowered because: {r}")
    return 0


def cmd_set_score(desk: db.Desk, args) -> int:
    desk.set_score(args.id, fit=args.fit, value=args.value, fit_why=args.fit_why, value_why=args.value_why,
                   disqualifiers=args.disqualifier)
    dq = f", {len(args.disqualifier)} disqualifier(s)" if args.disqualifier else ""
    print(f"Saved score for lead #{args.id}: fit {args.fit}, value {args.value}{dq}. "
          "Now run: python scripts/rank.py")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--backend", choices=["supabase", "sqlite"], default=None,
                        help="database (default: DESK_BACKEND in .env, else supabase)")
    common.add_argument("--db", type=Path, default=None, help="SQLite file (sqlite backend only)")

    parser = argparse.ArgumentParser(description="Opportunity Desk: add and track leads.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="create / check the database")

    p = sub.add_parser("add-lead", parents=[common], help="add a lead by hand")
    p.add_argument("--url", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--channel", required=True,
                   help="email, linkedin_message, upwork_proposal, agency_pitch, referral_ask "
                        "(or short: linkedin, upwork, agency, referral)")
    p.add_argument("--company", help="company name (useful for Upwork / HN / LinkedIn links)")

    p = sub.add_parser("list", parents=[common], help="list leads")
    p.add_argument("--status")

    p = sub.add_parser("show", parents=[common], help="show one lead")
    p.add_argument("id", type=int)

    p = sub.add_parser("move", parents=[common], help="change a lead's status")
    p.add_argument("id", type=int)
    p.add_argument("status")
    p.add_argument("--reason", required=True)

    p = sub.add_parser("history", parents=[common], help="all changes of one lead")
    p.add_argument("id", type=int)

    p = sub.add_parser("block", parents=[common], help="never contact this email or domain")
    p.add_argument("value", metavar="EMAIL_OR_DOMAIN")
    p.add_argument("--reason", required=True)

    sub.add_parser("blocked", parents=[common], help="show the block list")

    p = sub.add_parser("start-research", parents=[common], help="start research on a lead (checks daily cap)")
    p.add_argument("id", type=int)

    p = sub.add_parser("add-evidence", parents=[common], help="save one fact with proof")
    p.add_argument("id", type=int)
    p.add_argument("--claim", required=True, help="the fact, in your words")
    p.add_argument("--url", required=True, help="page where the proof is")
    p.add_argument("--quote", default="", help="exact text copied from the saved snapshot (max 300 chars)")
    p.add_argument("--source-type", required=True, choices=db.SOURCE_TYPES)
    p.add_argument("--topic", required=True, choices=db.TOPICS, help="'pain' = proof of the problem")
    p.add_argument("--grade", required=True, help=GRADE_HELP)
    p.add_argument("--date", help="date shown on the source, YYYY-MM-DD")
    p.add_argument("--snapshot", help="sha256 printed by scripts/snapshot.py")
    p.add_argument("--depends-on", help="for INFERENCE: evidence ids, like E1,E2")

    p = sub.add_parser("set-contact", parents=[common], help="save the person who owns the problem")
    p.add_argument("id", type=int)
    p.add_argument("--title", required=True)
    p.add_argument("--name")
    p.add_argument("--role-type", choices=db.ROLE_TYPES)
    p.add_argument("--email", help="only a published or verified email, never a guess")
    p.add_argument("--email-status", choices=db.EMAIL_OK)
    p.add_argument("--evidence", help="evidence id of the page that shows the email / person")
    p.add_argument("--profile-url", help="profile link (LinkedIn links are saved as text only)")

    p = sub.add_parser("set-research", parents=[common], help="save research notes on a lead")
    p.add_argument("id", type=int)
    p.add_argument("--pattern", help="problem pattern id from config/problems.yaml")
    p.add_argument("--why-now")
    p.add_argument("--unknowns", help='open questions, separated by ";" (replaces the old list)')
    p.add_argument("--channel")
    p.add_argument("--company-name")
    p.add_argument("--domain", help="company website domain")
    p.add_argument("--industry")
    p.add_argument("--size", help="size band, like 1-10, 11-50")
    p.add_argument("--country")

    p = sub.add_parser("verify-evidence", parents=[common], help="checker: set the final grade of one fact")
    p.add_argument("evidence_id", help="evidence id, like 12 or E12")
    p.add_argument("--grade", required=True, help=GRADE_HELP)
    p.add_argument("--note", required=True, help="why this grade")
    p.add_argument("--claim", help="corrected claim (if the old one says too much)")
    p.add_argument("--snapshot", help="new snapshot sha (if the page was saved again)")

    p = sub.add_parser("set-score", parents=[common], help="ranking: save fit and value (judgment part)")
    p.add_argument("id", type=int)
    p.add_argument("--fit", type=int, required=True, choices=range(0, 4), help="0-3: can Ahmad solve this?")
    p.add_argument("--value", type=int, required=True, choices=range(1, 4), help="1-3: how much is it worth?")
    p.add_argument("--fit-why", required=True, help="one line: why this fit")
    p.add_argument("--value-why", required=True, help="one line: why this value")
    p.add_argument("--disqualifier", action="append", default=[],
                   help="a disqualifier that applies (from problems.yaml / policy.yaml). Repeat for more.")
    return parser


COMMANDS = {
    "init": cmd_init, "add-lead": cmd_add_lead, "list": cmd_list, "show": cmd_show, "move": cmd_move,
    "history": cmd_history, "block": cmd_block, "blocked": cmd_blocked,
    "start-research": cmd_start_research, "add-evidence": cmd_add_evidence, "set-contact": cmd_set_contact,
    "set-research": cmd_set_research, "verify-evidence": cmd_verify_evidence, "set-score": cmd_set_score,
}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        return COMMANDS[args.command](db.Desk(store), args)
    except REFUSED as exc:
        print(f"REFUSED: {exc}")
        return 1
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
