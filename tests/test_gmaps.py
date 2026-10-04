"""Tests for scripts/sources/gmaps.py with saved sample pages (no live Google, no browser)."""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import scout  # noqa: E402
import httpx  # noqa: E402
from sources import gmaps, places  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sources"
NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 4)


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "g.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return db.Desk(store, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


# ---------- pure parsing ----------
def test_parse_results_each_place_once():
    res = gmaps.parse_results(page("gmaps_results.html"))
    assert [r["name"] for r in res] == ["Sunny Smile Dental (sample)", "Maple Tooth Care (sample)"]


def test_canonical_place_url_uses_cid():
    url = "https://www.google.com/maps/place/X/@1,2,17z/data=!4m6!3m5!1s0x8640bf1:0x1a2b3c!8m2"
    assert gmaps.canonical_place_url(url) == f"https://maps.google.com/?cid={int('1a2b3c', 16)}"
    assert gmaps.canonical_place_url("https://maps.google.com/?cid=42") == "https://maps.google.com/?cid=42"
    assert gmaps.canonical_place_url("https://www.google.com/maps/place/Y/@1,2,3z") == \
        "https://www.google.com/maps/place/Y"


def test_parse_place_public_info():
    p = gmaps.parse_place(page("gmaps_place.html"))
    assert p == {"name": "Sunny Smile Dental (sample)", "category": "Dentist",
                 "address": "100 Sample St, Houston, TX 77002", "website": "https://sunnysmile-sample.example/",
                 "phone": "(713) 555-0100", "rating": 3.4, "review_count": 128}


def test_parse_reviews_lowest_first_no_names_dates():
    reviews = gmaps.parse_reviews(page("gmaps_place.html"), TODAY)
    assert [r["stars"] for r in reviews] == [1, 2, 5]          # empty review skipped, nested copy not doubled
    assert reviews[0]["date"] == "2026-08-05" and reviews[0]["approx"] is True
    assert reviews[1]["date"] == "2026-09-13"
    assert set(reviews[0]) == {"stars", "date", "approx", "text"}
    blob = json.dumps(reviews)
    for name in ("John Reviewer", "Mary Sample", "Pat Example"):
        assert name not in blob


def test_is_blocked():
    assert gmaps.is_blocked("https://www.google.com/maps/search/x", page("gmaps_captcha.html"))
    assert gmaps.is_blocked("https://www.google.com/sorry/index?continue=x", "<html></html>")
    assert not gmaps.is_blocked("https://www.google.com/maps/search/x", page("gmaps_results.html"))


def test_analyze_site_contact_form_booking():
    site = gmaps.analyze_site(page("site_home.html"), "https://sunnysmile-sample.example/")
    assert site["contact_page"] == "https://sunnysmile-sample.example/contact-us"
    assert site["has_booking"] and "calendly" in site["booking_links"][0]
    assert site["has_form"] is False                         # a search box is not a contact form
    site = gmaps.analyze_site(page("site_home.html"), "https://sunnysmile-sample.example/",
                              page("site_contact.html"), "https://sunnysmile-sample.example/contact-us")
    assert site["has_form"] is True


# ---------- the run, with a fake browser ----------
class FakeBrowser:
    calls: list = []
    blocked_on_place = False

    def __init__(self, wait, headless=True):
        self.wait = wait

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, query, limit):
        FakeBrowser.calls.append(("search", query))
        self.wait()
        return gmaps.parse_results(page("gmaps_results.html"))[:limit]

    def place(self, url, today):
        FakeBrowser.calls.append(("place", url))
        if FakeBrowser.blocked_on_place:
            raise gmaps.GmapsBlocked("captcha")
        return gmaps.parse_place(page("gmaps_place.html")), gmaps.parse_reviews(page("gmaps_place.html"), today)

    def website(self, url):
        FakeBrowser.calls.append(("website", url))
        site = gmaps.analyze_site(page("site_home.html"), url, page("site_contact.html"), url + "contact-us")
        return site, "Family dentistry in Houston.\nCONTACT PAGE:\nOwner: Dr. Sample Person"


@pytest.fixture(autouse=True)
def reset_fake(monkeypatch):
    FakeBrowser.calls = []
    FakeBrowser.blocked_on_place = False
    monkeypatch.setattr(places, "api_key", lambda: None)      # tests never use a real key


def run(desk, query="dental clinic in Houston TX", **kw):
    lines = []
    waits = []
    tally = gmaps.run(desk, query, browser_factory=FakeBrowser, sleep=waits.append, rand=lambda a, b: (a + b) / 2,
                      out=lines.append, **kw)
    return tally, lines, waits


