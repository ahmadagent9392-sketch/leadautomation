#!/usr/bin/env python3
"""Demo mode (Stage 8): fill data/demo.db with MADE-UP businesses for videos, screenshots and the public repo.

Usage:
    python scripts/demo.py                 # makes data/demo.db + data/demo/ (config copy, snapshots)
    python scripts/dashboard.py --demo     # then look at it
    python scripts/desk.py list --demo     # every desk command takes --demo

Every business, person, email and web page here is invented (".example" domains, which can never be real).
The demo goes through the real desk rules (evidence checks, ranking, critic, approval), so it looks like real use.
Nothing is sent and nothing goes to the internet.
If data/demo.db already has data, this refuses: files in data/ are never deleted by code.
Delete data/demo.db and the data/demo folder yourself to make a fresh demo.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

import db
import ideas as ideas_mod
import rank
from store_sqlite import SqliteStore

FAKE_ME = {"name": "Demo Developer", "business_name": "Demo Automation Studio",
           "postal_address": "1 Example Street, Demo City", "skills": ["Python", "n8n", "Google Sheets"],
           "portfolio_links": ["https://portfolio.example/demo"], "email_signature": "Demo Developer\n"}
GOOD = dict(specific=2, true=2, relevant=2, short=2, tone=2, next_step=2, compliance=1)   # 13/14

# stage = how far the made-up lead goes in the desk
BUSINESSES = [
    dict(key="brightsmile", name="Brightsmile Dental (demo)", stage="won", person="Sara Demo", title="Office Manager",
         pattern="missed-leads-slow-replies", quote="we can't keep up with patient calls and web form requests",
         why_now="opening a second clinic next month"),
    dict(key="harborfit", name="Harbor Fitness Studio (demo)", stage="replied", person="Omar Sample",
         title="Owner", pattern="missed-leads-slow-replies",
         quote="new member enquiries sit in our inbox for days", why_now="spring membership drive"),
    dict(key="pinecrest", name="Pinecrest Vet Clinic (demo)", stage="contacted", person="Lena Fictional",
         title="Practice Manager", pattern="missed-leads-slow-replies",
         quote="our front desk misses calls during surgery hours", sent_days_ago=6),
    dict(key="northwind", name="Northwind Logistics (demo)", stage="contacted", person="Ravi Placeholder",
         title="Operations Manager", pattern="manual-data-entry",
         quote="we copy every order from email into our spreadsheet by hand", sent_days_ago=2),
    dict(key="maplehome", name="Maple Home Repairs (demo)", stage="opted_out", person="Tom Madeup",
         title="Owner", pattern="missed-leads-slow-replies", quote="we lose jobs because we call back too late"),
    dict(key="sunnyside", name="Sunnyside Bakery Co (demo)", stage="in_gmail", person="Mia Invented",
         title="Owner", pattern="manual-data-entry", quote="wholesale orders are typed into invoices one by one"),
    dict(key="willow", name="Willow Interiors (demo)", stage="in_gmail", channel="linkedin_message",
         person="Nora Imaginary", title="Founder", pattern="missed-leads-slow-replies",
         quote="design enquiries from Instagram wait a week for an answer"),
    dict(key="cedarlaw", name="Cedar Family Law (demo)", stage="draft_ready", person="Ana Pretend",
         title="Office Administrator", pattern="missed-leads-slow-replies",
         quote="potential clients wait two days for a call back"),
    dict(key="bluepeak", name="Blue Peak Plumbing (demo)", stage="draft_ready", person="Sam Notreal",
         title="Owner", pattern="missed-leads-slow-replies", quote="after-hours calls go to a full voicemail box",
         idea="demo-trades-booking"),
    dict(key="greenleaf", name="Greenleaf Accounting (demo)", stage="qualified", person="Priya Example",
         title="Managing Partner", pattern="manual-data-entry",
         quote="staff re-type client receipts into our books every week", why_now="tax season starts in January"),
    dict(key="quickfix", name="QuickFix Auto Garage (demo)", stage="qualified", person="Jon Sample",
         title="Owner", pattern="missed-leads-slow-replies", quote="customers say they could not book online",
         idea="demo-trades-booking"),
    dict(key="lakeside", name="Lakeside Yoga (demo)", stage="researched", person="Eva Fiction", title="Owner",
         pattern="missed-leads-slow-replies", quote="class requests come by DM and get lost"),
    dict(key="metrocopy", name="Metro Copy Center (demo)", stage="rejected", person="Ben Madeup", title="Manager",
         pattern="manual-data-entry", quote="we enter print orders by hand"),
    dict(key="oakridge", name="Oakridge Dental Lab (demo)", stage="new", idea="demo-trades-booking"),
    dict(key="riverbend", name="Riverbend Cleaning (demo)", stage="new"),
]
STAGE_ORDER = ["new", "researched", "qualified", "draft_ready", "in_gmail", "contacted", "replied", "opted_out", "won"]


class DemoError(db.DeskError):
    pass


def _reached(b: dict, stage: str) -> bool:
    if b["stage"] == "rejected":
        return stage in ("new", "researched")
    return STAGE_ORDER.index(b["stage"]) >= STAGE_ORDER.index(stage)


def make_config(demo_dir: Path) -> Path:
    """data/demo/config = config/ with a made-up name and address, bigger caps, no real idea files."""
    cfg = demo_dir / "config"
    shutil.copytree(db.ROOT / "config", cfg, ignore=shutil.ignore_patterns("ideas"), dirs_exist_ok=True)
    me = yaml.safe_load((cfg / "me.yaml").read_text(encoding="utf-8"))
    me.update(FAKE_ME)
    (cfg / "me.yaml").write_text(yaml.safe_dump(me, sort_keys=False), encoding="utf-8")
    offer = yaml.safe_load((cfg / "offer.yaml").read_text(encoding="utf-8"))
    offer["status"] = "decided"
    offer["offers"][0].update(name="Missed-call catcher (demo)", problem="missed calls and slow replies",
                              customer="small local businesses", result="every enquiry answered the same day",
                              proof=[{"title": "demo video", "link": "https://portfolio.example/video"}])
    (cfg / "offer.yaml").write_text(yaml.safe_dump(offer, sort_keys=False), encoding="utf-8")
    policy = yaml.safe_load((cfg / "policy.yaml").read_text(encoding="utf-8"))
    for cap in ("max_new_leads_per_day", "max_research_per_day", "max_drafts_per_day", "max_first_emails_per_day"):
        policy["caps"][cap] = max(int(policy["caps"][cap]), 50)
    (cfg / "policy.yaml").write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    return cfg


def _page(b: dict, day: date) -> str:
    return (f"{b['name']} - Careers and contact\n"
            f"We are hiring an assistant. Right now {b['quote']}.\n"
            f"Contact our {b.get('title', 'manager').lower()} {b.get('person', '')} at "
            f"{b['key']}@{b['key']}.example\nPosted: {day:%B %d, %Y}\n")


def _body(b: dict) -> str:
    first = b["person"].split()[0]
    return (f"Hi {first}, I read on your careers page that {b['quote']}. "
            f"A small auto-reply plus one shared call-back list could catch those requests the same day. "
            f"Want me to send a 2-minute video of how it would work for {b['name'].replace(' (demo)', '')}?")


def seed_one(desk: db.Desk, b: dict, out_dir: Path) -> int:
    today = desk.today()
    site = f"https://{b['key']}.example"
    lead_id = desk.add_lead(site + "/careers", f"demo: {b.get('quote', 'found on a demo search')}",
                            b.get("channel", "email"), company=b["name"], actor="demo")
    if b.get("idea"):
        desk.store.update_lead(lead_id, {"idea": b["idea"]})
    if not _reached(b, "researched"):
        return lead_id

    seen = today - timedelta(days=5)
    desk.start_research(lead_id, actor="demo")
    desk.set_research(lead_id, pattern=b["pattern"], why_now=b.get("why_now") or "hiring for this job now",
                      unknowns=["budget owner not confirmed"] if b["stage"] == "researched" else None)
    url = site + "/careers"
    sha = desk.save_snapshot(url, _page(b, seen), 200, "careers")[0]
    pain, _, _ = desk.add_evidence(lead_id, claim=b["quote"], url=url, quote=b["quote"], grade="STRONG_SIGNAL",
                                   source_type="job_post", topic="pain", observed_at=seen.isoformat(), snapshot=sha,
                                   actor="demo")
    desk.verify_evidence(pain, "STRONG_SIGNAL", "quote found on the saved page", actor="demo")
    who = f"Contact our {b['title'].lower()} {b['person']}"
    contact, _, _ = desk.add_evidence(lead_id, claim=f"{b['person']} is {b['title']}", url=url, quote=who,
                                      grade="CONFIRMED_FACT", source_type="website", topic="contact", snapshot=sha,
                                      actor="demo")
    desk.verify_evidence(contact, "CONFIRMED_FACT", "quote found on the saved page", actor="demo")
    desk.set_contact(lead_id, title=b["title"], name=b["person"], role_type="owner",
                     email=f"{b['key']}@{b['key']}.example", email_status="published", evidence_id=contact,
                     actor="demo")
    desk.move(lead_id, "researched", "demo research done", actor="demo")
    if b["stage"] == "rejected":
        desk.move(lead_id, "rejected", "demo: checker FAIL - the job post was for a different branch", actor="demo")
        return lead_id
    if not _reached(b, "qualified"):
        return lead_id

    desk.move(lead_id, "verified", "demo checker PASS", actor="demo")
    desk.set_score(lead_id, fit=3, value=2 if b["pattern"] == "manual-data-entry" else 3,
                   fit_why="repeated task with common tools", value_why="they are hiring for it", actor="demo")
    rank.run(desk, lead_id)
    if not _reached(b, "draft_ready"):
        return lead_id

    mid, _ = desk.save_draft(lead_id, body=_body(b), subject="your enquiries" if b.get("channel", "email") == "email" else None,
                             evidence_ids=[pain],
                             angle="their job post", cta="video", actor="demo")
    desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=GOOD, reasons=[], actor="demo")
    if not _reached(b, "in_gmail"):
        return lead_id

    desk.approve(mid, "approve", "demo: short and true", actor="demo")
    desk.export_draft(mid, out_dir)                    # email: Gmail data; other channels: copy-paste file
    if b.get("channel", "email") == "email":
        desk.set_gmail_draft(mid, f"demo-draft-{lead_id}", f"demo-thread-{lead_id}", actor="demo")
    if not _reached(b, "contacted"):
        return lead_id

    sent = desk.now() - timedelta(days=b.get("sent_days_ago", 8))
    desk.mark_sent(mid, f"demo-msg-{lead_id}", sent.isoformat(timespec="seconds"), actor="demo")
    if b["stage"] == "opted_out":
        desk.log_reply(lead_id, "Please remove me from your list.", sender=f"{b['key']}@{b['key']}.example",
                       gmail_message_id=f"demo-reply-{lead_id}", actor="demo")
        return lead_id
    if not _reached(b, "replied"):
        return lead_id

    rid, _, _, _ = desk.log_reply(lead_id, "Sounds useful. Can you show me how it works next week?",
                                  sender=f"{b['key']}@{b['key']}.example", gmail_message_id=f"demo-reply-{lead_id}",
                                  actor="demo")
    desk.classify_reply(rid, category="positive", next_action="ask_ahmad",
                        note="demo: wants to see it next week", asked="a short demo next week", actor="demo")
    if b["stage"] == "won":
        desk.reply_done(rid, "demo: call booked", actor="demo")
        for status in ("meeting", "proposal", "won"):
            desk.move(lead_id, status, f"demo: {status}", actor="demo")
    return lead_id


def seed(store, demo_dir: Path, now=db.now_utc) -> list[int]:
    if store.list_leads():
        raise DemoError("data/demo.db already has demo data. To start again, delete data/demo.db and the "
                        "data/demo folder yourself, then run this again.")
    cfg = make_config(demo_dir)
    ideas_mod.new_idea("demo-trades-booking", idea="local trades that miss booking calls (demo)",
                       keywords=["front desk", "booking", "call back", "voicemail"],
                       review_keywords=["never called back", "could not book"], owner_roles=["owner"],
                       replace=True, config_dir=cfg)
    desk = db.Desk(store, config_dir=cfg, now=now, snapshot_dir=demo_dir / "snapshots")
    ids = [seed_one(desk, b, demo_dir / "cards") for b in BUSINESSES]
    for n, (src, title) in enumerate((("hn", "Ask HN: how do you handle missed calls? (demo)"),
                                      ("gmaps", "Demo Pet Grooming - 3.1 stars (demo)"),
                                      ("jobs", "Front desk assistant - Demo Physio (demo)")), 1):
        store.add_raw_item({"source": src, "url": f"https://found{n}.example/post", "title": title,
                            "text": "made-up found item for the demo", "found_at": db.now_utc().isoformat(),
                            "status": "new"})
    return ids


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Fill data/demo.db with made-up businesses.")
    ap.add_argument("--db", type=Path, default=db.DEMO_SQLITE)
    ap.add_argument("--dir", type=Path, default=db.DEMO_DIR, help="demo config + snapshots folder")
    args = ap.parse_args(argv)
    store = SqliteStore(args.db)
    try:
        ids = seed(store, args.dir)
    except db.DeskError as exc:
        print(f"REFUSED: {exc}" if isinstance(exc, DemoError) else f"ERROR: {exc}")
        return 1
    finally:
        store.close()
    print(f"Demo ready: {len(ids)} made-up businesses in {args.db}.")
    print("Look at it:  python scripts/dashboard.py --demo   (or: python scripts/desk.py list --demo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
