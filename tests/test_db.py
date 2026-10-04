"""Tests for scripts/db.py rules, using the SQLite store (no internet)."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

# The status flow written out by hand (the spec). db.TRANSITIONS must match it exactly.
ALLOWED = {
    "new": {"researched", "rejected", "opted_out"},
    "researched": {"verified", "rejected", "opted_out"},
    "verified": {"qualified", "rejected", "opted_out"},
    "qualified": {"draft_ready", "rejected", "opted_out"},
    "draft_ready": {"approved", "rejected", "opted_out"},
    "approved": {"contacted", "draft_ready", "rejected", "opted_out"},
    "contacted": {"replied", "no_response", "opted_out"},
    "replied": {"meeting", "proposal", "lost", "opted_out"},
    "meeting": {"proposal", "lost", "opted_out"},
    "proposal": {"won", "lost", "opted_out"},
    "won": set(), "lost": set(), "no_response": set(), "rejected": set(), "opted_out": set(),
}
ALL_PAIRS = [(a, b) for a in ALLOWED for b in ALLOWED]
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india",
         "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec"]


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def desk(store):
    return db.Desk(store)


def force_status(store, lead_id, status):
    with store.conn:
        store.conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (status, lead_id))


def give_proof(store, lead_id):
    """Checked problem proof + a contact, so the lead may move to 'verified'."""
    lead = store.get_lead(lead_id)
    store.add_evidence({"opportunity_id": lead_id, "claim": "c", "url": "https://alpha.com", "quote": "q",
                        "source_type": "job_post", "topic": "pain", "grade": "STRONG_SIGNAL",
                        "depends_on": [], "verified": True})
    store.add_person({"company_id": lead["company_id"], "title": "Owner", "role_type": "owner"})


# ---------- status flow ----------
def test_spec_matches_code():
    assert set(db.STATUSES) == set(ALLOWED)
    assert {s: db.TRANSITIONS[s] for s in db.STATUSES} == ALLOWED


@pytest.mark.parametrize("old,new", ALL_PAIRS)
def test_every_pair(old, new):
    if new in ALLOWED[old]:
        db.check_move(old, new)
    else:
        with pytest.raises(db.InvalidMove):
            db.check_move(old, new)


@pytest.mark.parametrize("old,new", [(a, b) for a, b in ALL_PAIRS if b in ALLOWED[a]])
def test_allowed_move_saves_status_and_one_event(desk, store, old, new):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    force_status(store, lead_id, old)
    if new == "verified":
        give_proof(store, lead_id)
    before = len(store.events_for(lead_id))
    assert desk.move(lead_id, new, "because") == (old, new)
    lead = store.get_lead(lead_id)
    assert lead["status"] == new
    events = store.events_for(lead_id)
    assert len(events) == before + 1
    assert events[-1]["type"] == "status_changed"
    assert events[-1]["payload"] == {"from": old, "to": new, "reason": "because"}
    assert lead["closed_reason"] == ("because" if new in db.END_STATES else None)


def test_new_to_won_refused_and_nothing_saved(desk, store):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    with pytest.raises(db.InvalidMove, match="new -> won is not allowed"):
        desk.move(lead_id, "won", "test")
    assert store.get_lead(lead_id)["status"] == "new"
    assert [e["type"] for e in store.events_for(lead_id)] == ["lead_added"]


def test_closed_lead_cannot_move(desk, store):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    desk.move(lead_id, "rejected", "not a fit")
    with pytest.raises(db.InvalidMove, match="closed"):
        desk.move(lead_id, "researched", "again")


def test_unknown_status_and_missing_reason(desk):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    with pytest.raises(db.InvalidMove, match="unknown status"):
        desk.move(lead_id, "dancing", "x")
    with pytest.raises(db.DeskError, match="reason"):
        desk.move(lead_id, "researched", "  ")


def test_missing_lead(desk):
    with pytest.raises(db.NotFound):
        desk.move(99, "researched", "x")


def test_status_changed_meanwhile(desk, store):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    force_status(store, lead_id, "researched")
    with pytest.raises(db.StatusConflict):
        store.change_status(lead_id, "new", "researched", "x", "human", None)


# ---------- events + init ----------
def test_events_are_append_only(desk, store):
    desk.add_lead("https://alpha.com", "n", "email")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with store.conn:
            store.conn.execute("UPDATE events SET type = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with store.conn:
            store.conn.execute("DELETE FROM events")


def test_init_twice_keeps_data(tmp_path):
    path = tmp_path / "again.db"
    s1 = SqliteStore(path)
    db.Desk(s1).add_lead("https://alpha.com", "n", "email")
    s1.close()
    s2 = SqliteStore(path)
    assert s2.check() == []
    assert len(s2.list_leads()) == 1
    s2.close()


# ---------- domains + names ----------
@pytest.mark.parametrize("url,expected", [
    ("https://www.Acme.com/about", "acme.com"),
    ("http://blog.acme.com:8080/x", "acme.com"),
    ("https://shop.acme.co.uk", "acme.co.uk"),
    ("acme.com", "acme.com"),
    ("https://cool-store.myshopify.com/products", "cool-store.myshopify.com"),
    ("https://www.upwork.com/jobs/~01", None),
    ("https://news.ycombinator.com/item?id=1", None),
    ("https://www.google.co.uk/maps/place/x", None),
    ("https://www.linkedin.com/in/someone", None),
])
def test_company_domain(url, expected):
    assert db.company_domain(url) == expected


def test_name_key_and_similarity():
    assert db.name_key("ACME, Inc.") == db.name_key("Acme Inc") == "acme"
    assert db.name_key("Smith & Sons LLC") == "smith and sons"
    assert db.similar_names("bright dental", "brigth dental")      # typo
    assert not db.similar_names("bright dental", "bright plumbing")
    assert not db.similar_names("", "")


def test_channel_aliases(desk, store):
    lead_id = desk.add_lead("https://alpha.com", "n", "linkedin")
    assert store.get_lead(lead_id)["channel"] == "linkedin_message"
    with pytest.raises(db.DeskError, match="unknown channel"):
        desk.add_lead("https://bravo.com", "n", "fax")


def test_url_must_be_http(desk):
    with pytest.raises(db.DeskError, match="http"):
        desk.add_lead("alpha.com", "n", "email")


# ---------- duplicates ----------
def test_duplicate_same_domain(desk, store):
    desk.add_lead("https://www.alpha.com", "n", "email")
    with pytest.raises(db.DuplicateLead, match="alpha.com already exists"):
        desk.add_lead("https://blog.alpha.com/post", "n", "email")
    assert len(store.list_leads()) == 1


def test_duplicate_same_url(desk):
    desk.add_lead("https://www.upwork.com/jobs/~01", "n", "upwork")
    with pytest.raises(db.DuplicateLead, match="same URL"):
        desk.add_lead("https://www.upwork.com/jobs/~01", "n", "upwork")


def test_duplicate_similar_name(desk):
    desk.add_lead("https://news.ycombinator.com/item?id=1", "n", "email", company="ACME, Inc.")
    with pytest.raises(db.DuplicateLead, match="very similar"):
        desk.add_lead("https://acme-corp.io", "n", "email", company="Acme Inc")


def test_different_companies_pass(desk, store):
    desk.add_lead("https://bright-dental.com", "n", "email")
    desk.add_lead("https://bright-plumbing.com", "n", "email")
    assert len(store.list_leads()) == 2


def test_platform_links_do_not_clash(desk, store):
    desk.add_lead("https://www.upwork.com/jobs/~01", "n", "upwork")
    desk.add_lead("https://www.upwork.com/jobs/~02", "n", "upwork")
    leads = store.list_leads()
    assert len(leads) == 2
    assert all(l["company_domain"] is None for l in leads)


# ---------- block list ----------
def test_block_domain_and_email(desk, store):
    value, kind, added, _ = desk.block("https://www.Spam.com/page", "manual")
    assert (value, kind, added) == ("spam.com", "domain", True)
    assert desk.block("spam.com", "again")[2] is False          # already there
    value, kind, added, _ = desk.block("Owner@Other.com", "opt_out")
    assert (value, kind) == ("owner@other.com", "email")
    assert desk.is_blocked("someone@spam.com")                   # domain blocks all its emails
    assert desk.is_blocked("owner@other.com")
    assert not desk.is_blocked("sales@other.com")
    assert len(store.list_blocks()) == 2


def test_blocked_domain_refused_on_add(desk, store):
    desk.block("alpha.com", "opt_out")
    with pytest.raises(db.Blocked):
        desk.add_lead("https://www.alpha.com", "n", "email")
    assert store.list_leads() == []


def test_block_warns_about_open_leads(desk):
    lead_id = desk.add_lead("https://alpha.com", "n", "email")
    _, _, _, affected = desk.block("boss@alpha.com", "opt_out")
    assert [l["id"] for l in affected] == [lead_id]


def test_block_needs_reason_and_valid_value(desk):
    with pytest.raises(db.DeskError, match="reason"):
        desk.block("alpha.com", "")
    with pytest.raises(db.DeskError, match="not an email or a domain"):
        desk.block("hello", "x")


# ---------- daily cap ----------
def test_daily_cap(desk, store):
    cap = desk.policy["caps"]["max_new_leads_per_day"]
    for word in WORDS[:cap]:
        desk.add_lead(f"https://{word}.com", "n", "email")
    with pytest.raises(db.CapReached, match="daily limit"):
        desk.add_lead(f"https://{WORDS[cap]}.com", "n", "email")
    assert len(store.list_leads()) == cap


def test_start_of_today_is_karachi_midnight():
    tz = db.local_tz()
    now = datetime(2026, 10, 4, 20, 30, tzinfo=timezone.utc)      # 01:30 on Oct 5 in Karachi
    assert db.start_of_today_utc(tz, now) == datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)


# ---------- .env + backend choice ----------
def test_open_store_supabase_needs_keys(tmp_path, monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "DESK_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("# empty\nSUPABASE_URL=\n", encoding="utf-8")
    with pytest.raises(db.ConfigError, match="SUPABASE_URL"):
        db.open_store("supabase", env_file=env)


def test_load_env_reads_file(tmp_path, monkeypatch):
    for key in ("SUPABASE_URL", "DESK_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text('SUPABASE_URL="https://x.supabase.co"\nDESK_BACKEND=sqlite\n', encoding="utf-8")
    values = db.load_env(env)
    assert values["SUPABASE_URL"] == "https://x.supabase.co"
    store = db.open_store(env_file=env, db_path=tmp_path / "x.db")
    assert type(store).__name__ == "SqliteStore"
    store.close()