def test_run_saves_places_with_problem_reviews(desk, store):
    tally, lines, waits = run(desk)
    assert tally.counts == {"saved": 2}
    assert waits and all(w == 5.5 for w in waits)            # random 3-8 s wait between actions
    raw = store.list_raw_items(source="gmaps")[0]
    assert raw["url"].startswith("https://maps.google.com/?cid=")
    assert raw["extra"]["website"] == "https://sunnysmile-sample.example/"
    assert raw["extra"]["website_check"]["has_form"] is True
    assert raw["extra"]["website_check"]["snapshot"]
    hits = raw["matched"]["patterns"]["missed-leads-slow-replies"]
    assert "never called back" in hits and "could not book" in hits
    assert raw["posted_at"] == "2026-09-13"                  # newest problem review
    blob = json.dumps(raw)
    for name in ("John Reviewer", "Mary Sample", "Pat Example"):
        assert name not in blob                              # reviewer names are never stored


def test_cache_same_place_not_opened_twice(desk):
    run(desk)
    FakeBrowser.calls = []
    tally, _, _ = run(desk)
    assert tally.counts == {"seen": 2}
    assert [c for c in FakeBrowser.calls if c[0] == "place"] == []


def test_daily_search_cap(desk):
    for i in range(3):
        desk.store.add_event("gmaps_search", "role:gmaps", None, {"query": f"q{i}"})
    with pytest.raises(db.CapReached, match="3 Google Maps searches"):
        run(desk)
    assert FakeBrowser.calls == []


def test_daily_business_cap(desk):
    for i in range(59):
        desk.store.add_event("gmaps_business", "role:gmaps", None, {"url": f"u{i}"})
    tally, _, _ = run(desk)
    assert tally.counts == {"saved": 1}                      # only 1 business left today
    for i in range(1):
        desk.store.add_event("gmaps_business", "role:gmaps", None, {"url": "x"})
    with pytest.raises(db.CapReached, match="60 businesses"):
        run(desk, query="dentist in Austin TX")


def test_captcha_stops_and_logs_and_blocks_rest_of_day(desk, store):
    FakeBrowser.blocked_on_place = True
    with pytest.raises(gmaps.GmapsBlocked):
        run(desk)
    assert store.count_events("gmaps_blocked", "2000-01-01") == 1
    assert store.list_raw_items() == []
    FakeBrowser.blocked_on_place = False
    with pytest.raises(db.CapReached, match="blocked us earlier today"):
        run(desk, query="dentist in Austin TX")


def test_main_exit_code_3_on_block(tmp_path, monkeypatch, capsys):
    FakeBrowser.blocked_on_place = True
    monkeypatch.setattr(db, "DEFAULT_SNAPSHOTS", tmp_path / "snaps")
    code = gmaps.main(["dental clinic in Houston TX", "--backend", "sqlite", "--db", str(tmp_path / "m.db")],
                      browser_factory=FakeBrowser)
    assert code == 3
    assert "STOPPED" in capsys.readouterr().out


def test_dry_run_saves_nothing_but_counts_search(desk, store):
    tally, _, _ = run(desk, dry_run=True)
    assert tally.counts == {"dry_run": 2}
    assert store.list_raw_items() == []
    assert store.count_events("gmaps_search", "2000-01-01") == 1


def test_scout_keep_gmaps_makes_review_evidence(desk, store):
    run(desk)
    raw = store.list_raw_items(source="gmaps")[0]
    out = scout.keep(desk, raw["id"], pattern_id="missed-leads-slow-replies", signal="bad_review", channel="email",
                     reason="two recent reviews: calls not answered, no reply")
    lead = desk.get(out["lead_id"])
    assert lead["company_domain"] == "sunnysmile-sample.example"
    assert lead["company_name"] == "Sunny Smile Dental (sample)"
    ev = store.evidence_for(out["lead_id"])
    assert [e["grade"] for e in ev] == ["STRONG_SIGNAL", "WEAK_SIGNAL"]   # 2+ similar reviews -> STRONG
    assert all(e["source_type"] == "review" and e["topic"] == "pain" for e in ev)
    assert ev[0]["observed_at"] == "2026-08-05"
    # second place in the same run is the same business (same website) -> duplicate
    other = store.list_raw_items(source="gmaps", status="new")[0]
    with pytest.raises(db.DuplicateLead):
        scout.keep(desk, other["id"], pattern_id="missed-leads-slow-replies", signal="bad_review",
                   channel="email", reason="same reviews")
    assert store.get_raw_item(other["id"])["status"] == "rejected"


def test_short_query_refused(desk):
    with pytest.raises(db.DeskError):
        run(desk, query="x")


