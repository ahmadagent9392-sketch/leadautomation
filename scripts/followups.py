#!/usr/bin/env python3
"""Follow-up timers (Stage 6). Nothing is ever sent: a due follow-up only means "/draft ID can write one".

Rules (config/cadence.yaml):
  - follow-up n (n = 2..5) is due `followup_days[n-2]` business days (Mon-Fri) after the FIRST message was sent,
    and at least `min_gap_business_days` after the last message;
  - stop on any reply, opt-out, bounce, max touches (cadence max_touches and policy max_touches_per_lead);
  - after the last touch, no reply for `no_response_after_business_days` -> lead moves to no_response;
  - not_now reply -> a "nurture" reminder at their date (or + not_now_default_days);
  - out-of-office -> the pending follow-up moves after their return date.

Usage:
    python scripts/followups.py            # update all timers (safe to run many times)
    python scripts/followups.py --dry-run  # only show what would change
Also takes --backend supabase|sqlite and --db PATH (like desk.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULTS = {"followup_days": [3, 7, 14, 24], "max_touches": 5, "not_now_default_days": 60,
            "stale_after_days": 21, "no_response_after_business_days": 10, "min_gap_business_days": 2,
            "ooo_default_business_days": 5}


def setting(cadence: dict, name: str):
    value = (cadence or {}).get(name)
    return DEFAULTS[name] if value is None else value


def is_business_day(d: date) -> bool:
    return d.weekday() < 5          # Mon-Fri; no holiday list


def add_business_days(d: date, n: int) -> date:
    """n business days after d (d itself does not count). n = 0 -> d, or the next business day if d is a weekend."""
    if n <= 0:
        while not is_business_day(d):
            d += timedelta(days=1)
        return d
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


def max_touches(cadence: dict, policy: dict) -> int:
    limits = [int(setting(cadence, "max_touches"))]
    cap = (policy.get("caps") or {}).get("max_touches_per_lead")
    if cap:
        limits.append(int(cap))
    return min(limits)


def next_touch_due(first_sent: date, last_sent: date, touches_sent: int, cadence: dict, policy: dict) -> date | None:
    """Due date of the next follow-up, or None when no more touches are allowed."""
    days = list(setting(cadence, "followup_days"))
    n = touches_sent + 1                          # the touch we would write next
    if touches_sent < 1 or n > max_touches(cadence, policy) or n - 2 >= len(days):
        return None
    by_plan = add_business_days(first_sent, int(days[n - 2]))
    by_gap = add_business_days(last_sent, int(setting(cadence, "min_gap_business_days")))
    return max(by_plan, by_gap)


def no_response_on(last_sent: date, cadence: dict) -> date:
    return add_business_days(last_sent, int(setting(cadence, "no_response_after_business_days")))


def nurture_on(today: date, their_date: date | None, cadence: dict) -> date:
    if their_date and their_date > today:
        return their_date
    return today + timedelta(days=int(setting(cadence, "not_now_default_days")))


def after_out_of_office(today: date, back_on: date | None, cadence: dict) -> date:
    if back_on and back_on >= today:
        return add_business_days(back_on, 1)
    return add_business_days(today, int(setting(cadence, "ooo_default_business_days")))


def main(argv: list[str] | None = None) -> int:
    import db  # here, not at the top: db imports this file

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Update follow-up timers for all leads.")
    parser.add_argument("--dry-run", action="store_true", help="only show what would change")
    parser.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        changes = db.Desk(store).sync_followups(dry_run=args.dry_run)
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()
    word = "Would change" if args.dry_run else "Changed"
    if not changes:
        print("Follow-up timers are up to date. Nothing changed.")
    for c in changes:
        print(f"{word}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
