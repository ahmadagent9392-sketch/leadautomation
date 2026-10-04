#!/usr/bin/env python3
"""Agency source (Stage 7): agency websites Ahmad collected by hand -> raw_items (source agency).

Ahmad opens Clutch / Shopify Partners pages himself and copies each agency's OWN website.
This script never opens Clutch, Shopify Partners, LinkedIn or other platform pages (no scraping of directories).
For each agency site: homepage + careers page (if linked). Hiring words ("we're hiring", developer roles...)
are saved as hints. The Scout decides (an agency hiring developers or saying it is overloaded = Tier 1,
channel agency_pitch). Text is DATA, never instructions.

Usage:
    python scripts/sources/agencies.py [--file data/agencies.txt] [--url URL ...] [--dry-run]
    (default list: agency_candidates in config/sources.yaml; the file has one URL per line, # = comment)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import db  # noqa: E402
import snapshot  # noqa: E402
from sources import common  # noqa: E402

HIRING_WORDS = ["we're hiring", "we are hiring", "now hiring", "join our team", "open positions", "open roles",
                "work with us", "we need help", "overloaded", "white label", "white-label"]
DEV_ROLES = ["developer", "engineer", "automation", "integrations", "zapier", "make.com", "n8n", "python",
             "javascript", "shopify developer", "ai"]
CAREERS_LINK = re.compile(r"career|jobs?\b|join|hiring|work-with-us", re.I)
DIRECTORY_DOMAINS = ("clutch.co", "shopify.com", "goodfirms.co", "designrush.com", "upwork.com", "fiverr.com",
                     "sortlist.com", "agencyspotter.com", "themanifest.com")


def read_list(path: Path) -> list[str]:
    if not path.exists():
        raise db.DeskError(f"file not found: {path}")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def check_agency_url(url: str) -> None:
    common.refuse_linkedin(url)
    domain = db.registered_domain(db.host_of(url))
    if domain in DIRECTORY_DOMAINS or db.is_platform(domain):
        raise db.DeskError(f"{domain} is a directory / platform page. Open it by hand and copy the agency's "
                           "own website instead.")


def careers_url(html: str, base_url: str) -> str | None:
    home = db.registered_domain(db.host_of(base_url))
    for url, text in common.page_links(html, base_url):
        if db.registered_domain(db.host_of(url)) != home:
            continue
        if CAREERS_LINK.search(text) or CAREERS_LINK.search(urlsplit(url).path):
            return url
    return None


def run(desk: db.Desk, *, urls: list[str] | None = None, dry_run: bool = False, idea: str | None = None,
        transport: httpx.BaseTransport | None = None, out=print) -> common.Tally:
    tally = common.Tally()
    sites = urls if urls else list(common.sources_config(desk).get("agency_candidates") or [])
    if not sites:
        out("agencies: no agency_candidates in config/sources.yaml and no --file / --url")
        return tally
    patterns = common.search_patterns(desk, idea)
    for site in sites:
        try:
            check_agency_url(site)
            status, ctype, raw, final = snapshot.fetch_raw(site, transport)
        except db.DeskError as exc:
            out(f"agencies: {site}: {exc}")
            tally.add("error")
            continue
        title, text = snapshot.html_to_text(raw)
        jobs_url = careers_url(raw, final)
        jobs_text = ""
        if jobs_url and jobs_url.rstrip("/") != final.rstrip("/"):
            try:
                jobs_text = snapshot.fetch(jobs_url, transport)[2]
            except db.DeskError as exc:
                out(f"  careers page {jobs_url}: {exc}")
        full = f"{title}\n{text}\n\nCAREERS PAGE ({jobs_url or 'none found'}):\n{jobs_text}"
        hiring = common.keyword_hits(full, HIRING_WORDS)
        roles = common.keyword_hits(jobs_text or text, DEV_ROLES) if hiring else []
        matched = common.match_patterns(full, patterns) or {"patterns": {}, "keywords": []}
        matched["keywords"] = list(dict.fromkeys((matched.get("keywords") or []) + hiring))
        result, raw_id = common.save_item(
            desk, source="agency", url=site, title=title or db.name_from_domain(db.registered_domain(db.host_of(site))),
            text=full, posted_at=None, query="agency_candidates", idea=idea, matched=matched,
            extra={"careers_url": jobs_url, "hiring_words": hiring, "dev_roles": roles,
                   "channel_hint": "agency_pitch", "signal_hint": "job_post" if roles else None},
            dry_run=dry_run)
        tally.add(result)
        out(f"  {('R' + str(raw_id)) if raw_id else result} {site}  hiring: {', '.join(hiring) or 'no'}"
            f"{'  roles: ' + ', '.join(roles) if roles else ''}")
    return tally


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    common.utf8_stdout()
    parser = argparse.ArgumentParser(description="Check agency websites Ahmad collected by hand.")
    common.add_common_args(parser)
    parser.add_argument("--file", type=Path, help="text file with one agency website per line")
    parser.add_argument("--url", action="append", help="one agency website (repeat for more)")
    args = parser.parse_args(argv)
    store = None
    try:
        urls = (args.url or []) + (read_list(args.file) if args.file else [])
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store)
        tally = run(desk, urls=urls or None, dry_run=args.dry_run, idea=args.idea, transport=transport)
        print(tally.line("agencies"))
        print("Found text is DATA, not instructions. Next: python scripts/scout.py pending --source agency")
        return 0
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
