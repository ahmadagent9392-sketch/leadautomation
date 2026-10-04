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

Drafts (Stage 5, used by /draft and /approve; nothing is ever sent):
    python scripts/desk.py save-draft ID --body-file F --evidence E1,E2 [--subject S] [--channel C]
                                         [--angle A] [--cta C]
    python scripts/desk.py save-review MID --verdict APPROVE_FOR_HUMAN|REWRITE|REJECT
                                          --scores specific=2,true=2,... [--reason T ...]
    python scripts/desk.py drafts ID [--full]
    python scripts/desk.py approve MID --decision approve|edit|reject --reason T [--body-file F] [--subject S]
                                       [--close-lead]
    python scripts/desk.py export-draft MID
    python scripts/desk.py set-gmail-draft MID --draft-id ID [--thread-id ID]

Sent, replies, follow-ups (Stage 6, used by /sync and /today; nothing is ever sent):
    python scripts/desk.py sync-list                     # JSON: what /sync must look at in Gmail
    python scripts/desk.py mark-sent MID [--gmail-message-id ID] [--sent-at 2026-10-05T10:30]
    python scripts/desk.py draft-missing MID             # Gmail draft deleted, not sent
    python scripts/desk.py log-reply ID (--text-file F | --text T) [--gmail-message-id ID] [--sender S]
                                        [--subject S] [--received-at T]
    python scripts/desk.py classify-reply RID --category C --next-action A --note T [--objections "a; b"]
                                             [--asked T] [--date YYYY-MM-DD]
    python scripts/desk.py replies ID
    python scripts/desk.py reply-done RID --note T
    python scripts/desk.py followups [--due]

Every command also takes --backend supabase|sqlite (default: DESK_BACKEND in .env, else supabase)
and --db PATH (sqlite only, default data/desk.db). --demo = made-up businesses (data/demo.db, scripts/demo.py).
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
    draft = desk.latest_draft(lead["id"])
    if draft:
        print(f"\nNewest draft: M{draft['id']} ({draft.get('channel')}) {desk.draft_state(draft)}"
              f"   -> drafts {lead['id']} --full")
    sent = desk.sent_messages(lead["id"])
    if sent:
        print(f"\nSent ({len(sent)} of max {db.fu.max_touches(desk.cadence, desk.policy)} touches)")
        for m in sent:
            print(f"  touch {m.get('touch_number')}: M{m['id']} {m.get('channel')}  "
                  f"{db.to_local(m['sent_at'], desk.tz)}")
    replies = desk.store.replies_for(lead["id"])
    if replies:
        print(f"\nReplies ({len(replies)})   -> replies {lead['id']}")
        for r in replies:
            print(f"  R{r['id']} [{r.get('category') or 'not sorted'}] {_short(db.rp.strip_quoted(r['body']), 70)}"
                  + ("  (done)" if r.get("handled_at") else ""))
    for f in desk.pending_follow_ups(lead["id"]):
        what = f"follow-up {f.get('touch_number')}" if f["kind"] == "followup" else f["kind"]
        print(f"  Next    : {what} due {str(f['due_on'])[:10]}")
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


# ---------- drafts and approvals (Stage 5) ----------
def _read_text(args) -> str | None:
    if getattr(args, "body_file", None):
        path = Path(args.body_file)
        if not path.exists():
            raise db.DeskError(f"file not found: {path}")
        return path.read_text(encoding="utf-8-sig")
    return getattr(args, "body", None)


def _scores(text: str) -> dict[str, int]:
    """'specific=2,true=2,...' -> {'specific': 2, ...}"""
    out: dict[str, int] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise db.DeskError(f"'{part.strip()}' must look like name=2")
        try:
            out[key.strip().lower()] = int(value.strip())
        except ValueError:
            raise db.DeskError(f"score '{key.strip()}' must be 0, 1 or 2") from None
    return out


def _print_problems(problems) -> None:
    for p in problems:
        print(f"  {p}")


