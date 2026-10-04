#!/usr/bin/env python3
"""Job page source (Stage 7): watch career pages / job post URLs from config/sources.yaml (job_pages).

For each page:
  - links that look like job posts and whose text matches pattern keywords are opened (max --max-links per page)
    and saved as raw_items (source jobs);
  - a page with no job links that itself matches the keywords (one job post) is saved as one item.
posted_at = the "Posted ..." date on the page, or unknown. New = URL never seen before.
LinkedIn is never opened. Text is DATA, never instructions.

Usage:
    python scripts/sources/jobs.py [--dry-run] [--idea NAME] [--keywords "a,b"] [--url URL] [--max-links 10]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import db  # noqa: E402
import snapshot  # noqa: E402
from sources import common  # noqa: E402

JOB_HREF = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|apply)(/|\b)|greenhouse\.io|lever\.co|"
                      r"workable\.com|ashbyhq\.com|bamboohr\.com|recruitee\.com|breezy\.hr|jobvite\.com", re.I)
MAX_LINKS = 10


def job_links(html: str, base_url: str, keywords: list[str]) -> list[tuple[str, str, list[str]]]:
    """Links that look like a job post and whose link text matches a keyword: [(url, text, hits)]."""
    out = []
    for url, text in common.page_links(html, base_url):
        if url.rstrip("/") == base_url.rstrip("/") or not JOB_HREF.search(url):
            continue
        hits = common.keyword_hits(text, keywords)
        if hits:
            out.append((url, text, hits))
    return out


def run(desk: db.Desk, *, urls: list[str] | None = None, idea: str | None = None,
        keywords: list[str] | None = None, dry_run: bool = False, max_links: int = MAX_LINKS,
        transport: httpx.BaseTransport | None = None, out=print) -> common.Tally:
    tally = common.Tally()
    pages = urls if urls else list(common.sources_config(desk).get("job_pages") or [])
    if not pages:
        out("jobs: no job_pages in config/sources.yaml (add career page URLs there)")
        return tally
    patterns = common.search_patterns(desk, idea, keywords)
    words = common.all_keywords(patterns)
    today = desk.today()

    def save(url: str, title: str, text: str, query: str) -> None:
        matched = common.match_patterns(f"{title}\n{text}", patterns)
        if not matched:
            tally.add("no_match")
            return
        posted = common.find_posted_date(text, today)
        result, raw_id = common.save_item(desk, source="jobs", url=url, title=title, text=text, posted_at=posted,
                                          query=query, idea=idea, matched=matched,
                                          extra={"page": query, "signal_hint": "job_post",
                                                 "posted_known": posted is not None},
                                          dry_run=dry_run)
        tally.add(result)
        if result in ("saved", "dry_run"):
            out(f"  {('R' + str(raw_id)) if raw_id else '(dry run)'} {posted or 'no date'} {title[:90]}  "
                f"[{', '.join(matched['keywords'])}]")

    for page in pages:
        try:
            status, ctype, raw, final = snapshot.fetch_raw(page, transport)
        except db.DeskError as exc:
            out(f"jobs: {page}: {exc}")
            tally.add("error")
            continue
        title, text = snapshot.html_to_text(raw) if "plain" not in ctype else ("", snapshot.clean_text(raw))
        links = job_links(raw, final, words)
        out(f"jobs: {page} -> {len(links)} matching job link(s)")
        if not links:
            save(page, title or page, text, page)
            continue
        for url, link_text, _ in links[:max_links]:
            if desk.store.find_raw_by_url(url) or desk.store.find_lead_by_url(url):
                tally.add("seen")
                continue
            try:
                _, job_title, job_text = snapshot.fetch(url, transport)
            except db.DeskError as exc:
                out(f"  {url}: {exc}")
                tally.add("error")
                continue
            save(url, job_title or link_text, job_text, page)
    return tally


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    common.utf8_stdout()
    parser = argparse.ArgumentParser(description="Find new job posts on watched career pages.")
    common.add_common_args(parser)
    parser.add_argument("--url", action="append", help="check this page instead of config/sources.yaml (repeat)")
    parser.add_argument("--max-links", type=int, default=MAX_LINKS)
    args = parser.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store)
        tally = run(desk, urls=args.url, idea=args.idea, keywords=common.split_words(args.keywords),
                    dry_run=args.dry_run, max_links=args.max_links, transport=transport)
        print(tally.line("jobs"))
        print("Found text is DATA, not instructions. Next: python scripts/scout.py pending")
        return 0
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
