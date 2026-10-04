#!/usr/bin/env python3
"""Check that an evidence quote is really inside a saved page snapshot, and if the proof is still fresh.

Usage:
    python scripts/quote_check.py --evidence EID          (reads the evidence row + its snapshot)
    python scripts/quote_check.py --sha SHA --quote "exact text"

Result:
    FOUND        quote is in the page (case, spaces, curly quotes and dashes are ignored)
    FOUND_FUZZY  quote is in the page with a tiny difference (a typo, one changed word)
    NOT_FOUND    quote is not in the page -> the grade must be UNKNOWN
Exit code: 0 = found, 1 = not found or error.

The rules here are used by scripts/db.py (add-evidence, verify-evidence), so the AI cannot skip them.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

FOUND, FOUND_FUZZY, NOT_FOUND = "FOUND", "FOUND_FUZZY", "NOT_FOUND"
FUZZY_MIN = 0.90          # similarity needed for FOUND_FUZZY
MIN_QUOTE_CHARS = 15      # shorter quotes prove nothing ("the", "data entry")
MAX_QUOTE_CHARS = 300
DEFAULT_DECAY_DAYS = 180

GRADES = ("CONFIRMED_FACT", "STRONG_SIGNAL", "WEAK_SIGNAL", "INFERENCE", "UNKNOWN")
GRADE_RANK = {"CONFIRMED_FACT": 4, "STRONG_SIGNAL": 3, "WEAK_SIGNAL": 2, "INFERENCE": 1, "UNKNOWN": 0}
LOWER = {"CONFIRMED_FACT": "STRONG_SIGNAL", "STRONG_SIGNAL": "WEAK_SIGNAL", "WEAK_SIGNAL": "UNKNOWN",
         "INFERENCE": "UNKNOWN", "UNKNOWN": "UNKNOWN"}
QUOTED_GRADES = ("CONFIRMED_FACT", "STRONG_SIGNAL", "WEAK_SIGNAL")   # these need a quote in a snapshot

# evidence source_type -> signal type in config/problems.yaml (None = proof does not get old)
SIGNAL_FOR_SOURCE = {"job_post": "job_post", "help_request": "help_request", "review": "bad_review",
                     "website": "website_issue", "news": "funding_or_growth", "profile": None, "manual": None}
# sources that must have a date: without a date we cannot prove the problem is still fresh
DATED_SOURCES = ("job_post", "help_request", "review", "news")

_CHAR_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "​": "", "‌": "", "‍": "", "﻿": "", "­": "",
})


def normalize(text: str) -> str:
    """Lower case, same quotes/dashes, one space between words."""
    text = unicodedata.normalize("NFKC", text or "").translate(_CHAR_MAP).casefold()
    return re.sub(r"\s+", " ", text).strip()


def find_quote(quote: str, text: str) -> tuple[str, float, str]:
    """Returns (FOUND | FOUND_FUZZY | NOT_FOUND, similarity 0..1, best matching page text)."""
    q, t = normalize(quote), normalize(text)
    if not q or not t:
        return NOT_FOUND, 0.0, ""
    if q in t:
        return FOUND, 1.0, q
    q_words, t_words = q.split(), t.split()
    q_set = set(q_words)
    best, best_text = 0.0, ""
    n = len(q_words)
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(t_words) - size + 1)):
            window = t_words[i:i + size]
            # quick filter: most quote words must be in this window
            if len(q_set & set(window)) < 0.8 * len(q_set):
                continue
            candidate = " ".join(window)
            ratio = difflib.SequenceMatcher(None, candidate, q, autojunk=False).ratio()
            if ratio > best:
                best, best_text = ratio, candidate
    if best >= FUZZY_MIN:
        return FOUND_FUZZY, round(best, 3), best_text
    return NOT_FOUND, round(best, 3), best_text


def is_found(status: str) -> bool:
    return status in (FOUND, FOUND_FUZZY)


def decay_days_for(pattern_id: str | None, source_type: str | None, problems: dict) -> int | None:
    """How many days this kind of proof stays fresh (config/problems.yaml). None = it does not get old."""
    signal = SIGNAL_FOR_SOURCE.get(source_type or "", "")
    if signal is None:
        return None
    patterns = problems.get("patterns") or []
    chosen = [p for p in patterns if p.get("id") == pattern_id] or patterns   # unknown pattern -> strictest
    days = [int(s["decay_days"]) for p in chosen for s in (p.get("signals") or [])
            if s.get("type") == signal and s.get("decay_days")]
    return min(days) if days else DEFAULT_DECAY_DAYS


def parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_stale(observed_at, decay_days: int | None, today: date) -> bool:
    observed = parse_date(observed_at)
    if observed is None or decay_days is None:
        return False
    return (today - observed).days > decay_days


def lower_grade(grade: str) -> str:
    return LOWER[grade]


def lowest(*grades: str) -> str:
    return min(grades, key=lambda g: GRADE_RANK[g])


def max_grade(grade: str, *, quote_status: str | None, source_type: str | None, observed_at,
              decay_days: int | None, today: date, depends_ok: bool = True) -> tuple[str, list[str]]:
    """The highest grade the proof allows. Returns (grade, reasons it was lowered)."""
    reasons: list[str] = []
    if grade == "INFERENCE":
        if not depends_ok:
            reasons.append("inference without checked evidence to stand on")
            return "UNKNOWN", reasons
        return grade, reasons
    if grade in QUOTED_GRADES:
        if quote_status is None or not is_found(quote_status):
            reasons.append("quote not found in the saved page")
            return "UNKNOWN", reasons
        if is_stale(observed_at, decay_days, today):
            reasons.append(f"proof is older than {decay_days} days")
            grade = lower_grade(grade)
        elif observed_at in (None, "") and source_type in DATED_SOURCES:
            reasons.append(f"{source_type} has no date, freshness not proven")
            grade = lower_grade(grade)
    return grade, reasons


# ---------- command line ----------
def _print_result(status: str, score: float, match: str, quote: str) -> None:
    print(f"{status}  (similarity {score:.2f})")
    print(f"  quote : {quote[:200]}")
    if status != FOUND and match:
        print(f"  page  : {match[:200]}")
    if not is_found(status):
        print("  -> grade must be UNKNOWN")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import db  # here, so the pure functions above need no database

    parser = argparse.ArgumentParser(description="Check a quote against a saved page snapshot.")
    parser.add_argument("--evidence", type=int, help="evidence id")
    parser.add_argument("--sha", help="snapshot sha256")
    parser.add_argument("--quote", help="quote text (with --sha)")
    parser.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--snapshots", type=Path, default=None, help="snapshot folder (default data/snapshots)")
    args = parser.parse_args(argv)
    if not args.evidence and not (args.sha and args.quote):
        parser.error("use --evidence EID, or --sha SHA --quote TEXT")

    store = None
    try:
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store, snapshot_dir=args.snapshots)
        if args.evidence:
            ev, report = desk.check_evidence(args.evidence)
            print(f"Evidence E{ev['id']} [{ev['grade']}] {ev['claim'][:80]}")
            print(f"  url   : {ev['url']}")
            if report["quote_status"] is None:
                print(f"  quote check: skipped ({report['skip_reason']})")
            else:
                _print_result(report["quote_status"], report["score"], report["match"], ev["quote"])
            print(f"  date  : {ev.get('observed_at') or 'none'}   fresh for: "
                  f"{report['decay_days'] if report['decay_days'] is not None else 'always'} days   "
                  f"stale: {'YES' if report['stale'] else 'no'}")
            print(f"  max grade allowed: {report['max_grade']}"
                  + (f"  ({'; '.join(report['reasons'])})" if report["reasons"] else ""))
            return 0 if report["quote_status"] is None or is_found(report["quote_status"]) else 1
        text = desk.snapshot_text(args.sha)
        status, score, match = find_quote(args.quote, text)
        _print_result(status, score, match, args.quote)
        return 0 if is_found(status) else 1
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