def cmd_save_draft(desk: db.Desk, args) -> int:
    body = _read_text(args)
    if body is None:
        raise db.DeskError("give the text with --body-file FILE (or --body TEXT)")
    mid, problems = desk.save_draft(args.id, body=body, subject=args.subject, evidence_ids=_ids(args.evidence),
                                    channel=args.channel, angle=args.angle, cta=args.cta)
    bad = [p for p in problems if p.level == "ERROR"]
    print(f"Saved draft M{mid} for lead #{args.id} ({len(bad)} error(s), {len(problems) - len(bad)} warning(s)).")
    _print_problems(problems)
    if bad:
        print("Fix the errors and save a new draft (this counts as a rewrite).")
    return 0


def cmd_save_review(desk: db.Desk, args) -> int:
    mid = db.message_id(args.message_id)
    final, notes = desk.save_review(mid, verdict=args.verdict, scores=_scores(args.scores), reasons=args.reason)
    print(f"Review saved for M{mid}: {final}.")
    for n in notes:
        print(f"  note: {n}")
    return 0


def cmd_drafts(desk: db.Desk, args) -> int:
    lead = desk.get(args.id)
    drafts = [m for m in desk.store.messages_for(lead["id"]) if m.get("direction") == "out"]
    print(f"Drafts of lead #{lead['id']} ({lead['company_name']}), lead is [{lead['status']}]")
    if not drafts:
        print("  No drafts yet. Run /draft in Claude Code.")
        return 0
    for m in drafts:
        critic = m.get("critic") or {}
        score = f", critic {critic['total']}/14" if critic.get("total") is not None else ""
        print(f"  M{m['id']} {m.get('channel')}{score}: {desk.draft_state(m)}  "
              f"({db.to_local(m['created_at'], desk.tz)[:16]})")
    if not args.full:
        print(f"\nFull text of the newest draft: drafts {lead['id']} --full")
        return 0

    m = drafts[-1]
    msg, _, problems = desk.check_message(m["id"])
    person = desk.recipient(lead) or {}
    print(f"\n===== M{msg['id']} ({msg.get('channel')}) =====")
    if msg.get("channel") == "email":
        print(f"To      : {person.get('email') or '(no email)'}  ({person.get('full_name') or '?'}, "
              f"{person.get('title') or '?'})")
    else:
        print(f"For     : {person.get('full_name') or '?'}, {person.get('title') or '?'}"
              + (f"  {person['profile_url']}" if person.get("profile_url") else ""))
    if msg.get("subject"):
        print(f"Subject : {msg['subject']}")
    print("-" * 60)
    print(msg["body"])
    print("-" * 60)
    if msg.get("channel") == "email":
        print("Footer added by code when the Gmail draft is made:")
        print(desk_footer(desk))
        print("-" * 60)
    print(f"Words   : {db.ck.word_count(msg['body'])} (limit {(desk.policy.get('word_limits') or {}).get(msg.get('channel'), '?')})")
    print(f"State   : {desk.draft_state(msg)}")
    by_id = {e["id"]: e for e in desk.store.evidence_for(lead["id"])}
    print("Proof used:")
    for eid in msg.get("evidence_ids") or []:
        e = by_id.get(eid)
        if not e:
            print(f"  E{eid}: not found")
            continue
        print(f"  E{eid} [{e['grade']}] {_short(e['claim'], 80)}")
        if e.get("quote"):
            print(f"      \"{_short(e['quote'], 120)}\"")
        print(f"      {e['url']}  {e.get('observed_at') or 'no date'}")
    critic = msg.get("critic") or {}
    if critic:
        print(f"Critic  : {critic.get('verdict')}  {critic.get('total', '')}"
              + (f"/14  {critic.get('scores')}" if critic.get("scores") else ""))
        for r in critic.get("reasons") or []:
            print(f"  - {r}")
    print("Code checks:" + ("  OK" if not problems else ""))
    _print_problems(problems)
    return 0


def desk_footer(desk: db.Desk) -> str:
    return db.ck.render_footer(desk.policy, desk.me())


def cmd_approve(desk: db.Desk, args) -> int:
    mid = db.message_id(args.message_id)
    target, text = desk.approve(mid, args.decision, args.reason, body=_read_text(args), subject=args.subject,
                                close_lead=args.close_lead)
    print(text)
    if args.decision in ("approve", "edit"):
        print(f"Next: python scripts/desk.py export-draft M{target}")
    return 0


