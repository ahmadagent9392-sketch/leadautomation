"""Tests for scripts/scout.py, scripts/sources/common.py and scripts/ideas.py (SQLite, no internet)."""
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import ideas  # noqa: E402
import scout  # noqa: E402
from sources import common  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 4)
JOB = ("Bright Dental (sample) is hiring a front desk receptionist to answer inquiries and call back patients. "
       "We miss too many calls.")


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(ROOT / "config", d, ignore=shutil.ignore_patterns("ideas"))
    return d


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "s.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path, cfg):
    return db.Desk(store, config_dir=cfg, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


def raw(desk, url="https://news.ycombinator.com/item?id=1", posted="2026-10-01", source="hn", idea=None,
        text=JOB, title="Bright Dental | Receptionist"):
    patterns = common.search_patterns(desk, idea)
    result, rid = common.save_item(desk, source=source, url=url, title=title, text=text, posted_at=posted,
                                   idea=idea, matched=common.match_patterns(text, patterns))
    assert result == "saved", result
    return rid


def keep(desk, rid, **kw):
    args = dict(pattern_id="missed-leads-slow-replies", signal="job_post", channel="email",
                reason="hiring a receptionist to answer inquiries", company=None)
    args.update(kw)
    return scout.keep(desk, rid, **args)


# ---------- common ----------
def test_keyword_hits_whole_words_and_case():
    text = "We need a Front-Desk person; data entry daily. Receptionists welcome."
    assert common.keyword_hits(text, ["front desk", "data entry", "receptionist", "entry"]) == \
        ["front desk", "data entry", "entry"]


def test_match_patterns(desk):
    m = common.match_patterns(JOB, common.search_patterns(desk))
    assert m["patterns"] == {"missed-leads-slow-replies": ["receptionist", "answer inquiries", "call back",
                                                           "front desk"]}
    assert common.match_patterns("nothing here", common.search_patterns(desk)) == {}


def test_save_item_skips_old_seen_lead_and_dry_run(desk):
    assert common.save_item(desk, source="hn", url="https://x.example/1", title="t", text="x",
                            posted_at="2026-08-01")[0] == "old"            # older than max_age_days 45
    assert common.save_item(desk, source="hn", url="https://x.example/2", title="t", text="x",
                            dry_run=True) == ("dry_run", None)
    assert desk.store.list_raw_items() == []
    assert common.save_item(desk, source="hn", url="https://x.example/2", title="t", text="x")[0] == "saved"
    assert common.save_item(desk, source="hn", url="https://x.example/2", title="t", text="x")[0] == "seen"
    desk.add_lead("https://x.example/3", "n", "email")
    assert common.save_item(desk, source="hn", url="https://x.example/3", title="t", text="x")[0] == "lead"


def test_save_item_refuses_linkedin(desk):
    with pytest.raises(db.DeskError, match="LinkedIn"):
        common.save_item(desk, source="web", url="https://www.linkedin.com/posts/abc", title="t", text="x")


def test_dates():
    assert common.find_posted_date("Job title\nPosted: September 20, 2026\nApply", TODAY) == date(2026, 9, 20)
    assert common.find_posted_date("Date posted 2026-09-01", TODAY) == date(2026, 9, 1)
    assert common.find_posted_date("posted 3 days ago", TODAY) == date(2026, 10, 1)
    assert common.find_posted_date("Posted 12 Nov 2026", TODAY) is None           # future -> unknown
    assert common.find_posted_date("no date at all 2026-01-01", TODAY) is None
    assert common.relative_date("a month ago", TODAY) == date(2026, 9, 4)


def test_page_links():
    html = '<a href="/jobs/1#top">Front Desk</a><a href="mailto:x@y.z">m</a><a href="/jobs/1">again</a>'
    assert common.page_links(html, "https://a.example/careers") == [("https://a.example/jobs/1", "Front Desk")]


# ---------- scout keep / reject ----------
def test_pending_lists_new_items(desk):
    rid = raw(desk)
    items = scout.pending(desk)
    assert items[0]["raw_id"] == f"R{rid}" and items[0]["age_days"] == 3
    assert "receptionist" in items[0]["matched"]["keywords"]


def test_keep_makes_lead_with_pattern_snapshot_event(desk, store):
    rid = raw(desk)
    out = keep(desk, rid, company="Bright Dental (sample)")
    lead = desk.get(out["lead_id"])
    assert lead["status"] == "new" and lead["pattern_id"] == "missed-leads-slow-replies"
    assert "signal=job_post" in lead["note"] and f"found=R{rid}" in lead["note"]
    assert lead["company_name"] == "Bright Dental (sample)"
    assert desk.snapshot_text(out["snapshot"]).startswith("Bright Dental | Receptionist")
    r = store.get_raw_item(rid)
    assert r["status"] == "kept" and r["lead_id"] == out["lead_id"]
    assert [e["type"] for e in store.events_for(out["lead_id"])] == ["lead_added", "scout_kept"]
    with pytest.raises(db.DeskError, match="already 'kept'"):
        keep(desk, rid)


def test_keep_refuses_old_post_by_signal_decay(desk, store):
    rid = raw(desk, posted="2026-09-10")              # 24 days; help_request stays fresh 14 days
    with pytest.raises(db.DeskError, match="14 days"):
        keep(desk, rid, signal="help_request")
    assert store.get_raw_item(rid)["status"] == "expired"
    assert store.list_leads() == []


def test_keep_unknown_pattern_or_signal(desk):
    rid = raw(desk)
    with pytest.raises(db.DeskError, match="unknown pattern"):
        keep(desk, rid, pattern_id="nope")
    with pytest.raises(db.DeskError, match="signal 'tweet'"):
        keep(desk, rid, signal="tweet")
    with pytest.raises(db.DeskError, match="reason"):
        keep(desk, rid, reason=" ")


def test_keep_duplicate_marks_rejected(desk, store):
    desk.add_lead("https://brightdental.example/jobs", "by hand", "email")
    rid = raw(desk, url="https://brightdental.example/careers/front-desk")
    with pytest.raises(db.DuplicateLead):
        keep(desk, rid)
    r = store.get_raw_item(rid)
    assert r["status"] == "rejected" and r["reason"].startswith("duplicate")


def test_keep_blocked_domain(desk, store):
    desk.block("blocked-sample.example", "opted out")
    rid = raw(desk, url="https://blocked-sample.example/jobs/1")
    with pytest.raises(db.Blocked):
        keep(desk, rid)
    assert store.get_raw_item(rid)["status"] == "rejected"


def test_keep_respects_daily_new_lead_cap(desk):
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
             "kilo", "lima", "mike", "november", "oscar"]
    for name in names:
        desk.add_lead(f"https://{name}-sample.example", "n", "email")
    rid = raw(desk)
    with pytest.raises(db.CapReached, match="15 new leads"):
        keep(desk, rid)


