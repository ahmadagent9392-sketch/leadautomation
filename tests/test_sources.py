"""Tests for scripts/sources/hn.py, jobs.py, agencies.py, pagespeed.py with fake servers (no internet)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
from sources import agencies, common, hn, jobs, pagespeed  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
TS = int(NOW.timestamp())
DAY = 86400


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "src.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return db.Desk(store, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


def quiet(*_):
    pass


# ---------- Hacker News ----------
HN_STORIES = {"hits": [
    {"objectID": "500", "title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": TS - 3 * DAY},
    {"objectID": "501", "title": "Ask HN: Who wants to be hired? (October 2026)", "created_at_i": TS - 3 * DAY},
    {"objectID": "502", "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)", "created_at_i": TS - 3 * DAY},
]}
HN_HIRING = {"id": 500, "children": [
    {"id": 601, "created_at_i": TS - 2 * DAY,
     "text": "Sample Clinic Co | Houston | Onsite<p>We need a receptionist to answer inquiries and call back leads."},
    {"id": 602, "created_at_i": TS - 2 * DAY, "text": "Rocket Sample | Remote | Senior Rust engineer"},
    {"id": 603, "created_at_i": TS - 2 * DAY, "text": None},
]}
HN_FREELANCE = {"id": 502, "children": [
    {"id": 701, "created_at_i": TS - DAY, "text": "SEEKING WORK | Python dev, I do data entry automation"},
    {"id": 702, "created_at_i": TS - DAY,
     "text": "SEEKING FREELANCER | Sample Shop | We copy and paste orders into a spreadsheet by hand every day"},
]}
HN_ASK = {"hits": [{"objectID": "800", "title": "Ask HN: How do you handle missed calls at a small business?",
                    "story_text": "We lose leads from missed calls &amp; voicemail.", "created_at_i": TS - 5 * DAY}]}


def hn_server(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/v1/search_by_date" and "author_whoishiring" in params.get("tags", ""):
            return httpx.Response(200, json=HN_STORIES)
        if path == "/api/v1/search_by_date" and params.get("tags") == "ask_hn":
            return httpx.Response(200, json=HN_ASK if params.get("query") == "missed calls" else {"hits": []})
        if path == "/api/v1/items/500":
            return httpx.Response(200, json=HN_HIRING)
        if path == "/api/v1/items/502":
            return httpx.Response(200, json=HN_FREELANCE)
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


def test_hn_saves_matching_posts(desk, store):
    calls = []
    tally = hn.run(desk, transport=hn_server(calls), out=quiet)
    rows = {r["url"]: r for r in store.list_raw_items(source="hn")}
    assert set(rows) == {hn.ITEM_URL.format(601), hn.ITEM_URL.format(702), hn.ITEM_URL.format(800)}
    job = rows[hn.ITEM_URL.format(601)]
    assert job["extra"]["signal_hint"] == "job_post" and job["posted_at"] == "2026-10-02"
    assert "receptionist" in job["matched"]["keywords"]
    assert job["title"].startswith("Sample Clinic Co")
    assert rows[hn.ITEM_URL.format(702)]["extra"]["signal_hint"] == "help_request"   # SEEKING WORK skipped
    assert rows[hn.ITEM_URL.format(800)]["text"] == "We lose leads from missed calls & voicemail."
    assert tally.counts["saved"] == 3 and tally.counts["no_match"] == 1
    assert not any("/items/501" in str(c.url) for c in calls)         # "Who wants to be hired" is ignored
    again = hn.run(desk, transport=hn_server([]), out=quiet)
    assert again.counts.get("saved") is None and again.counts["seen"] == 3


def test_hn_dry_run_saves_nothing(desk, store):
    tally = hn.run(desk, dry_run=True, transport=hn_server([]), out=quiet)
    assert tally.counts["dry_run"] == 3 and store.list_raw_items() == []


def test_hn_idea_keywords_only(desk, store, tmp_path):
    tally = hn.run(desk, keywords=["rust engineer"], transport=hn_server([]), out=quiet, idea=None)
    urls = [r["url"] for r in store.list_raw_items()]
    assert hn.ITEM_URL.format(602) in urls                             # extra keyword found the Rust post


def test_hn_down_gives_clear_error(desk):
    def boom(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(db.DeskError, match="cannot reach Hacker News"):
        hn.run(desk, transport=httpx.MockTransport(boom), out=quiet)


# ---------- job pages ----------
CAREERS = """<html><head><title>Careers - Sample Dental Group</title></head><body>
<a href="/careers/front-desk-receptionist">Front Desk Receptionist</a>
<a href="/careers/hygienist">Dental Hygienist</a>
<a href="/about">About us</a>
<a href="https://www.linkedin.com/jobs/view/1">Front desk on LinkedIn</a></body></html>"""
JOB_PAGE = """<html><head><title>Front Desk Receptionist</title></head><body><h1>Front Desk Receptionist</h1>
<p>Posted: September 28, 2026</p><p>Answer inquiries, call back patients, book appointments. Our phones never stop
and we miss calls every day, so we need help at the front desk. Full time, Houston office.</p></body></html>"""
SINGLE = """<html><head><title>Admin assistant</title></head><body><p>We need an admin assistant for data entry
into QuickBooks every day. Posted 5 days ago.</p><p>Apply by email.</p></body></html>"""


def jobs_server(calls):
    pages = {"https://dental-sample.example/careers": CAREERS,
             "https://dental-sample.example/careers/front-desk-receptionist": JOB_PAGE,
             "https://shop-sample.example/job": SINGLE}

    def handler(request):
        calls.append(str(request.url))
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})
    return httpx.MockTransport(handler)


def test_jobs_follows_matching_links_and_reads_date(desk, store):
    calls = []
    jobs.run(desk, urls=["https://dental-sample.example/careers"], transport=jobs_server(calls), out=quiet)
    rows = store.list_raw_items(source="jobs")
    assert [r["url"] for r in rows] == ["https://dental-sample.example/careers/front-desk-receptionist"]
    assert rows[0]["posted_at"] == "2026-09-28" and rows[0]["extra"]["posted_known"] is True
    assert not any("linkedin" in c for c in calls) and not any("hygienist" in c for c in calls)
    calls.clear()
    tally = jobs.run(desk, urls=["https://dental-sample.example/careers"], transport=jobs_server(calls), out=quiet)
    assert tally.counts["seen"] == 1
    assert calls == ["https://dental-sample.example/careers"]            # a seen job page is not opened again


def test_jobs_single_post_page(desk, store):
    jobs.run(desk, urls=["https://shop-sample.example/job"], transport=jobs_server([]), out=quiet)
    row = store.list_raw_items(source="jobs")[0]
    assert row["url"] == "https://shop-sample.example/job" and row["posted_at"] == "2026-09-29"
    assert "manual-data-entry" in row["matched"]["patterns"]


def test_jobs_no_pages_configured(desk):
    lines = []
    assert jobs.run(desk, out=lines.append).counts == {}
    assert "no job_pages" in lines[0]


def test_jobs_broken_page_does_not_stop(desk):
    tally = jobs.run(desk, urls=["https://gone-sample.example/careers", "https://shop-sample.example/job"],
                     transport=jobs_server([]), out=quiet)
    assert tally.counts == {"error": 1, "saved": 1}


# ---------- agencies ----------
AGENCY_HOME = """<html><head><title>Pixel Sample Agency</title></head><body><p>Shopify stores for brands.</p>
<a href="/careers">Careers</a><a href="https://twitter.com/x">Twitter</a></body></html>"""
AGENCY_JOBS = """<html><body><h1>We're hiring</h1><p>Shopify developer (contract) and automation engineer.
Our team is overloaded with client work.</p></body></html>"""


def agency_server():
    pages = {"https://pixel-sample.example/": AGENCY_HOME, "https://pixel-sample.example/careers": AGENCY_JOBS,
             "https://quiet-sample.example/": "<html><head><title>Quiet</title></head><body>Design studio.</body></html>"}

    def handler(request):
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body, headers={"content-type": "text/html"}) if body else httpx.Response(404)
    return httpx.MockTransport(handler)


def test_agency_hiring_hints(desk, store):
    agencies.run(desk, urls=["https://pixel-sample.example/", "https://quiet-sample.example/"],
                 transport=agency_server(), out=quiet)
    rows = {r["url"]: r for r in store.list_raw_items(source="agency")}
    pixel = rows["https://pixel-sample.example/"]
    assert pixel["extra"]["careers_url"] == "https://pixel-sample.example/careers"
    assert "we're hiring" in pixel["extra"]["hiring_words"] and "overloaded" in pixel["extra"]["hiring_words"]
    assert "shopify developer" in pixel["extra"]["dev_roles"]
    assert pixel["extra"]["channel_hint"] == "agency_pitch"
    assert rows["https://quiet-sample.example/"]["extra"]["hiring_words"] == []


def test_agency_directory_pages_refused(desk, store):
    lines = []
    tally = agencies.run(desk, urls=["https://clutch.co/agencies/shopify", "https://www.shopify.com/partners/x",
                                     "https://www.linkedin.com/company/x"], transport=agency_server(),
                         out=lines.append)
    assert tally.counts == {"error": 3} and store.list_raw_items() == []
    assert "Open it by hand" in lines[0] and "LinkedIn" in lines[2]


def test_agency_list_file(tmp_path):
    f = tmp_path / "agencies.txt"
    f.write_text("# my list\nhttps://pixel-sample.example/  # from Clutch\n\nhttps://quiet-sample.example/\n",
                 encoding="utf-8")
    assert agencies.read_list(f) == ["https://pixel-sample.example/", "https://quiet-sample.example/"]


# ---------- PageSpeed ----------
def psi_server(score, calls=None, status=200):
    def handler(request):
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"bad key SECRETKEY123 for {score}"}})
        return httpx.Response(200, json={"lighthouseResult": {
            "finalDisplayedUrl": "https://slow-sample.example/",
            "categories": {"performance": {"score": score}},
            "audits": {"largest-contentful-paint": {"displayValue": "8.1 s"},
                       "total-blocking-time": {"displayValue": "1,240 ms"}}}})
    return httpx.MockTransport(handler)


def test_pagespeed_slow_site_is_weak_evidence(desk, store):
    lead = desk.add_lead("https://slow-sample.example", "n", "email")
    calls = []
    out = pagespeed.run(desk, lead_id=lead, transport=psi_server(0.23, calls), out=quiet)
    assert out["score"] == 23 and out["evidence"]
    ev = store.get_evidence(out["evidence"])
    assert ev["grade"] == "WEAK_SIGNAL" and ev["source_type"] == "website" and ev["topic"] == "pain"
    assert ev["quote"] == "Performance score: 23/100"
    assert "Largest Contentful Paint: 8.1 s" in desk.snapshot_text(out["snapshot"])
    params = dict(calls[0].url.params)
    assert params["url"] == "https://slow-sample.example" and params["strategy"] == "mobile"


def test_pagespeed_fast_site_no_evidence(desk, store):
    lead = desk.add_lead("https://fast-sample.example", "n", "email")
    out = pagespeed.run(desk, lead_id=lead, transport=psi_server(0.91), out=quiet)
    assert out["score"] == 91 and out["evidence"] is None and store.evidence_for(lead) == []


def test_pagespeed_never_prints_key(desk, monkeypatch):
    monkeypatch.setattr(db, "load_env", lambda path=None: {"PAGESPEED_API_KEY": "SECRETKEY123"})
    with pytest.raises(db.DeskError) as err:
        pagespeed.run(desk, url="https://x-sample.example", transport=psi_server(0.5, status=400), out=quiet)
    assert "SECRETKEY123" not in str(err.value) and "***" in str(err.value)


def test_pagespeed_lead_must_be_in_research(desk, store):
    lead = desk.add_lead("https://late-sample.example", "n", "email")
    with store.conn:
        store.conn.execute("UPDATE opportunities SET status = 'contacted' WHERE id = ?", (lead,))
    with pytest.raises(db.InvalidMove):
        pagespeed.run(desk, lead_id=lead, transport=psi_server(0.2), out=quiet)


def test_pagespeed_platform_lead_needs_url(desk):
    lead = desk.add_lead("https://www.upwork.com/jobs/~01abc", "n", "upwork", company="Some Client sample")
    with pytest.raises(db.DeskError, match="--url"):
        pagespeed.run(desk, lead_id=lead, transport=psi_server(0.2), out=quiet)