def cmd_export_draft(desk: db.Desk, args) -> int:
    import json
    result = desk.export_draft(db.message_id(args.message_id))
    if result["channel"] == "email":
        print(f"M{result['message_id']} passed all checks. Make the Gmail draft with this (create_draft):")
        payload = {"to": result["to"], "subject": result["subject"], "body": result["body"]}
        if result.get("reply_to_message_id"):
            payload["replyToMessageId"] = result["reply_to_message_id"]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"Then: python scripts/desk.py set-gmail-draft M{result['message_id']} --draft-id ID --thread-id ID")
    else:
        print(f"M{result['message_id']} passed all checks. Copy-paste file: {result['path']}")
        print(f"Ahmad opens it, copies the text, and sends it by hand on {result['channel']}.")
    return 0


def cmd_set_gmail_draft(desk: db.Desk, args) -> int:
    mid = db.message_id(args.message_id)
    desk.set_gmail_draft(mid, args.draft_id, args.thread_id)
    print(f"Saved: M{mid} is in Gmail drafts ({args.draft_id}). Ahmad reads it and sends it by hand.")
    return 0


# ---------- sent, replies, follow-ups (Stage 6) ----------
def _rid(text) -> int:
    try:
        return int(str(text).strip().upper().removeprefix("R"))
    except ValueError:
        raise db.DeskError(f"'{text}' is not a reply id (like 4 or R4)") from None


def sync_list(desk: db.Desk) -> dict:
    """What /sync must check in Gmail: drafts not sent yet, and threads that can get replies."""
    leads = {l["id"]: l for l in desk.store.list_leads()}
    waiting, threads = [], {}
    for m in desk.store.list_messages():
        lead = leads.get(m["opportunity_id"])
        if m.get("direction") != "out" or m.get("channel") != "email" or not lead:
            continue
        if m.get("gmail_draft_id") and not m.get("sent_at"):
            waiting.append({"message_id": f"M{m['id']}", "lead_id": lead["id"], "touch": m.get("touch_number"),
                            "gmail_draft_id": m["gmail_draft_id"], "thread_id": m.get("thread_id")})
        if m.get("thread_id"):
            t = threads.setdefault(m["thread_id"], {"thread_id": m["thread_id"], "lead_id": lead["id"],
                                                    "lead_status": lead["status"], "known_message_ids": []})
            if m.get("gmail_message_id"):
                t["known_message_ids"].append(m["gmail_message_id"])
    for r in desk.store.list_replies():
        for t in threads.values():
            if t["lead_id"] == r["opportunity_id"] and r.get("gmail_message_id"):
                t["known_message_ids"].append(r["gmail_message_id"])
    # closed leads too: an opt-out can still arrive after a lead is closed
    sent_threads = [t for t in threads.values() if t["known_message_ids"]]
    return {"waiting_to_send": waiting, "threads": sent_threads,
            "bounce_search": "from:(mailer-daemon OR postmaster) newer_than:30d"}


def cmd_sync_list(desk: db.Desk, args) -> int:
    import json
    print(json.dumps(sync_list(desk), ensure_ascii=False, indent=2))
    return 0


def cmd_mark_sent(desk: db.Desk, args) -> int:
    print(desk.mark_sent(db.message_id(args.message_id), args.gmail_message_id, args.sent_at))
    return 0


def cmd_draft_missing(desk: db.Desk, args) -> int:
    mid = db.message_id(args.message_id)
    desk.draft_missing(mid)
    lead_id = desk.get_message(mid)["opportunity_id"]
    print(f"M{mid}: Gmail draft is gone (not sent). The approval stays. To make it again: /approve {lead_id}")
    return 0


def cmd_log_reply(desk: db.Desk, args) -> int:
    text = _read_text(argparse.Namespace(body_file=args.text_file, body=args.text))
    if text is None:
        raise db.DeskError("give the reply with --text-file FILE (or --text TEXT)")
    rid, rule, done, new = desk.log_reply(args.id, text, gmail_message_id=args.gmail_message_id,
                                          sender=args.sender, subject=args.subject, received_at=args.received_at)
    if not new:
        print(f"Already saved as R{rid}. Nothing changed.")
        return 0
    print(f"Saved reply R{rid} for lead #{args.id}. Fixed rules: {rule or 'nothing found'}.")
    for d in done:
        print(f"  done: {d}")
    if rule in ("opt_out", "bounce"):
        print("The code already acted. The reply reader may still add notes (classify-reply).")
    else:
        print(f"Next: the reply reader sorts it -> classify-reply R{rid} --category ... --next-action ... --note ...")
    return 0