def test_idea_max_10_leads_per_day(desk, cfg, store):
    ideas.new_idea("dental-booking", idea="dental clinics that need booking automation",
                   keywords=["receptionist", "front desk", "booking"], config_dir=cfg, today=TODAY)
    desk = db.Desk(store, config_dir=cfg, now=lambda: NOW, snapshot_dir=desk.snapshot_dir)
    for i in range(11):
        rid = raw(desk, url=f"https://news.ycombinator.com/item?id={100 + i}", idea="dental-booking")
        if i < 10:
            name = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet"][i]
            out = keep(desk, rid, pattern_id="idea-dental-booking", company=f"{name} Smiles sample")
            assert desk.get(out["lead_id"])["idea"] == "dental-booking"
        else:
            with pytest.raises(db.CapReached, match="10 new leads today"):
                keep(desk, rid, pattern_id="idea-dental-booking", company="Zulu Teeth sample")
    assert scout.stats(desk)["by_idea"]["dental-booking"]["kept"] == 10


def test_reject_and_expire(desk, store):
    rid = raw(desk)
    scout.reject(desk, rid, "staffing agency posting for a client")
    assert store.get_raw_item(rid)["status"] == "rejected"
    with pytest.raises(db.DeskError):
        scout.reject(desk, rid, "again")
    old = store.add_raw_item({"source": "hn", "url": "https://old.example", "text": "x",
                              "found_at": "2026-08-01T00:00:00+00:00"})
    assert scout.expire(desk) == 1
    assert store.get_raw_item(old)["status"] == "expired"


