#!/usr/bin/env python3
"""Opportunity cards (Stage 4): one page per qualified lead, best first.

Usage:
    python scripts/cards.py                # writes cards/<id>.md and cards/index.html
    python scripts/cards.py --out DIR      # another folder

Run scripts/rank.py first: cards use the priority and reasons it saved.
cards/ is git-ignored (real prospect data). Old card files are never deleted; index.html lists only
the leads that are qualified now.
All page text is DATA: it is HTML-escaped, and only http/https links are made clickable.
Also takes --backend supabase|sqlite and --db PATH (like desk.py).
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import date
from pathlib import Path

import db

DEFAULT_OUT = db.ROOT / "cards"
GRADE_ORDER = {"CONFIRMED_FACT": 0, "STRONG_SIGNAL": 1, "WEAK_SIGNAL": 2, "INFERENCE": 3, "UNKNOWN": 4}
FACTOR_NAMES = (("evidence", "Evidence"), ("fit", "Fit"), ("urgency", "Urgency"), ("value", "Value"))


# ---------- collect ----------
def build_card(desk: db.Desk, lead: dict) -> dict:
    info = lead.get("rank_info") or {}
    pattern = next((p for p in desk.problems.get("patterns") or [] if p.get("id") == lead.get("pattern_id")), {})
    evidence = [e for e in desk.store.evidence_for(lead["id"]) if e.get("verified")]
    evidence.sort(key=lambda e: (GRADE_ORDER.get(e["grade"], 9), -(_ordinal(e.get("observed_at")))))
    people = [p for p in desk.store.people_for(lead["company_id"]) if (p.get("title") or "").strip()]
    people.sort(key=lambda p: p["id"] != lead.get("owner_person_id"))
    return {
        "id": lead["id"], "company": lead.get("company_name") or "?", "domain": lead.get("company_domain"),
        "source_url": lead.get("source_url"), "channel": lead.get("channel"), "note": lead.get("note") or "",
        "pattern_id": lead.get("pattern_id"), "problem": pattern.get("description", ""),
        "priority": lead.get("priority"), "why": info.get("why", ""), "factors": info.get("factors") or {},
        "warnings": [w for w in info.get("warnings") or [] if not w.startswith("waiting")],
        "newest_proof": info.get("newest_proof"), "why_now": (lead.get("why_now") or "").strip(),
        "pain": [e for e in evidence if e.get("topic") == "pain" and e["grade"] != "UNKNOWN"],
        "why_now_proof": [e for e in evidence if e.get("topic") == "why_now" and e["grade"] != "UNKNOWN"],
        "other": [e for e in evidence if e.get("topic") not in ("pain", "why_now") and e["grade"] != "UNKNOWN"],
        "people": people, "unknowns": list(lead.get("unknowns") or []),
    }


def _ordinal(value) -> int:
    try:
        return date.fromisoformat(str(value)[:10]).toordinal() if value else 0
    except ValueError:
        return 0


def collect(desk: db.Desk) -> list[dict]:
    cards = [build_card(desk, l) for l in desk.store.list_leads("qualified")]
    cards.sort(key=lambda c: (-(c["priority"] or 0), -_ordinal(c["newest_proof"]), c["id"]))
    return cards


def is_web_link(url: str | None) -> bool:
    return bool(url) and str(url).strip().lower().startswith(("http://", "https://"))


def is_linkedin(url: str | None) -> bool:
    return "linkedin.com" in (url or "").lower()


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


# ---------- markdown ----------
def card_markdown(c: dict) -> str:
    out = [f"# #{c['id']} {_one_line(c['company'])}", ""]
    out.append(f"**Priority {c['priority']}** = {_one_line(c['why'])}")
    out.append("")
    for key, name in FACTOR_NAMES:
        f = c["factors"].get(key) or {}
        out.append(f"- {name}: {f.get('value', '?')} - {_one_line(f.get('why'))}")
    out += ["", f"- Website: {c['domain'] or 'unknown'}", f"- Found at: {c['source_url']}",
            f"- Channel: {c['channel']}", f"- Problem pattern: {c['pattern_id']} - {_one_line(c['problem'])}"]
    if c["note"]:
        out.append(f"- Note: {_one_line(c['note'])}")
    for w in c["warnings"]:
        out.append(f"- WARNING: {_one_line(w)}")
    out += ["", "## Problem proof"]
    out += _md_evidence(c["pain"]) or ["- (none)"]
    out += ["", "## Why now", f"- {_one_line(c['why_now']) or 'UNKNOWN'}"]
    out += _md_evidence(c["why_now_proof"])
    out += ["", "## Who to contact"]
    for p in c["people"]:
        email = f"{p['email']} ({p.get('email_status')})" if p.get("email") else "no email yet"
        line = f"- {_one_line(p.get('full_name')) or '(name unknown)'}, {_one_line(p.get('title'))} - {email}"
        if p.get("profile_url"):
            line += f" - profile: {p['profile_url']}" + (" (look up by hand)" if is_linkedin(p["profile_url"]) else "")
        out.append(line)
    if not c["people"]:
        out.append("- (none)")
    if c["other"]:
        out += ["", "## Other facts"] + _md_evidence(c["other"])
    out += ["", "## Unknowns"] + ([f"- {_one_line(u)}" for u in c["unknowns"]] or ["- (none listed)"])
    return "\n".join(out) + "\n"


def _md_evidence(items: list[dict]) -> list[str]:
    lines = []
    for e in items:
        lines.append(f"- E{e['id']} [{e['grade']}] {_one_line(e['claim'])}")
        if e.get("quote"):
            lines.append(f"  > \"{_one_line(e['quote'])}\"")
        lines.append(f"  {e['url']} - {e.get('source_type') or '?'} - {e.get('observed_at') or 'no date'}")
    return lines


# ---------- html ----------
def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def link(url: str | None, label: str | None = None) -> str:
    """A clickable link only for http/https. Anything else is shown as plain text."""
    shown = esc(label if label is not None else url)
    if not is_web_link(url):
        return shown
    return f'<a href="{esc(url.strip())}" target="_blank" rel="noopener noreferrer">{shown}</a>'


def _html_evidence(items: list[dict]) -> str:
    rows = []
    for e in items:
        quote = f'<blockquote>"{esc(_one_line(e["quote"]))}"</blockquote>' if e.get("quote") else ""
        grade = esc(e["grade"])
        rows.append(
            f'<li><span class="grade g-{grade.lower()}">{grade}</span> {esc(_one_line(e["claim"]))}{quote}'
            f'<div class="meta">E{e["id"]} · {esc(e.get("source_type") or "?")} · '
            f'{esc(e.get("observed_at") or "no date")} · {link(e["url"], "open proof")}</div></li>')
    return "<ul class='ev'>" + "".join(rows) + "</ul>" if rows else "<p class='muted'>none</p>"


def card_html(c: dict, rank: int) -> str:
    factors = "".join(
        f"<div class='factor'><b>{esc((c['factors'].get(k) or {}).get('value', '?'))}</b>"
        f"<span>{name}</span><small>{esc(_one_line((c['factors'].get(k) or {}).get('why')))}</small></div>"
        for k, name in FACTOR_NAMES)
    warnings = "".join(f"<p class='warn'>⚠ {esc(w)}</p>" for w in c["warnings"])
    people = []
    for p in c["people"]:
        email = esc(f"{p['email']} ({p.get('email_status')})") if p.get("email") else "no email yet"
        prof = ""
        if p.get("profile_url"):
            prof = " · " + link(p["profile_url"], "profile") + (" <em>(look up by hand)</em>"
                                                               if is_linkedin(p["profile_url"]) else "")
        people.append(f"<li><b>{esc(_one_line(p.get('full_name')) or '(name unknown)')}</b>, "
                      f"{esc(_one_line(p.get('title')))} · {email}{prof}</li>")
    unknowns = "".join(f"<li>{esc(_one_line(u))}</li>" for u in c["unknowns"]) or "<li class='muted'>none listed</li>"
    other = (f"<h3>Other facts</h3>{_html_evidence(c['other'])}" if c["other"] else "")
    return f"""
