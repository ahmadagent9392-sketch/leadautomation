#!/usr/bin/env python3
"""Ranking (Stage 4): hard gates + priority. Moves verified leads to qualified or rejected.

Usage:
    python scripts/rank.py                 # rank all verified + qualified leads
    python scripts/rank.py --id 7          # only lead #7
    python scripts/rank.py --dry-run       # show the result, change nothing
    python scripts/rank.py --need-score    # verified leads that still need fit / value (see /cards)

Hard gates (all must pass, else the lead is rejected with the reasons):
    problem proof (checked, fresh today) · fit >= 2 · value >= pattern min_value_band · owner role known ·
    offer proof (warning only while offer.yaml status is not 'decided') · not blocked · not excluded
Priority = evidence(1-3) x fit(1-3) x urgency(0-2) x value(1-3). Ties: newest problem proof first.
Fit and value are judgment: Claude sets them with `desk.py set-score` in /cards. The rest is code.

Also takes --backend supabase|sqlite and --db PATH (like desk.py).
Exit code: 0 = OK, 1 = error.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import db
import quote_check as qc

ACTOR = "code:rank"
MIN_FIT = 2
EVIDENCE_POINTS = {"CONFIRMED_FACT": 3, "STRONG_SIGNAL": 2}      # two WEAK from different sources = 1
STRONG = ("CONFIRMED_FACT", "STRONG_SIGNAL")
PERSONAL_EMAIL_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com",
                          "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
                          "gmx.com", "mail.com", "yandex.com", "zoho.com"}
SOURCE_WORDS = {"job_post": "job post", "help_request": "help request", "review": "review", "website": "website",
                "news": "news", "profile": "profile", "manual": "pasted by Ahmad"}
GRADE_WORDS = {"CONFIRMED_FACT": "CONFIRMED", "STRONG_SIGNAL": "STRONG", "WEAK_SIGNAL": "WEAK"}


@dataclass
class Result:
    lead_id: int
    company: str
    status: str                      # status before ranking
    outcome: str = "waiting"         # qualified | rejected | waiting
    fails: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    factors: dict = field(default_factory=dict)      # name -> {"value": n, "why": text}
    priority: int | None = None
    why: str = ""
    newest_proof: str | None = None  # YYYY-MM-DD of the newest counted problem proof


# ---------- helpers ----------
def pattern_for(desk: db.Desk, pattern_id: str | None) -> dict | None:
    for p in desk.problems.get("patterns") or []:
        if p.get("id") == pattern_id:
            return p
    return None


def offer_proof(offer_cfg: dict, offer_id: str | None) -> tuple[bool, bool]:
    """Returns (offer is decided, the offer has at least one proof item with title + link)."""
    decided = str(offer_cfg.get("status") or "").strip().lower() == "decided"
    offers = offer_cfg.get("offers") or []
    chosen = [o for o in offers if o.get("id") == offer_id] or offers[:1]
    has = any((item or {}).get("title", "").strip() and (item or {}).get("link", "").strip()
              for o in chosen for item in (o.get("proof") or []))
    return decided, has


def _fresh(desk: db.Desk, lead: dict, ev: dict, today: date) -> bool:
    decay = qc.decay_days_for(lead.get("pattern_id"), ev.get("source_type"), desk.problems)
    return not qc.is_stale(ev.get("observed_at"), decay, today)


def _enough(pain: list[dict]) -> bool:
    """1 STRONG / CONFIRMED, or 2 WEAK from different source types."""
    weak_sources = {e.get("source_type") for e in pain if e["grade"] == "WEAK_SIGNAL"}
    return any(e["grade"] in STRONG for e in pain) or len(weak_sources) >= 2


def _date(ev: dict) -> str | None:
    d = qc.parse_date(ev.get("observed_at"))
    return d.isoformat() if d else None


def _proof_text(ev: dict) -> str:
    when = _date(ev) or "no date"
    return f"{GRADE_WORDS.get(ev['grade'], ev['grade'])} proof ({SOURCE_WORDS.get(ev.get('source_type'), 'source')} {when})"


def owner_of(desk: db.Desk, lead: dict) -> dict | None:
    people = [p for p in desk.store.people_for(lead["company_id"]) if (p.get("title") or "").strip()]
    for p in people:
        if p["id"] == lead.get("owner_person_id"):
            return p
    return people[0] if people else None


# ---------- the rules ----------
def evaluate(desk: db.Desk, lead: dict, offer_cfg: dict) -> Result:
    """Checks every gate and computes the priority. Changes nothing."""
    today = desk.today()
    info = lead.get("rank_info") or {}
    r = Result(lead["id"], lead.get("company_name") or "?", lead["status"])

    pattern = pattern_for(desk, lead.get("pattern_id"))
    if pattern is None:
        r.fails.append("no problem pattern set (set-research --pattern)")

    # 1. problem proof, checked and fresh today
    evidence = [e for e in desk.store.evidence_for(lead["id"]) if e.get("verified")]
    pain = [e for e in evidence if e.get("topic") == "pain" and e["grade"] in STRONG + ("WEAK_SIGNAL",)]
    fresh = [e for e in pain if _fresh(desk, lead, e, today)]
    strong = [e for e in fresh if e["grade"] in STRONG]
    weak = [e for e in fresh if e["grade"] == "WEAK_SIGNAL"]
    weak_sources = {e.get("source_type") for e in weak}
    if strong:
        best = max(strong, key=lambda e: (EVIDENCE_POINTS[e["grade"]], _date(e) or ""))
        counted = strong
        r.factors["evidence"] = {"value": EVIDENCE_POINTS[best["grade"]], "why": _proof_text(best)}
    elif len(weak_sources) >= 2:
        counted = weak
        r.factors["evidence"] = {"value": 1, "why": f"2 WEAK proofs from different sources "
                                                     f"({', '.join(sorted(SOURCE_WORDS.get(s, s) for s in weak_sources))})"}
    else:
        counted = []
        if _enough(pain):          # enough proof, but not fresh any more
            r.fails.append("problem proof is too old (older than the decay days in config/problems.yaml)")
        else:
            r.fails.append("no checked problem proof (need 1 STRONG_SIGNAL / CONFIRMED_FACT, "
                           "or 2 WEAK_SIGNAL from different source types)")
    dates = [d for d in (_date(e) for e in counted) if d]
    r.newest_proof = max(dates) if dates else None

    # 2-3. fit and value (set by set-score)
    fit, value = lead.get("fit"), lead.get("value_band")
    scored = fit is not None and value is not None
    if scored:
        r.factors["fit"] = {"value": fit, "why": info.get("fit_why", "")}
        r.factors["value"] = {"value": value, "why": info.get("value_why", "")}
        if fit < MIN_FIT:
            r.fails.append(f"fit {fit} is below {MIN_FIT}: {info.get('fit_why', '')}".rstrip(": "))
        min_value = int((pattern or {}).get("min_value_band") or 1)
        if value < min_value:
            r.fails.append(f"value {value} is below {min_value} (min_value_band): {info.get('value_why', '')}".rstrip(": "))

    # 4. owner role
    owner = owner_of(desk, lead)
    if owner is None:
        r.fails.append("owner role unknown (no contact person with a title)")

    # 5. offer proof
    decided, has_proof = offer_proof(offer_cfg, (pattern or {}).get("offer_id"))
    if not has_proof:
        if decided:
            r.fails.append("offer has no proof (config/offer.yaml: add a case study or demo)")
        else:
            r.warnings.append("no offer proof yet (config/offer.yaml) - needed before outreach")

    # 6. block list
    if lead.get("company_domain") and desk.is_blocked(lead["company_domain"]):
        r.fails.append(f"{lead['company_domain']} is on the block list")
    for p in desk.store.people_for(lead["company_id"]):
        if p.get("email") and desk.is_blocked(p["email"]):
            r.fails.append(f"contact email {p['email']} is on the block list")

    # 7. exclusions
    for d in info.get("disqualifiers") or []:
        r.fails.append(f"disqualifier: {d}")
    if lead.get("channel") == "email" and owner is not None:
        email = (owner.get("email") or "").lower()
        if email and email.rsplit("@", 1)[-1] in PERSONAL_EMAIL_DOMAINS:
            r.fails.append(f"personal email ({email}) for cold email is excluded (config/policy.yaml)")
        if not email:
            r.warnings.append("no published email for the contact yet - find it by hand before Stage 5")

    # urgency (why now)
    why_now = [e for e in evidence if e.get("topic") == "why_now" and e["grade"] not in ("UNKNOWN",)
               and _fresh(desk, lead, e, today)]
    if any(e["grade"] in STRONG for e in why_now):
        e = next(e for e in why_now if e["grade"] in STRONG)
        r.factors["urgency"] = {"value": 2, "why": _short(e["claim"], 60)}
    elif why_now or (lead.get("why_now") or "").strip():
        text = why_now[0]["claim"] if why_now else lead["why_now"]
        r.factors["urgency"] = {"value": 1, "why": f"{_short(text, 50)} (not strongly proven)"}
    else:
        r.factors["urgency"] = {"value": 0, "why": "no why-now found"}

    # outcome
    if r.fails:
        r.outcome = "rejected"
    elif not scored:
        r.outcome = "waiting"
        r.warnings.append("waiting for fit / value score (/cards)")
    else:
        r.outcome = "qualified"
        f = r.factors
        r.priority = f["evidence"]["value"] * f["fit"]["value"] * f["urgency"]["value"] * f["value"]["value"]
        r.why = (f"{f['evidence']['why']} x fit {f['fit']['value']} x urgency {f['urgency']['value']} "
                 f"({f['urgency']['why']}) x value {f['value']['value']} = {r.priority}")
    return r


def _short(text: str | None, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def sort_key(r: Result):
    """Best first: high priority, then newest proof, then oldest lead id."""
    newest = date.fromisoformat(r.newest_proof).toordinal() if r.newest_proof else 0
    return (-(r.priority or 0), -newest, r.lead_id)


def apply(desk: db.Desk, r: Result) -> None:
    """Saves the result and moves the lead (verified -> qualified / rejected, qualified -> rejected)."""
    lead = desk.get(r.lead_id)
    info = dict(lead.get("rank_info") or {})
    info.update({"factors": r.factors, "fails": r.fails, "warnings": r.warnings, "why": r.why,
                 "newest_proof": r.newest_proof, "ranked_at": desk.now().isoformat(timespec="seconds")})
    fields = {"rank_info": info, "priority": r.priority}
    if "urgency" in r.factors:
        fields["urgency"] = r.factors["urgency"]["value"]
    desk.store.update_lead(r.lead_id, fields)
    desk.store.add_event("ranked", ACTOR, r.lead_id,
                         {"outcome": r.outcome, "priority": r.priority, "fails": r.fails})
    if r.outcome == "qualified" and lead["status"] == "verified":
        desk.move(r.lead_id, "qualified", f"passed all gates, priority {r.priority}", actor=ACTOR)
    elif r.outcome == "rejected":
        desk.move(r.lead_id, "rejected", "rank: " + "; ".join(r.fails), actor=ACTOR)


def run(desk: db.Desk, lead_id: int | None = None, dry_run: bool = False) -> list[Result]:
    offer_cfg = db.load_yaml("offer", desk.config_dir)
    if lead_id is not None:
        lead = desk.get(lead_id)
        if lead["status"] not in db.SCORE_STATES:
            raise db.InvalidMove(f"lead #{lead_id} is '{lead['status']}'. Ranking needs: verified or qualified.")
        leads = [lead]
    else:
        leads = desk.store.list_leads("verified") + desk.store.list_leads("qualified")
    results = [evaluate(desk, lead, offer_cfg) for lead in leads]
    if not dry_run:
        for r in results:
            apply(desk, r)
    return sorted(results, key=sort_key)


def need_score(desk: db.Desk) -> list[dict]:
    return [l for l in desk.store.list_leads("verified") if l.get("fit") is None or l.get("value_band") is None]


# ---------- command line ----------
def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Rank verified leads: hard gates + priority.")
    ap.add_argument("--id", type=int, help="only this lead")
    ap.add_argument("--dry-run", action="store_true", help="show the result, change nothing")
    ap.add_argument("--need-score", action="store_true", help="list verified leads without fit / value")
    ap.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    ap.add_argument("--db", type=Path, default=None)
    args = ap.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store)
        if args.need_score:
            leads = need_score(desk)
            if not leads:
                print("No verified lead needs a score.")
            for l in leads:
                print(f"#{l['id']}  {l.get('company_name')}  pattern={l.get('pattern_id') or '?'}  channel={l['channel']}")
            return 0
        results = run(desk, args.id, args.dry_run)
        if not results:
            print("No verified or qualified leads to rank.")
            return 0
        print("DRY RUN - nothing saved.\n" if args.dry_run else "")
        for r in results:
            if r.outcome == "qualified":
                print(f"QUALIFIED  #{r.lead_id} {r.company}  priority {r.priority}\n    {r.why}")
            elif r.outcome == "rejected":
                print(f"REJECTED   #{r.lead_id} {r.company}")
                for f in r.fails:
                    print(f"    - {f}")
            else:
                print(f"WAITING    #{r.lead_id} {r.company}  (needs fit / value score: run /cards)")
            for w in r.warnings:
                if not w.startswith("waiting"):
                    print(f"    warning: {w}")
        counts = {o: sum(r.outcome == o for r in results) for o in ("qualified", "rejected", "waiting")}
        print(f"\n{counts['qualified']} qualified, {counts['rejected']} rejected, {counts['waiting']} waiting.")
        return 0
    except (db.InvalidMove, db.StatusConflict) as exc:
        print(f"REFUSED: {exc}")
        return 1
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
