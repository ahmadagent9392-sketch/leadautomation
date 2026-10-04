"""Tests for Stage 4 opportunity cards (scripts/cards.py). SQLite, no internet."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import cards  # noqa: E402
import rank  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402
from test_rank import add_proof, make_desk, make_verified, score  # noqa: E402

EVIL_PAGE = ("Evil Co careers. <script>alert('x')</script> we copy orders by hand every day into sheets. "
             "Posted 2026-09-20")


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "c.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return make_desk(store, tmp_path)


def test_cards_written_best_first(desk, store, tmp_path):
    a = make_verified(desk, store, url="https://alpha-dental.com")
    b = make_verified(desk, store, url="https://beta-clinic.com")
    for lead_id in (a, b):
        desk.set_research(lead_id, why_now="opening a second clinic")
    score(desk, a, fit=2, value=2)
    score(desk, b, fit=3, value=3)
    rank.run(desk)
    out = tmp_path / "cards"
    made = cards.write_cards(desk, out)
    assert [c["id"] for c in made] == [b, a]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert html.index(f'id="lead-{b}"') < html.index(f'id="lead-{a}"')
    assert "2 qualified lead(s)" in html and "<title>Opportunity Cards</title>" in html


def test_card_markdown_has_all_parts(desk, store, tmp_path):
    lead_id = make_verified(desk, store, email="sara@brightsmile.com")
    desk.set_research(lead_id, why_now="opening a second clinic", unknowns=["budget owner?"])
    score(desk, lead_id)
    rank.run(desk)
    cards.write_cards(desk, tmp_path / "cards")
    md = (tmp_path / "cards" / f"{lead_id}.md").read_text(encoding="utf-8")
    for part in ("# #1 Brightsmile", "**Priority", "## Problem proof", "[STRONG_SIGNAL]",
                 "> \"we can't keep up with patient calls\"", "https://brightsmile.com/careers",
                 "## Why now", "opening a second clinic", "## Who to contact",
                 "Sara Khan, Office Manager - sara@brightsmile.com (published)", "## Unknowns", "budget owner?",
                 "WARNING: no offer proof yet"):
        assert part in md, part


def test_only_qualified_leads_get_cards(desk, store, tmp_path):
    good = make_verified(desk, store, url="https://alpha-dental.com")
    bad = make_verified(desk, store, url="https://beta-clinic.com", contact=False)
    waiting = make_verified(desk, store, url="https://gamma-care.com")
    score(desk, good)
    score(desk, bad)
    rank.run(desk)
    made = cards.write_cards(desk, tmp_path / "cards")
    assert [c["id"] for c in made] == [good]
    assert not (tmp_path / "cards" / f"{bad}.md").exists()
    assert not (tmp_path / "cards" / f"{waiting}.md").exists()


def test_empty_index_when_nothing_qualified(desk, tmp_path):
    cards.write_cards(desk, tmp_path / "cards")
    assert "No qualified leads yet" in (tmp_path / "cards" / "index.html").read_text(encoding="utf-8")


def test_page_text_is_escaped_in_html(desk, store, tmp_path):
    lead_id = make_verified(desk, store, proof=False)
    add_proof(desk, lead_id, url="https://brightsmile.com/jobs", page=EVIL_PAGE,
              quote="<script>alert('x')</script> we copy orders by hand")
    score(desk, lead_id)
    rank.run(desk)
    cards.write_cards(desk, tmp_path / "cards")
    html = (tmp_path / "cards" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_only_web_links_are_clickable():
    assert cards.link("javascript:alert(1)", "x") == "x"
    assert "href" not in cards.link("data:text/html,hi")
    assert cards.link("https://a.com/?q=<b>", "go") == \
        '<a href="https://a.com/?q=&lt;b&gt;" target="_blank" rel="noopener noreferrer">go</a>'


def test_linkedin_profile_is_look_up_by_hand(desk, store, tmp_path):
    lead_id = make_verified(desk, store, contact=False)
    desk.store.conn.execute("UPDATE opportunities SET status='researched' WHERE id=?", (lead_id,))
    desk.set_contact(lead_id, title="Owner", name="Ali", role_type="owner",
                     profile_url="https://www.linkedin.com/in/someone")
    desk.store.conn.execute("UPDATE opportunities SET status='verified' WHERE id=?", (lead_id,))
    desk.store.conn.commit()
    score(desk, lead_id)
    rank.run(desk)
    cards.write_cards(desk, tmp_path / "cards")
    assert "(look up by hand)" in (tmp_path / "cards" / f"{lead_id}.md").read_text(encoding="utf-8")
    assert "(look up by hand)" in (tmp_path / "cards" / "index.html").read_text(encoding="utf-8")