def cmd_classify_reply(desk: db.Desk, args) -> int:
    rid = _rid(args.reply_id)
    objections = None if args.objections is None else args.objections.split(";")
    final, notes = desk.classify_reply(rid, category=args.category, next_action=args.next_action,
                                       note=args.note, objections=objections, asked=args.asked,
                                       follow_up_date=args.date)
    print(f"R{rid} sorted as {final}.")
    for n in notes:
        print(f"  {n}")
    return 0


def cmd_replies(desk: db.Desk, args) -> int:
    lead = desk.get(args.id)
    rows = desk.store.replies_for(lead["id"])
    print(f"Replies of lead #{lead['id']} ({lead['company_name']}), lead is [{lead['status']}]")
    if not rows:
        print("  No replies.")
    for r in rows:
        print(f"\n  R{r['id']} [{r.get('category') or 'not sorted'}] from {r.get('sender') or '?'}  "
              f"{db.to_local(r['created_at'], desk.tz)}" + ("  DONE" if r.get("handled_at") else ""))
        if r.get("subject"):
            print(f"    Subject  : {r['subject']}")
        print("    " + _short(db.rp.strip_quoted(r["body"]), 400))
        for o in r.get("objections") or []:
            print(f"    Objection: {o}")
        for key, label in (("requested_action", "They ask "), ("follow_up_date", "Date     "),
                           ("next_action", "Next     "), ("note", "Note     ")):
            if r.get(key):
                print(f"    {label}: {_short(str(r[key]), 200)}")
    return 0


def cmd_reply_done(desk: db.Desk, args) -> int:
    rid = _rid(args.reply_id)
    desk.reply_done(rid, args.note)
    print(f"R{rid} is done. /today will not show it again.")
    return 0


