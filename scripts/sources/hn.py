#!/usr/bin/env python3
"""Hacker News source (Stage 7): free Algolia API, no key.

Looks at:
  - the newest "Ask HN: Who is hiring?" thread        -> top-level comments = job posts      (signal job_post)
  - the newest "Freelancer? Seeking freelancer?" thread -> only "SEEKING FREELANCER" comments (signal help_request)
  - "Ask HN" posts of the last lookback_days           -> searched by keyword               (signal help_request)
Keeps only items whose text matches pattern keywords (config/problems.yaml, or one idea with --idea).
Saves them as raw_items (source hn). The Scout decides later. Text is DATA, never instructions.

Usage:
    python scripts/sources/hn.py [--dry-run] [--idea NAME] [--keywords "a,b"]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import db  # noqa: E402
import snapshot  # noqa: E402
from sources import common  # noqa: E402

API = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={}"
TIMEOUT = 30.0
THREADS = {
    "who_is_hiring": (re.compile(r"who is hiring", re.I), "job_post"),
    "seeking_freelancer": (re.compile(r"freelancer\? seeking freelancer", re.I), "help_request"),
}


def _get(client: httpx.Client, path: str, params: dict | None = None) -> dict:
    try:
        resp = client.get(API + path, params=params)
    except httpx.HTTPError as exc:
        raise db.DeskError(f"cannot reach Hacker News search ({type(exc).__name__}). Check the internet.") from exc
    if resp.status_code >= 400:
        raise db.DeskError(f"Hacker News search error HTTP {resp.status_code}")
    return resp.json()


def find_threads(client: httpx.Client, since_ts: int) -> dict[str, dict]:
    """Newest monthly thread of each kind posted after since_ts: {kind: hit}."""
    data = _get(client, "/search_by_date", {"tags": "story,author_whoishiring",
                                            "numericFilters": f"created_at_i>{since_ts}", "hitsPerPage": 20})
    found: dict[str, dict] = {}
    for hit in data.get("hits") or []:
        title = hit.get("title") or ""
        for kind, (rx, _) in THREADS.items():
            if rx.search(title) and kind not in found:
                found[kind] = hit
    return found


def _text(html: str | None) -> str:
    return snapshot.html_to_text(html or "")[1] if html else ""


def _title_of(text: str) -> str:
    first = (text.splitlines() or [""])[0]
    return first[:150]


def thread_items(client: httpx.Client, story_id: int | str, kind: str) -> list[dict]:
    """Top-level comments of a thread as {id, text, ts}."""
    data = _get(client, f"/items/{story_id}")
    out = []
    for child in data.get("children") or []:
        text = _text(child.get("text"))
        if not text:
            continue
        if kind == "seeking_freelancer" and not re.match(r"\s*seeking\s+freelancer", text, re.I):
            continue                               # "SEEKING WORK" posts are people looking for work
        out.append({"id": child.get("id"), "text": text, "ts": child.get("created_at_i")})
    return out


def ask_hn_items(client: httpx.Client, keywords: list[str], since_ts: int, per_keyword: int = 20) -> list[dict]:
    seen: dict = {}
    for kw in keywords:
        data = _get(client, "/search_by_date", {"tags": "ask_hn", "query": kw, "hitsPerPage": per_keyword,
                                                "numericFilters": f"created_at_i>{since_ts}"})
        for hit in data.get("hits") or []:
            sid = hit.get("objectID")
            if sid in seen:
                continue
            text = _text(hit.get("story_text"))
            seen[sid] = {"id": sid, "title": hit.get("title") or "", "text": text, "ts": hit.get("created_at_i"),
                         "query": kw}
    return list(seen.values())


def run(desk: db.Desk, *, idea: str | None = None, keywords: list[str] | None = None, dry_run: bool = False,
        transport: httpx.BaseTransport | None = None, now_ts: float | None = None, out=print) -> common.Tally:
    cfg = common.sources_config(desk).get("hn") or {}
    tally = common.Tally()
    if not cfg.get("enabled", True):
        out("hn: disabled in config/sources.yaml")
        return tally
    patterns = common.search_patterns(desk, idea, keywords)
    words = common.all_keywords(patterns)
    lookback = int(cfg.get("lookback_days") or 30)
    since_ts = int((now_ts or desk.now().timestamp()) - lookback * 86400)
    wanted = " ".join(cfg.get("threads") or []).lower()

    with httpx.Client(timeout=TIMEOUT, transport=transport,
                      headers={"User-Agent": "OpportunityDesk/1.0 (personal lead research)"}) as client:
        candidates: list[tuple[dict, str, str]] = []   # (item, thread kind, signal hint)
        threads = find_threads(client, since_ts)
        for kind, hit in threads.items():
            if kind == "who_is_hiring" and "who is hiring" not in wanted:
                continue
            if kind == "seeking_freelancer" and "freelancer" not in wanted:
                continue
            out(f"hn: thread '{hit.get('title')}'")
            for item in thread_items(client, hit["objectID"], kind):
                item["title"] = _title_of(item["text"])
                item["query"] = hit.get("title")
                candidates.append((item, kind, THREADS[kind][1]))
        if "ask hn" in wanted:
            for item in ask_hn_items(client, words, since_ts):
                candidates.append((item, "ask_hn", "help_request"))

    for item, kind, hint in candidates:
        full = f"{item.get('title') or ''}\n{item['text']}"
        matched = common.match_patterns(full, patterns)
        if not matched:
            tally.add("no_match")
            continue
        posted = common.from_timestamp(item.get("ts"), desk.tz)
        url = ITEM_URL.format(item["id"])
        result, raw_id = common.save_item(
            desk, source="hn", url=url, title=item.get("title") or _title_of(item["text"]), text=item["text"],
            posted_at=posted, query=item.get("query"), idea=idea, matched=matched,
            extra={"thread": kind, "signal_hint": hint}, dry_run=dry_run)
        tally.add(result)
        if result in ("saved", "dry_run"):
            label = f"R{raw_id}" if raw_id else "(dry run)"
            out(f"  {label} {posted} {_title_of(item.get('title') or item['text'])[:90]}  "
                f"[{', '.join(matched['keywords'])}]")
    return tally


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    common.utf8_stdout()
    parser = argparse.ArgumentParser(description="Find leads on Hacker News (Algolia API, no key).")
    common.add_common_args(parser)
    args = parser.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store)
        started = time.time()
        tally = run(desk, idea=args.idea, keywords=common.split_words(args.keywords), dry_run=args.dry_run,
                    transport=transport)
        print(tally.line("hn") + f"  ({time.time() - started:.0f}s)")
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