def test_add_raw_web_result(desk):
    result, rid, matched = scout.add_raw(desk, source="web", url="https://clinic-sample.example/careers",
                                         title="Front desk", text=JOB, posted="2026-10-02", idea=None,
                                         query="dentist hiring receptionist")
    assert result == "saved" and "receptionist" in matched["keywords"]
    with pytest.raises(db.DeskError, match="future"):
        scout.add_raw(desk, source="web", url="https://x.example", title="t", text="x", posted="2026-12-01",
                      idea=None, query=None)
    with pytest.raises(db.DeskError, match="add-raw is for"):
        scout.add_raw(desk, source="hn", url="https://x.example", title="t", text="x", posted=None, idea=None,
                      query=None)


def test_cli_pending_keep(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_SNAPSHOTS", tmp_path / "snaps")
    dbfile = str(tmp_path / "cli.db")
    base = ["--backend", "sqlite", "--db", dbfile]
    f = tmp_path / "post.txt"
    f.write_text(JOB, encoding="utf-8")
    assert scout.main(["add-raw", "--source", "manual", "--url", "https://clinic-sample.example/job",
                       "--title", "Receptionist", "--text-file", str(f), *base]) == 0
    assert "SAVED R1" in capsys.readouterr().out
    assert scout.main(["pending", *base]) == 0
    assert '"raw_id": "R1"' in capsys.readouterr().out
    assert scout.main(["keep", "R1", "--pattern", "missed-leads-slow-replies", "--signal", "job_post",
                       "--channel", "email", "--reason", "hiring receptionist", *base]) == 0
    assert "KEPT R1 -> lead #1" in capsys.readouterr().out
    assert scout.main(["keep", "R1", "--pattern", "missed-leads-slow-replies", "--signal", "job_post",
                       "--channel", "email", "--reason", "again", *base]) == 1


# ---------- ideas ----------
def test_idea_file_is_a_pattern_for_the_desk(cfg, store, tmp_path):
    path = ideas.new_idea("dental-booking", idea="dental clinics that need booking automation",
                          keywords=["booking", "front desk", "appointment"], review_keywords=["could not book"],
                          config_dir=cfg, today=TODAY)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["id"] == "idea-dental-booking" and data["temporary"] is True and data["signals"]
    desk = db.Desk(store, config_dir=cfg, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")
    lead = desk.add_lead("https://clinic-sample.example", "n", "email")
    desk.set_research(lead, pattern="idea-dental-booking")         # research / rank accept the idea pattern
    assert desk.get(lead)["pattern_id"] == "idea-dental-booking"
    # /discover uses only the real patterns; --idea uses only the idea
    assert "idea-dental-booking" not in [p["id"] for p in common.search_patterns(desk)]
    assert [p["id"] for p in common.search_patterns(desk, "dental-booking")] == ["idea-dental-booking"]


def test_idea_rules(cfg):
    with pytest.raises(db.DeskError, match="not a good idea name"):
        ideas.new_idea("Bad Name!", idea="x", keywords=["a", "b", "c"], config_dir=cfg)
    with pytest.raises(db.DeskError, match="3-15 keywords"):
        ideas.new_idea("ok-name", idea="x", keywords=["a"], config_dir=cfg)
    ideas.new_idea("ok-name", idea="x", keywords=["a", "b", "c"], config_dir=cfg)
    with pytest.raises(db.DeskError, match="already exists"):
        ideas.new_idea("ok-name", idea="x", keywords=["a", "b", "c"], config_dir=cfg)
    assert [i["name"] for i in ideas.list_ideas(cfg)] == ["ok-name"]
    with pytest.raises(db.DeskError, match="idea 'missing' not found"):
        common.search_patterns(db.Desk(SqliteStore(":memory:"), config_dir=cfg), "missing")


def test_promote_keeps_comments_and_loads_once(cfg):
    ideas.new_idea("dental-booking", idea="dental booking", keywords=["booking", "front desk", "appointment"],
                   config_dir=cfg, today=TODAY)
    before = (cfg / "problems.yaml").read_text(encoding="utf-8")
    assert ideas.promote("dental-booking", config_dir=cfg, today=TODAY) == "idea-dental-booking"
    after = (cfg / "problems.yaml").read_text(encoding="utf-8")
    assert after.startswith(before.rstrip("\n")) and "# These two are STARTER EXAMPLES" in after
    pats = db.load_problems(cfg)["patterns"]
    assert [p["id"] for p in pats].count("idea-dental-booking") == 1
    promoted = next(p for p in pats if p["id"] == "idea-dental-booking")
    assert "temporary" not in promoted                              # now a real pattern
    with pytest.raises(db.DeskError, match="already in config/problems.yaml"):
        ideas.promote("dental-booking", config_dir=cfg)