def cmd_followups(desk: db.Desk, args) -> int:
    today = desk.today().isoformat()
    rows = [f for f in desk.store.list_follow_ups("pending") if not args.due or str(f["due_on"])[:10] <= today]
    if not rows:
        print("No follow-ups due." if args.due else "No pending follow-ups.")
        return 0
    leads = {l["id"]: l for l in desk.store.list_leads()}
    _table([[f["opportunity_id"], _short(leads.get(f["opportunity_id"], {}).get("company_name"), 28),
             f["kind"], f.get("touch_number") or "", str(f["due_on"])[:10],
             "DUE" if str(f["due_on"])[:10] <= today else ""] for f in rows],
           ["LEAD", "COMPANY", "KIND", "TOUCH", "DUE ON", ""])
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--backend", choices=["supabase", "sqlite"], default=None,
                        help="database (default: DESK_BACKEND in .env, else supabase)")
    common.add_argument("--db", type=Path, default=None, help="SQLite file (sqlite backend only)")
    common.add_argument("--demo", action="store_true", help="made-up businesses only (data/demo.db)")

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

    p = sub.add_parser("save-draft", parents=[common], help="writer: save one outreach draft")
    p.add_argument("id", type=int)
    p.add_argument("--body-file", help="text file with the message (UTF-8). No email footer: code adds it.")
    p.add_argument("--body", help="the message text (use --body-file for more than one line)")
    p.add_argument("--subject", help="email subject (1-4 words)")
    p.add_argument("--evidence", required=True, help="evidence ids the message uses, like E1,E3")
    p.add_argument("--channel", help="default: the lead's channel")
    p.add_argument("--angle", help="short name of the angle, like 'missed calls'")
    p.add_argument("--cta", help="kind of next step, like video, mini_audit, question")

    p = sub.add_parser("save-review", parents=[common], help="critic: save the review of a draft")
    p.add_argument("message_id", help="draft id, like M12")
    p.add_argument("--verdict", required=True, help=", ".join(db.CRITIC_VERDICTS))
    p.add_argument("--scores", required=True, help="0-2 each: " + ",".join(f"{k}=N" for k in db.CRITIC_KEYS))
    p.add_argument("--reason", action="append", default=[], help="one exact fix or reason. Repeat for more.")

    p = sub.add_parser("drafts", parents=[common], help="list the drafts of a lead")
    p.add_argument("id", type=int)
    p.add_argument("--full", action="store_true", help="show the newest draft with proof and checks")

    p = sub.add_parser("approve", parents=[common], help="Ahmad: approve, edit or reject a draft")
    p.add_argument("message_id", help="draft id, like M12")
    p.add_argument("--decision", required=True, choices=list(db.DECISIONS))
    p.add_argument("--reason", required=True)
    p.add_argument("--body-file", help="edit: file with Ahmad's new text")
    p.add_argument("--subject", help="edit: new subject")
    p.add_argument("--close-lead", action="store_true", help="reject: also close the lead (rejected)")

    p = sub.add_parser("export-draft", parents=[common],
                       help="final checks; email -> Gmail draft data, others -> cards/<id>-message.txt")
    p.add_argument("message_id", help="draft id, like M12")

    p = sub.add_parser("set-gmail-draft", parents=[common], help="save the Gmail draft id of an exported email")
    p.add_argument("message_id", help="draft id, like M12")
    p.add_argument("--draft-id", required=True)
    p.add_argument("--thread-id")

    sub.add_parser("sync-list", parents=[common], help="JSON: Gmail drafts and threads /sync must check")

    p = sub.add_parser("mark-sent", parents=[common], help="Ahmad sent this approved message")
    p.add_argument("message_id", help="draft id, like M12")
    p.add_argument("--gmail-message-id", help="id of the sent message in Gmail")
    p.add_argument("--sent-at", help="when (default now), like 2026-10-05 or 2026-10-05T10:30")

    p = sub.add_parser("draft-missing", parents=[common], help="the Gmail draft was deleted, not sent")
    p.add_argument("message_id", help="draft id, like M12")

    p = sub.add_parser("log-reply", parents=[common], help="save a reply (Gmail or pasted by Ahmad)")
    p.add_argument("id", type=int)
    p.add_argument("--text-file", help="file with the reply text (UTF-8)")
    p.add_argument("--text", help="the reply text")
    p.add_argument("--gmail-message-id")
    p.add_argument("--sender")
    p.add_argument("--subject")
    p.add_argument("--received-at")

    p = sub.add_parser("classify-reply", parents=[common], help="reply reader: save the category of a reply")
    p.add_argument("reply_id", help="reply id, like R4")
    p.add_argument("--category", required=True, help=", ".join(db.rp.CATEGORIES))
    p.add_argument("--next-action", required=True, help=", ".join(db.rp.NEXT_ACTIONS))
    p.add_argument("--note", required=True, help="one line: why this category")
    p.add_argument("--objections", help='objections, separated by ";"')
    p.add_argument("--asked", help="what they asked for")
    p.add_argument("--date", help="a date they gave (return date, 'try me in March'), YYYY-MM-DD")

    p = sub.add_parser("replies", parents=[common], help="show the replies of a lead")
    p.add_argument("id", type=int)

    p = sub.add_parser("reply-done", parents=[common], help="Ahmad dealt with this reply")
    p.add_argument("reply_id", help="reply id, like R4")
    p.add_argument("--note", required=True)

    p = sub.add_parser("followups", parents=[common], help="list pending follow-ups")
    p.add_argument("--due", action="store_true", help="only the ones due today or late")
    return parser


COMMANDS = {
    "init": cmd_init, "add-lead": cmd_add_lead, "list": cmd_list, "show": cmd_show, "move": cmd_move,
    "history": cmd_history, "block": cmd_block, "blocked": cmd_blocked,
    "start-research": cmd_start_research, "add-evidence": cmd_add_evidence, "set-contact": cmd_set_contact,
    "set-research": cmd_set_research, "verify-evidence": cmd_verify_evidence, "set-score": cmd_set_score,
    "save-draft": cmd_save_draft, "save-review": cmd_save_review, "drafts": cmd_drafts, "approve": cmd_approve,
    "export-draft": cmd_export_draft, "set-gmail-draft": cmd_set_gmail_draft,
    "sync-list": cmd_sync_list, "mark-sent": cmd_mark_sent, "draft-missing": cmd_draft_missing,
    "log-reply": cmd_log_reply, "classify-reply": cmd_classify_reply, "replies": cmd_replies,
    "reply-done": cmd_reply_done, "followups": cmd_followups,
}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db, demo=args.demo)
        return COMMANDS[args.command](db.Desk(store, **db.desk_options(args.demo)), args)
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
