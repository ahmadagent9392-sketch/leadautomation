"""Tests for Stage 7b LinkedIn export import (scripts/sources/linkedin_export.py) and scout keep-warm.
Made-up contacts only, SQLite, nothing is opened."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "sources"))

import db  # noqa: E402
import linkedin_export as li  # noqa: E402
import scout  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
P = "https://www." + "linkedin.com/in/"
ME = P + "ahmad-demo"
CONNECTIONS = f"""Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Sara,Demo,{P}sara-demo,sara.secret@brightsmile.example,Brightsmile Dental,Owner,12 Mar 2025
Omar,Sample,{P}omar-sample,,Pixel Agency,Founder & CEO,01 Jan 2024
Lena,Fiction,{P}lena-fiction,lena@bigcorp.example,BigCorp,Software Engineer,05 May 2023
Tom,Madeup,{P}tom-madeup,,,Owner,05 May 2023
Ravi,Placeholder,https://example.com/ravi,,Northwind,Operations Manager,05 May 2023
Mia,Invented,{P}mia-invented,,Sunnyside Bakery,Operations Manager,10 Feb 2026
"""
MESSAGES = f"""CONVERSATION ID,CONVERSATION TITLE,FROM,SENDER PROFILE URL,TO,RECIPIENT PROFILE URLS,DATE,SUBJECT,CONTENT,FOLDER
c1,,Ahmad,{ME},Sara Demo,{P}sara-demo,2026-08-01 10:00:00 UTC,,SECRET PRICE TALK 500 dollars,INBOX
c1,,Sara Demo,{P}sara-demo,Ahmad,{ME},2026-08-02 10:00:00 UTC,,thanks lets talk later,INBOX
c2,,Ahmad,{ME},Mia Invented,{P}mia-invented,2026-09-01 10:00:00 UTC,,hello Mia,INBOX
"""


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "l.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return db.Desk(store, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def files(tmp_path):
    c, m = tmp_path / "Connections.csv", tmp_path / "messages.csv"
    c.write_text(CONNECTIONS, encoding="utf-8")
    m.write_text(MESSAGES, encoding="utf-8")
    return c, m


def test_parse_connections_skips_notes_and_never_reads_email(files):
    rows = li.parse_connections(files[0])
    assert [r["name"] for r in rows][:2] == ["Sara Demo", "Omar Sample"]
    assert rows[0]["connected_on"] == "2025-03-12"
    assert all("email" not in k.lower() for r in rows for k in r)


def test_parse_messages_counts_only(files):
    talks = li.parse_messages(files[1])
    sara = talks[(P + "sara-demo").lower()]
    assert sara == {"messages": 2, "two_way": True, "last": "2026-08-02"}
    assert talks[(P + "mia-invented").lower()]["two_way"] is False
    assert ME.lower() not in talks                             # Ahmad himself


def test_run_saves_warm_contacts_without_emails_or_message_text(desk, store, files):
    tally, saved = li.run(desk, *files, max_items=40, min_warmth=3)
    names = [c["name"] for c in saved]
    assert names[0] == "Sara Demo"                             # owner + two-way talk
    assert "Omar Sample" in names and "Mia Invented" in names
    assert "Lena Fiction" not in names                         # engineer at a big company: not warm
    assert tally.counts["no_company"] == 1 and tally.counts["no_profile_url"] == 1
    for raw in store.list_raw_items(source="linkedin"):
        blob = str(raw)
        assert "secret" not in blob.lower() and "@" not in blob and "500 dollars" not in blob
        assert raw["posted_at"] is None and raw["status"] == "new"
    sara = next(r for r in store.list_raw_items(source="linkedin") if "Sara" in r["title"])
    assert "Messages: 2, last 2026-08-02, two-way" in sara["text"] and sara["extra"]["warmth"] >= 7


def test_run_twice_does_not_save_twice(desk, store, files):
    li.run(desk, *files)
    tally, saved = li.run(desk, *files)
    assert saved == [] and tally.counts.get("seen", 0) >= 3


def test_max_and_dry_run(desk, store, files):
    tally, saved = li.run(desk, *files, max_items=1, dry_run=True)
    assert len(saved) == 1 and tally.counts["over_max"] >= 2
    assert store.list_raw_items() == []


def test_missing_file_message(desk, tmp_path):
    with pytest.raises(db.DeskError, match="Get a copy|data/linkedin"):
        li.run(desk, tmp_path / "nope.csv", None)


def test_bad_csv_message(desk, tmp_path):
    bad = tmp_path / "Connections.csv"
    bad.write_text("hello,world\n1,2\n", encoding="utf-8")
    with pytest.raises(db.DeskError, match="no LinkedIn header"):
        li.run(desk, bad, None)


def test_warmth_reasons():
    score, why = li.warmth({"position": "Founder & CEO", "company": "Pixel Agency"}, None, [], NOW.date())
    assert score >= 4 and "agency" in why
    assert li.warmth({"position": "Intern", "company": "X"}, None, [], NOW.date())[0] == 0


# ---------- keep-warm ----------
def kept(desk, store, files, name="Sara"):
    li.run(desk, *files)
    raw = next(r for r in store.list_raw_items(source="linkedin") if name in r["title"])
    return raw, scout.keep_warm(desk, raw["id"], channel="referral", reason="talked in August")


def test_keep_warm_makes_lead_with_contact(desk, store, files):
    raw, out = kept(desk, store, files)
    lead = desk.get(out["lead_id"])
    assert lead["status"] == "new" and lead["channel"] == "referral_ask"
    assert lead["company_name"] == "Brightsmile Dental" and lead["source_url"] == raw["url"]
    person = store.people_for(lead["company_id"])[0]
    assert person["full_name"] == "Sara Demo" and person["title"] == "Owner" and person["role_type"] == "owner"
    assert person["profile_url"] == raw["url"] and not person.get("email")
    assert store.get_raw_item(raw["id"])["status"] == "kept"


def test_keep_warm_rules(desk, store, files):
    li.run(desk, *files)
    raw = store.list_raw_items(source="linkedin")[0]
    with pytest.raises(db.DeskError, match="warm leads use"):
        scout.keep_warm(desk, raw["id"], channel="email", reason="x")
    with pytest.raises(db.DeskError, match="reason"):
        scout.keep_warm(desk, raw["id"], channel="referral_ask", reason=" ")
    other = desk.store.add_raw_item({"source": "hn", "url": "https://news.example/1", "title": "t", "text": "x",
                                     "found_at": NOW.isoformat()})
    with pytest.raises(db.DeskError, match="only for LinkedIn"):
        scout.keep_warm(desk, other, channel="referral_ask", reason="x")


def test_keep_warm_respects_daily_cap(desk, store, files):
    li.run(desk, *files)
    cap = desk.policy["caps"]["max_new_leads_per_day"]
    for _ in range(cap):
        store.add_event("lead_added", "test", None, {})
    raw = store.list_raw_items(source="linkedin")[0]
    with pytest.raises(db.CapReached):
        scout.keep_warm(desk, raw["id"], channel="linkedin_message", reason="founder")


def test_keep_warm_blocked_company_domain_not_needed_but_duplicate_refused(desk, store, files):
    desk.add_lead("https://brightsmile.example/careers", "x", "email", company="Brightsmile Dental")
    li.run(desk, *files)
    raw = next(r for r in store.list_raw_items(source="linkedin") if "Sara" in r["title"])
    with pytest.raises(db.DuplicateLead):
        scout.keep_warm(desk, raw["id"], channel="referral_ask", reason="x")
    assert store.get_raw_item(raw["id"])["status"] == "rejected"


def test_linkedin_items_never_expire(desk, store, files):
    li.run(desk, *files)
    later = db.Desk(store, now=lambda: datetime(2027, 6, 1, tzinfo=timezone.utc), snapshot_dir=desk.snapshot_dir)
    assert scout.expire(later) == 0


def test_other_sources_still_refuse_linkedin(desk):
    import common
    with pytest.raises(Exception):
        common.save_item(desk, source="web", url=P + "x", title="t", text="x")


def test_cli(desk, store, files, tmp_path, capsys):
    code = li.main(["--connections", str(files[0]), "--messages", str(files[1]), "--backend", "sqlite",
                    "--db", str(tmp_path / "cli.db")])
    out = capsys.readouterr().out
    assert code == 0 and "Sara Demo" in out and "Emails and message text are never saved" in out


# ---------- old SQLite files get the new source ----------
def test_old_sqlite_file_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    s = SqliteStore(path)
    s.add_raw_item({"source": "hn", "url": "https://news.example/old", "title": "kept", "text": "x",
                    "found_at": NOW.isoformat()})
    s.close()
    con = sqlite3.connect(path)                     # make it look like a Stage 7 file
    con.executescript("""
        PRAGMA foreign_keys = OFF;
        ALTER TABLE raw_items RENAME TO r2;
        CREATE TABLE raw_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL CHECK (source IN ('hn','jobs','agency','gmaps','web','manual')),
          url TEXT NOT NULL UNIQUE, title TEXT, text TEXT NOT NULL DEFAULT '', posted_at TEXT,
          found_at TEXT NOT NULL, query TEXT, idea TEXT, matched TEXT NOT NULL DEFAULT '{}',
          extra TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'new'
          CHECK (status IN ('new','kept','rejected','expired')), reason TEXT,
          lead_id INTEGER REFERENCES opportunities(id));
        INSERT INTO raw_items SELECT * FROM r2; DROP TABLE r2;""")
    con.close()
    s = SqliteStore(path)
    try:
        assert s.find_raw_by_url("https://news.example/old")["title"] == "kept"     # old rows kept
        assert s.add_raw_item({"source": "linkedin", "url": P + "new", "title": "n", "text": "x",
                               "found_at": NOW.isoformat()})
    finally:
        s.close()
