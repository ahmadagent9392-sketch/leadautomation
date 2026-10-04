"""Tests for scripts/followups.py: business days and follow-up dates (Stage 6). Pure math, no database."""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import followups as fu  # noqa: E402

CADENCE = yaml.safe_load((ROOT / "config" / "cadence.yaml").read_text(encoding="utf-8"))
POLICY = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))
MON = date(2026, 10, 5)
FRI = date(2026, 10, 9)
SAT = date(2026, 10, 10)


@pytest.mark.parametrize("start, n, want", [
    (MON, 1, date(2026, 10, 6)),
    (MON, 3, date(2026, 10, 8)),
    (FRI, 1, date(2026, 10, 12)),           # Friday + 1 = Monday
    (FRI, 3, date(2026, 10, 14)),
    (SAT, 1, date(2026, 10, 12)),
    (MON, 0, MON),
    (SAT, 0, date(2026, 10, 12)),           # 0 on a weekend = next Monday
    (MON, 10, date(2026, 10, 19)),
])
def test_add_business_days(start, n, want):
    assert fu.add_business_days(start, n) == want


def test_cadence_dates_from_first_message():
    # first message Monday Oct 5: touches due after 3, 7, 14, 24 business days
    got = [fu.next_touch_due(MON, MON, n, CADENCE, POLICY) for n in (1, 2, 3, 4)]
    assert got == [date(2026, 10, 8), date(2026, 10, 14), date(2026, 10, 23), date(2026, 11, 6)]


def test_late_follow_up_keeps_a_gap():
    # touch 2 was sent late (Oct 13); touch 3 by plan is Oct 14, but the gap needs 2 business days
    assert fu.next_touch_due(MON, date(2026, 10, 13), 2, CADENCE, POLICY) == date(2026, 10, 15)


def test_no_more_touches_after_max():
    assert fu.next_touch_due(MON, MON, 5, CADENCE, POLICY) is None
    assert fu.next_touch_due(MON, MON, 0, CADENCE, POLICY) is None        # nothing sent yet
    assert fu.max_touches(CADENCE, POLICY) == 5
    assert fu.max_touches(CADENCE, {"caps": {"max_touches_per_lead": 3}}) == 3
    assert fu.next_touch_due(MON, MON, 3, CADENCE, {"caps": {"max_touches_per_lead": 3}}) is None


def test_no_response_date():
    assert fu.no_response_on(MON, CADENCE) == date(2026, 10, 19)


def test_nurture_date():
    assert fu.nurture_on(MON, date(2027, 1, 15), CADENCE) == date(2027, 1, 15)
    assert fu.nurture_on(MON, None, CADENCE) == date(2026, 12, 4)           # + 60 days
    assert fu.nurture_on(MON, date(2026, 1, 1), CADENCE) == date(2026, 12, 4)   # a past date is ignored


def test_out_of_office_date():
    assert fu.after_out_of_office(MON, date(2026, 10, 16), CADENCE) == date(2026, 10, 19)   # back Fri -> Mon
    assert fu.after_out_of_office(MON, None, CADENCE) == date(2026, 10, 12)                 # + 5 business days


def test_defaults_when_cadence_is_missing_keys():
    assert fu.setting({}, "stale_after_days") == 21
    assert fu.next_touch_due(MON, MON, 1, {}, {}) == date(2026, 10, 8)