def test_limited_view_is_detected_and_reported(desk, store):
    limited = page("gmaps_place.html").replace("</body>", "<div>You're seeing a limited view of Google Maps. "
                                               "<a href='#'>See more</a></div></body>")
    assert gmaps.is_limited_view(limited) and not gmaps.is_limited_view(page("gmaps_place.html"))

    class LimitedBrowser(FakeBrowser):
        def place(self, url, today):
            p = gmaps.parse_place(limited)
            p["limited_view"] = True
            return p, []
    lines = []
    gmaps.run(desk, "dental clinic in Houston TX", browser_factory=LimitedBrowser, sleep=lambda s: None,
              out=lines.append)
    assert any("limited view" in line for line in lines)
    raw = store.list_raw_items(source="gmaps")[0]
    assert raw["extra"]["limited_view"] is True and raw["extra"]["reviews"] == [] and raw["matched"] == {}


# ---------- Places API for reviews (Maps "limited view") ----------
class LimitedOnly(FakeBrowser):
    def place(self, url, today):
        p = gmaps.parse_place(page("gmaps_place.html"))
        p.update(limited_view=True, review_count=None)
        return p, []

    def website(self, url):
        return {"url": url, "has_form": False}, ""


PLACES_REVIEWS = {"rating": 3.4, "userRatingCount": 128, "reviews": [
    {"rating": 5, "publishTime": "2026-09-01T10:00:00Z", "text": {"text": "Great team."},
     "authorAttribution": {"displayName": "Secret Reviewer Name"}},
    {"rating": 1, "publishTime": "2026-09-20T10:00:00Z",
     "originalText": {"text": "Called five times, nobody answered and they never called back."},
     "authorAttribution": {"displayName": "Another Reviewer"}},
]}


def places_server(calls, found_name="Sunny Smile Dental (sample)", status=200):
    def handler(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "API key PLACESKEY1 not valid"}})
        if request.url.path.endswith(":searchText"):
            return httpx.Response(200, json={"places": [{"id": "abc", "displayName": {"text": found_name}}]})
        return httpx.Response(200, json=PLACES_REVIEWS)
    return httpx.MockTransport(handler)


def run_places(desk, monkeypatch, transport, **kw):
    monkeypatch.setattr(places, "api_key", lambda: "PLACESKEY1")
    lines = []
    gmaps.run(desk, "dental clinic in Houston TX", browser_factory=LimitedOnly, sleep=lambda s: None,
              out=lines.append, places_transport=transport, **kw)
    return lines


def test_limited_view_uses_places_api_for_reviews(desk, store, monkeypatch):
    calls = []
    run_places(desk, monkeypatch, places_server(calls))
    raw = store.list_raw_items(source="gmaps")[0]
    assert raw["extra"]["review_source"] == "places_api" and raw["extra"]["review_count"] == 128
    assert [r["stars"] for r in raw["extra"]["reviews"]] == [1, 5]          # lowest first
    assert raw["extra"]["reviews"][0]["date"] == "2026-09-20" and raw["extra"]["reviews"][0]["approx"] is False
    assert "never called back" in raw["matched"]["keywords"]
    assert "Reviewer" not in json.dumps(raw)                                 # names dropped
    assert calls[0].headers["x-goog-api-key"] == "PLACESKEY1"
    assert calls[0].headers["x-goog-fieldmask"] == "places.id,places.displayName"
    assert store.count_events("places_call", "2000-01-01") == 4             # 2 places x (search + details)


def test_places_wrong_business_not_used(desk, store, monkeypatch):
    run_places(desk, monkeypatch, places_server([], found_name="Totally Different Plumbing"))
    raw = store.list_raw_items(source="gmaps")[0]
    assert raw["extra"]["reviews"] == [] and "different business" in raw["extra"]["review_note"]


def test_places_daily_limit(desk, store, monkeypatch):
    for _ in range(29):
        store.add_event("places_call", "role:places", None, {})
    calls = []
    lines = run_places(desk, monkeypatch, places_server(calls))
    assert calls == [] and any("Places API limit" in line for line in lines)
    assert store.list_raw_items(source="gmaps")[0]["extra"]["reviews"] == []


def test_places_error_hides_key(desk, store, monkeypatch):
    lines = run_places(desk, monkeypatch, places_server([], status=400))
    joined = " ".join(lines)
    assert "Places API error HTTP 400" in joined and "PLACESKEY1" not in joined and "***" in joined


def test_no_key_gives_hint(desk):
    lines = []
    gmaps.run(desk, "dental clinic in Houston TX", browser_factory=LimitedOnly, sleep=lambda s: None,
              out=lines.append)
    assert any("GOOGLE_PLACES_API_KEY" in line for line in lines)
