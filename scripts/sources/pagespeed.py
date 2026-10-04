#!/usr/bin/env python3
"""PageSpeed source (Stage 7): Google PageSpeed Insights (mobile) for a lead's website -> website evidence.

The result is saved as a snapshot ("Performance score: 23/100", LCP...). If the score is below
`pagespeed.bad_score` (config/sources.yaml, default 50) and a lead id is given, it is saved as evidence:
source website, topic pain, WEAK_SIGNAL. A slow site is a Tier-2 signal: never enough alone.

Key: PAGESPEED_API_KEY in .env (free, optional; without it Google allows only a few calls).

Usage:
    python scripts/sources/pagespeed.py LEAD_ID          (uses the lead's company domain)
    python scripts/sources/pagespeed.py --url https://example.com [--lead LEAD_ID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import db  # noqa: E402
from sources import common  # noqa: E402

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
REPORT = "https://pagespeed.web.dev/analysis?url={}"
TIMEOUT = 120.0
DEFAULT_BAD_SCORE = 50
AUDITS = (("largest-contentful-paint", "Largest Contentful Paint"), ("first-contentful-paint", "First Contentful Paint"),
          ("total-blocking-time", "Total Blocking Time"), ("cumulative-layout-shift", "Cumulative Layout Shift"),
          ("speed-index", "Speed Index"))


def call_api(url: str, key: str | None, transport: httpx.BaseTransport | None = None) -> dict:
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if key:
        params["key"] = key
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
            resp = client.get(API, params=params)
    except httpx.HTTPError as exc:
        raise db.DeskError(f"cannot reach Google PageSpeed ({type(exc).__name__})") from exc
    if resp.status_code == 429:
        raise db.DeskError("PageSpeed quota used up. Add a free PAGESPEED_API_KEY to .env (Google Cloud console), "
                           "or try tomorrow.")
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        msg = msg.replace(key, "***") if key else msg      # never print the key
        raise db.DeskError(f"PageSpeed error HTTP {resp.status_code}: {msg[:200]}")
    return resp.json()


def summarize(data: dict) -> dict:
    lh = data.get("lighthouseResult") or {}
    score = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    audits = lh.get("audits") or {}
    return {"score": round(score * 100) if isinstance(score, (int, float)) else None,
            "final_url": lh.get("finalDisplayedUrl") or lh.get("finalUrl") or data.get("id"),
            "metrics": {label: (audits.get(key) or {}).get("displayValue") for key, label in AUDITS
                        if (audits.get(key) or {}).get("displayValue")}}


def report_text(site: str, summary: dict, today) -> str:
    lines = [f"Google PageSpeed Insights (mobile) for {site} on {today.isoformat()}",
             f"Performance score: {summary['score'] if summary['score'] is not None else 'unknown'}/100"]
    lines += [f"{label}: {value}" for label, value in summary["metrics"].items()]
    return "\n".join(lines)


def run(desk: db.Desk, *, url: str | None = None, lead_id: int | None = None,
        transport: httpx.BaseTransport | None = None, out=print) -> dict:
    cfg = common.sources_config(desk).get("pagespeed") or {}
    if not cfg.get("enabled", True):
        raise db.DeskError("pagespeed is disabled in config/sources.yaml")
    bad = int(cfg.get("bad_score") or DEFAULT_BAD_SCORE)
    lead = desk.get(lead_id) if lead_id else None
    if not url:
        if not lead or not lead.get("company_domain"):
            raise db.DeskError("this lead has no company website domain. Use --url https://THEIR-SITE")
        url = f"https://{lead['company_domain']}"
    common.refuse_linkedin(url)
    if lead and lead["status"] not in db.RESEARCH_STATES:
        raise db.InvalidMove(f"lead #{lead_id} is '{lead['status']}'. Evidence needs status: "
                             f"{', '.join(db.RESEARCH_STATES)}.")

    summary = summarize(call_api(url, db.load_env().get("PAGESPEED_API_KEY") or None, transport))
    text = report_text(url, summary, desk.today())
    report_url = REPORT.format(quote(url, safe=""))
    sha = desk.save_snapshot(report_url, text, 200, f"PageSpeed {url}")[0]
    out(text)
    out(f"snapshot {sha}  ({report_url})")
    result = {"score": summary["score"], "snapshot": sha, "evidence": None, "report_url": report_url}
    if summary["score"] is None:
        out("No score in the answer. Nothing saved as evidence.")
    elif summary["score"] >= bad:
        out(f"Score {summary['score']} >= {bad}: the site is fine. No evidence saved.")
    elif lead:
        eid, grade, warnings = desk.add_evidence(
            lead_id, claim=f"Their website is slow on mobile: PageSpeed score {summary['score']}/100",
            url=report_url, quote=f"Performance score: {summary['score']}/100", grade="WEAK_SIGNAL",
            source_type="website", topic="pain", observed_at=desk.today().isoformat(), snapshot=sha,
            actor="role:pagespeed")
        result["evidence"] = eid
        out(f"Saved evidence E{eid} website / pain / {grade} (Tier 2: needs a second signal)")
        for w in warnings:
            out(f"  WARNING: {w}")
    else:
        out(f"Score {summary['score']} < {bad}: slow site. Give a lead id (--lead ID) to save it as evidence.")
    return result


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    common.utf8_stdout()
    parser = argparse.ArgumentParser(description="Google PageSpeed check for a lead's website.")
    parser.add_argument("lead_id", nargs="?", type=int)
    parser.add_argument("--lead", type=int, dest="lead_opt")
    parser.add_argument("--url")
    parser.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    lead_id = args.lead_id or args.lead_opt
    if not lead_id and not args.url:
        parser.error("give a LEAD_ID or --url")
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        run(db.Desk(store), url=args.url, lead_id=lead_id, transport=transport)
        return 0
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