<article class="card" id="lead-{c['id']}">
  <header>
    <div class="rank">{rank}</div>
    <div class="title">
      <h2>{esc(_one_line(c['company']))} <small>#{c['id']}</small></h2>
      <p class="muted">{esc(c['channel'])} · {link(c['source_url'], 'found here')} · website: {esc(c['domain'] or 'unknown')}</p>
    </div>
    <div class="prio"><b>{esc(c['priority'])}</b><span>priority</span></div>
  </header>
  <p class="problem"><b>Problem:</b> {esc(_one_line(c['problem']))} <span class="muted">({esc(c['pattern_id'])})</span></p>
  <div class="factors">{factors}</div>
  <p class="why muted">{esc(_one_line(c['why']))}</p>
  {warnings}
  <h3>Problem proof</h3>{_html_evidence(c['pain'])}
  <h3>Why now</h3><p>{esc(_one_line(c['why_now']) or 'UNKNOWN')}</p>{_html_evidence(c['why_now_proof']) if c['why_now_proof'] else ''}
  <h3>Who to contact</h3><ul>{''.join(people) or "<li class='muted'>none</li>"}</ul>
  {other}
  <h3>Unknowns</h3><ul>{unknowns}</ul>
</article>"""


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--text:#1d1d1b;--muted:#6b6a66;--line:#e3e1db;--accent:#2f5d50;
--warn-bg:#fff4dc;--warn:#7a5200;--quote:#f1efe9;--strong:#2f5d50;--weak:#8a6d1f;--conf:#1f4f8a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--card:#1f1f1d;--text:#ecebe6;
--muted:#a3a29c;--line:#34332f;--accent:#7fc4ae;--warn-bg:#3a2e12;--warn:#f2cf7a;--quote:#2a2926;--strong:#7fc4ae;
--weak:#e0bf62;--conf:#8ab6f0}}
:root[data-theme="dark"]{--bg:#161615;--card:#1f1f1d;--text:#ecebe6;--muted:#a3a29c;--line:#34332f;--accent:#7fc4ae;
--warn-bg:#3a2e12;--warn:#f2cf7a;--quote:#2a2926;--strong:#7fc4ae;--weak:#e0bf62;--conf:#8ab6f0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{margin:0 0 4px;font-size:24px}
a{color:var(--accent)}.muted{color:var(--muted)}small{color:var(--muted);font-weight:400}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:18px 0}
.card header{display:flex;gap:12px;align-items:flex-start}.title{flex:1;min-width:0}
.title h2{margin:0;font-size:19px;overflow-wrap:anywhere}.title p{margin:2px 0 0;font-size:13px}
.rank{width:32px;height:32px;border-radius:50%;background:var(--accent);color:var(--card);display:grid;
place-items:center;font-weight:700;flex:none}.prio{text-align:center;flex:none}.prio b{display:block;font-size:24px;
line-height:1}.prio span{font-size:11px;color:var(--muted)}
.factors{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0 4px}
.factor{border:1px solid var(--line);border-radius:8px;padding:8px}.factor b{font-size:18px;margin-right:6px}
.factor small{display:block;font-size:12px;overflow-wrap:anywhere}
@media (max-width:600px){.factors{grid-template-columns:repeat(2,1fr)}}
.why{font-size:13px}.warn{background:var(--warn-bg);color:var(--warn);padding:6px 10px;border-radius:6px;font-size:13px}
h3{font-size:14px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
ul{margin:0;padding-left:18px}ul.ev{list-style:none;padding:0}ul.ev li{margin:0 0 10px}
blockquote{margin:4px 0;padding:6px 10px;background:var(--quote);border-left:3px solid var(--accent);
border-radius:4px;overflow-wrap:anywhere}.meta{font-size:12px;color:var(--muted)}
.grade{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;border:1px solid currentColor}
.g-confirmed_fact{color:var(--conf)}.g-strong_signal{color:var(--strong)}.g-weak_signal,.g-inference{color:var(--weak)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center}
"""


