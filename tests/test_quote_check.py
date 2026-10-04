"""Tests for scripts/quote_check.py: is the quote really in the page, and is the proof still fresh?"""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import quote_check as qc  # noqa: E402

PAGE = """Bright Smile Dental - Careers
We are hiring a Front Desk Coordinator.
Our phones ring all day and we can't keep up with patient calls and web form inquiries.
Many messages wait two or three days for a reply.
Posted: September 20, 2026"""
TODAY = date(2026, 10, 4)


# ---------- quote in page ----------
def test_quote_present_exact():
    assert qc.find_quote("we can't keep up with patient calls", PAGE)[0] == qc.FOUND


def test_quote_case_spaces_and_curly_quotes_ignored():
    quote = "WE  CAN’T keep up\nwith   patient calls"
    assert qc.find_quote(quote, PAGE)[0] == qc.FOUND


def test_dashes_and_invisible_chars_ignored():
    assert qc.find_quote("Bright Smile Dental — Careers", PAGE)[0] == qc.FOUND
    assert qc.find_quote("Front​ Desk Coordinator", PAGE)[0] == qc.FOUND


def test_small_typo_is_fuzzy_found():
    status, score, match = qc.find_quote("Many messages wait two or three days for a replly.", PAGE)
    assert status == qc.FOUND_FUZZY and score >= qc.FUZZY_MIN
    assert "two or three days" in match


def test_quote_missing():
    status, score, _ = qc.find_quote("We lost 40% of our customers last year because of slow replies", PAGE)
    assert status == qc.NOT_FOUND and not qc.is_found(status)


def test_paraphrase_is_not_found():
    # same meaning, different words: this is NOT a quote
    assert qc.find_quote("we cannot handle all the calls from patients", PAGE)[0] == qc.NOT_FOUND
    assert qc.find_quote("messages often wait days before anyone replies", PAGE)[0] == qc.NOT_FOUND


def test_empty_inputs():
    assert qc.find_quote("", PAGE)[0] == qc.NOT_FOUND
    assert qc.find_quote("hello there friend", "")[0] == qc.NOT_FOUND


# ---------- freshness ----------
PROBLEMS = db.load_yaml("problems")


def test_decay_days_from_config():
    assert qc.decay_days_for("manual-data-entry", "job_post", PROBLEMS) == 45
    assert qc.decay_days_for("missed-leads-slow-replies", "help_request", PROBLEMS) == 14
    assert qc.decay_days_for("manual-data-entry", "review", PROBLEMS) == 120
    assert qc.decay_days_for(None, "job_post", PROBLEMS) == 45          # unknown pattern -> strictest
    assert qc.decay_days_for("manual-data-entry", "profile", PROBLEMS) is None
    assert qc.decay_days_for("x", "job_post", {"patterns": []}) == qc.DEFAULT_DECAY_DAYS


def test_stale_date():
    assert qc.is_stale("2026-08-01", 45, TODAY) is True       # 64 days old
    assert qc.is_stale("2026-09-20", 45, TODAY) is False      # 14 days old
    assert qc.is_stale("2026-08-20", 45, TODAY) is False      # exactly 45 days: still fresh
    assert qc.is_stale(None, 45, TODAY) is False
    assert qc.is_stale("2020-01-01", None, TODAY) is False    # profile pages do not get old


# ---------- grades ----------
def test_lower_grade_one_level():
    assert qc.lower_grade("CONFIRMED_FACT") == "STRONG_SIGNAL"
    assert qc.lower_grade("STRONG_SIGNAL") == "WEAK_SIGNAL"
    assert qc.lower_grade("WEAK_SIGNAL") == "UNKNOWN"
    assert qc.lower_grade("UNKNOWN") == "UNKNOWN"


def _max(grade, **kw):
    base = dict(quote_status=qc.FOUND, source_type="job_post", observed_at="2026-09-20", decay_days=45,
                today=TODAY)
    base.update(kw)
    return qc.max_grade(grade, **base)


def test_max_grade_rules():
    assert _max("STRONG_SIGNAL") == ("STRONG_SIGNAL", [])
    assert _max("STRONG_SIGNAL", quote_status=qc.NOT_FOUND)[0] == "UNKNOWN"
    assert _max("CONFIRMED_FACT", quote_status=None)[0] == "UNKNOWN"
    assert _max("STRONG_SIGNAL", observed_at="2026-06-01")[0] == "WEAK_SIGNAL"        # stale
    assert _max("STRONG_SIGNAL", observed_at=None)[0] == "WEAK_SIGNAL"                # job post without date
    assert _max("STRONG_SIGNAL", observed_at=None, source_type="website", decay_days=180)[0] == "STRONG_SIGNAL"
    assert _max("INFERENCE", quote_status=None)[0] == "INFERENCE"
    assert _max("INFERENCE", quote_status=None, depends_ok=False)[0] == "UNKNOWN"


def test_lowest():
    assert qc.lowest("CONFIRMED_FACT", "WEAK_SIGNAL", "STRONG_SIGNAL") == "WEAK_SIGNAL"