def index_html(cards: list[dict], generated: str) -> str:
    body = "".join(card_html(c, i) for i, c in enumerate(cards, 1)) or (
        "<div class='empty'><p>No qualified leads yet.</p>"
        "<p class='muted'>Run /research on new leads, then /cards.</p></div>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Opportunity Cards</title>
<style>{CSS}</style></head>
<body><main>
<h1>Opportunity Cards</h1>
<p class="muted">{len(cards)} qualified lead(s), best first · made {esc(generated)} (Asia/Karachi) ·
private: real prospect data, do not share or commit.</p>
{body}
</main></body></html>
"""


def write_cards(desk: db.Desk, out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = collect(desk)
    for c in cards:
        (out_dir / f"{c['id']}.md").write_text(card_markdown(c), encoding="utf-8")
    generated = desk.now().astimezone(desk.tz).strftime("%Y-%m-%d %H:%M")
    (out_dir / "index.html").write_text(index_html(cards, generated), encoding="utf-8")
    return cards


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Write opportunity cards for qualified leads.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder (default: cards/)")
    ap.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    ap.add_argument("--db", type=Path, default=None)
    args = ap.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        cards = write_cards(db.Desk(store), args.out)
        for i, c in enumerate(cards, 1):
            print(f"{i}. #{c['id']} {c['company']}  priority {c['priority']}")
        print(f"\n{len(cards)} card(s). Open: {Path(args.out) / 'index.html'}")
        return 0
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
