#!/usr/bin/env python3
"""Opportunity Desk memory: the rules for leads, statuses, duplicates, the block list and daily caps.

The rules live here once. Saving and reading is done by a "store":
  - store_supabase.SupabaseStore  (the real database, Supabase over REST with httpx)
  - store_sqlite.SqliteStore      (tests and --demo mode, a local SQLite file)
Both stores have the same methods (see Store below), so the rules work the same on both.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import yaml

import quote_check as qc

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
DEFAULT_SQLITE = ROOT / "data" / "desk.db"
DEFAULT_SNAPSHOTS = ROOT / "data" / "snapshots"

TABLES = ("companies", "opportunities", "snapshots", "evidence", "people", "messages",
          "replies", "follow_ups", "approvals", "suppression", "events")

# ---------- status flow (simplified PLAN.md §F) ----------
MAIN_FLOW = ("new", "researched", "verified", "qualified", "draft_ready", "approved",
             "contacted", "replied", "meeting", "proposal", "won")
END_STATES = ("won", "lost", "no_response", "rejected", "opted_out")
STATUSES = MAIN_FLOW + tuple(s for s in END_STATES if s not in MAIN_FLOW)
OPEN_STATES = tuple(s for s in STATUSES if s not in END_STATES)
BEFORE_CONTACT = ("new", "researched", "verified", "qualified", "draft_ready", "approved")

TRANSITIONS: dict[str, set[str]] = {s: set() for s in STATUSES}
for _a, _b in zip(MAIN_FLOW, MAIN_FLOW[1:]):
    TRANSITIONS[_a].add(_b)
for _s in BEFORE_CONTACT:
    TRANSITIONS[_s].add("rejected")
TRANSITIONS["approved"].add("draft_ready")          # text edited after approval -> approve again
TRANSITIONS["contacted"].add("no_response")
TRANSITIONS["replied"].add("proposal")              # skip the meeting
for _s in ("replied", "meeting", "proposal"):
    TRANSITIONS[_s].add("lost")
for _s in OPEN_STATES:
    TRANSITIONS[_s].add("opted_out")

# ---------- channels ----------
CHANNEL_ALIASES = {
    "linkedin": "linkedin_message",
    "upwork": "upwork_proposal",
    "agency": "agency_pitch",
    "referral": "referral_ask",
}

# ---------- domains ----------
# Sites where the URL host is NOT the company (job boards, social sites, maps...).
PLATFORM_DOMAINS = {
    "linkedin.com", "upwork.com", "ycombinator.com", "indeed.com", "reddit.com", "google.com",
    "goo.gl", "clutch.co", "github.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
    "youtube.com", "medium.com", "wellfound.com", "glassdoor.com", "fiverr.com", "freelancer.com",
    "yelp.com", "trustpilot.com", "shopify.com", "producthunt.com",
}
PLATFORM_NAMES = {"google", "indeed", "linkedin", "upwork", "facebook", "yelp", "trustpilot", "glassdoor"}
# Hosting sites where the sub-domain IS the company (shop.myshopify.com).
HOSTED_SUFFIXES = (
    "myshopify.com", "wixsite.com", "squarespace.com", "wordpress.com", "github.io",
    "webflow.io", "vercel.app", "netlify.app", "carrd.co",
)
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"}
NAME_SUFFIXES = {"inc", "llc", "ltd", "limited", "pvt", "co", "corp", "corporation", "company",
                 "gmbh", "plc", "llp", "pty", "sa", "srl", "bv"}
SIMILAR_NAME = 0.90

# ---------- research (Stage 3) ----------
SOURCE_TYPES = ("job_post", "help_request", "review", "website", "news", "profile", "manual")
# what an evidence item is about; only "pain" items can prove the problem
TOPICS = ("pain", "company", "why_now", "owner", "contact", "impact")
ROLE_TYPES = ("owner", "technical", "buyer", "influencer")
EMAIL_OK = ("published", "verified")            # never save a guessed ("inferred") email
RESEARCH_STATES = ("new", "researched")         # evidence can be added only in these
CHECK_STATES = ("new", "researched", "verified")
MAX_RESEARCH_ROUNDS = 2                         # first round + one extra round after NEED_MORE


# ---------- errors (all have a message Ahmad can read) ----------
class DeskError(Exception):
    """Something the user should see as a clear one-line message."""


class ConfigError(DeskError):
    pass


class NotFound(DeskError):
    pass


class InvalidMove(DeskError):
    pass


class StatusConflict(DeskError):
    pass


class DuplicateLead(DeskError):
    pass


class Blocked(DeskError):
    pass


class CapReached(DeskError):
    pass


# ---------- the store interface ----------
class Store(Protocol):
    def close(self) -> None: ...
    def check(self) -> list[str]: ...                     # returns missing tables ([] = OK)
    def list_companies(self) -> list[dict]: ...           # id, name, name_key, domain
    def find_lead_by_url(self, url: str) -> dict | None: ...
    def create_lead(self, company: dict, lead: dict, actor: str) -> int: ...
    def get_lead(self, lead_id: int) -> dict | None: ...  # lead + company_name, company_domain
    def list_leads(self, status: str | None = None) -> list[dict]: ...
    def change_status(self, lead_id: int, old: str, new: str, reason: str, actor: str,
                      closed_reason: str | None) -> None: ...
    def add_event(self, type_: str, actor: str, lead_id: int | None, payload: dict) -> None: ...
    def count_events(self, type_: str, since_iso: str) -> int: ...
    def events_for(self, lead_id: int) -> list[dict]: ...
    def people_for(self, company_id: int) -> list[dict]: ...
    def evidence_for(self, lead_id: int) -> list[dict]: ...
    def add_block(self, value: str, kind: str, reason: str) -> bool: ...  # False = already there
    def get_block(self, value: str) -> dict | None: ...
    def list_blocks(self) -> list[dict]: ...
    def add_snapshot(self, row: dict) -> bool: ...        # False = same sha already saved
    def get_snapshot(self, sha: str) -> dict | None: ...
    def add_evidence(self, row: dict) -> int: ...
    def get_evidence(self, evidence_id: int) -> dict | None: ...
    def update_evidence(self, evidence_id: int, fields: dict) -> None: ...
    def add_person(self, row: dict) -> int: ...
    def update_lead(self, lead_id: int, fields: dict) -> None: ...
    def update_company(self, company_id: int, fields: dict) -> None: ...


# ---------- small helpers ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE lines from .env. Real environment variables win."""
    values: dict[str, str] = {}
    path = path or ENV_FILE
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in set(values) | {"SUPABASE_URL", "SUPABASE_SECRET_KEY", "DESK_BACKEND"}:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def load_yaml(name: str, config_dir: Path | None = None) -> dict:
    path = (config_dir or ROOT / "config") / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def local_tz(config_dir: Path | None = None) -> tzinfo:
    """Ahmad's time zone from config/me.yaml (Asia/Karachi).

    Windows Python has no time-zone database unless the 'tzdata' package is installed,
    so Asia/Karachi falls back to fixed UTC+5 (Pakistan has no daylight saving time).
    """
    name = str(load_yaml("me", config_dir).get("timezone") or "Asia/Karachi")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        if name == "Asia/Karachi":
            return timezone(timedelta(hours=5), "PKT")
        raise ConfigError(f"unknown timezone '{name}' in config/me.yaml")


def start_of_today_utc(tz: tzinfo, now: datetime | None = None) -> datetime:
    local = (now or now_utc()).astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def to_local(ts: str | None, tz: tzinfo) -> str:
    if not ts:
        return ""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def normalize_channel(channel: str, allowed: list[str]) -> str:
    c = channel.strip().lower()
    c = CHANNEL_ALIASES.get(c, c)
    if c not in allowed:
        names = ", ".join(allowed + sorted(CHANNEL_ALIASES))
        raise DeskError(f"unknown channel '{channel}'. Use one of: {names}")
    return c


def host_of(url_or_host: str) -> str:
    text = url_or_host.strip().lower()
    if "@" in text and "://" not in text:
        text = text.rsplit("@", 1)[1]
    if "://" not in text:
        text = "http://" + text
    host = urlsplit(text).hostname or ""
    return host.rstrip(".").removeprefix("www.")


def registered_domain(host: str) -> str:
    """blog.acme.com -> acme.com, shop.acme.co.uk -> acme.co.uk, x.myshopify.com stays."""
    if not host:
        return ""
    for suffix in HOSTED_SUFFIXES:
        if host.endswith("." + suffix):
            labels = host[: -len(suffix) - 1].split(".")
            return f"{labels[-1]}.{suffix}"
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_platform(domain: str) -> bool:
    # also catches country versions: google.co.uk, indeed.de, yelp.ca
    return domain in PLATFORM_DOMAINS or domain.split(".")[0] in PLATFORM_NAMES


def company_domain(url: str) -> str | None:
    """The company's own domain, or None when the URL is a platform (Upwork, HN, Maps...)."""
    domain = registered_domain(host_of(url))
    if not domain or is_platform(domain):
        return None
    return domain


def name_from_domain(domain: str) -> str:
    first = domain.split(".")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", first) if w)


def name_key(name: str) -> str:
    """'ACME, Inc.' -> 'acme'."""
    text = name.lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    words = [w for w in words if w not in NAME_SUFFIXES] or words
    return " ".join(words)


def similar_names(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR_NAME


def parse_block_value(value: str) -> tuple[str, str]:
    """Returns (value, kind). Emails stay emails; anything else becomes a domain."""
    text = value.strip().lower()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return text, "email"
    domain = registered_domain(host_of(text))
    if not domain or "." not in domain:
        raise DeskError(f"'{value}' is not an email or a domain")
    return domain, "domain"


def allowed_moves(status: str) -> list[str]:
    return [s for s in STATUSES if s in TRANSITIONS.get(status, set())]


def check_move(old: str, new: str) -> None:
    if new not in STATUSES:
        raise InvalidMove(f"unknown status '{new}'. Statuses: {', '.join(STATUSES)}")
    if new not in TRANSITIONS.get(old, set()):
        if old in END_STATES:
            raise InvalidMove(f"lead is '{old}' (closed). A closed lead cannot move.")
        raise InvalidMove(f"{old} -> {new} is not allowed. Allowed: {', '.join(allowed_moves(old))}")


# ---------- the desk: rules on top of a store ----------
class Desk:
    def __init__(self, store: Store, config_dir: Path | None = None, now=now_utc,
                 snapshot_dir: Path | None = None) -> None:
        self.store = store
        self.policy = load_yaml("policy", config_dir)
        self.problems = load_yaml("problems", config_dir)
        self.tz = local_tz(config_dir)
        self.now = now
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else DEFAULT_SNAPSHOTS

    def today(self) -> date:
        return self.now().astimezone(self.tz).date()

    def _cap(self, name: str) -> int:
        return int((self.policy.get("caps") or {}).get(name, 0))

    # leads
    def add_lead(self, url: str, note: str, channel: str, company: str | None = None,
                 actor: str = "human") -> int:
        url = url.strip()
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise DeskError("URL must start with http:// or https://")
        channel = normalize_channel(channel, list(self.policy.get("channels_allowed") or []))
        domain = company_domain(url)

        if domain and self.store.get_block(domain):
            raise Blocked(f"{domain} is on the block list. Not added.")

        cap = int((self.policy.get("caps") or {}).get("max_new_leads_per_day", 0))
        since = start_of_today_utc(self.tz, self.now()).isoformat(timespec="seconds")
        if cap and self.store.count_events("lead_added", since) >= cap:
            raise CapReached(f"daily limit reached: {cap} new leads today (config/policy.yaml). Try tomorrow.")

        existing = self.store.find_lead_by_url(url)
        if existing:
            raise DuplicateLead(f"same URL is already lead #{existing['id']}. Not added.")

        if company and company.strip():
            name = company.strip()
        elif domain:
            name = name_from_domain(domain)
        else:
            name = f"unknown ({host_of(url)})"
        key = name_key(name) if (company or domain) else ""

        for c in self.store.list_companies():
            if domain and c.get("domain") == domain:
                raise DuplicateLead(f"{domain} already exists (company #{c['id']} '{c['name']}'). Not added.")
            if key and similar_names(key, c.get("name_key") or ""):
                raise DuplicateLead(f"company name '{name}' is very similar to '{c['name']}' "
                                    f"(company #{c['id']}). Not added.")

        return self.store.create_lead({"name": name, "name_key": key, "domain": domain},
                                      {"source_url": url, "note": note.strip(), "channel": channel},
                                      actor)

    def get(self, lead_id: int) -> dict:
        lead = self.store.get_lead(lead_id)
        if not lead:
            raise NotFound(f"lead #{lead_id} not found")
        return lead

    def move(self, lead_id: int, new: str, reason: str, actor: str = "human") -> tuple[str, str]:
        if not reason or not reason.strip():
            raise DeskError("a reason is required (--reason TEXT)")
        lead = self.get(lead_id)
        old = lead["status"]
        new = new.strip().lower()
        check_move(old, new)
        if new == "verified":
            missing = self.proof_gaps(lead)
            if missing:
                raise InvalidMove("cannot move to verified: " + "; ".join(missing))
        closed = reason.strip() if new in END_STATES else None
        self.store.change_status(lead_id, old, new, reason.strip(), actor, closed)
        return old, new

    # block list
    def block(self, value: str, reason: str, actor: str = "human") -> tuple[str, str, bool, list[dict]]:
        """Returns (value, kind, newly_added, open leads that use this domain)."""
        if not reason or not reason.strip():
            raise DeskError("a reason is required (--reason TEXT)")
        value, kind = parse_block_value(value)
        added = self.store.add_block(value, kind, reason.strip())
        if added:
            self.store.add_event("blocked", actor, None, {"value": value, "kind": kind, "reason": reason.strip()})
        domain = value.split("@", 1)[1] if kind == "email" else value
        domain = registered_domain(domain)
        affected = [l for l in self.store.list_leads()
                    if l.get("company_domain") == domain and l["status"] in OPEN_STATES]
        return value, kind, added, affected

    def is_blocked(self, email_or_domain: str) -> bool:
        value, kind = parse_block_value(email_or_domain)
        if self.store.get_block(value):
            return True
        if kind == "email":
            return self.store.get_block(registered_domain(value.split("@", 1)[1])) is not None
        return False

    # ---------- research (Stage 3) ----------
    def start_research(self, lead_id: int, actor: str = "role:researcher") -> int:
        """Checks the daily research cap and the round limit. Returns the round number (1 or 2)."""
        lead = self.get(lead_id)
        rounds = sum(1 for e in self.store.events_for(lead_id) if e["type"] == "research_started")
        if rounds >= MAX_RESEARCH_ROUNDS:
            raise CapReached(f"lead #{lead_id} already had {rounds} research rounds (max {MAX_RESEARCH_ROUNDS}). "
                             "Ahmad decides now: add proof by hand, or move it to rejected.")
        allowed = ("new",) if rounds == 0 else ("new", "researched")
        if lead["status"] not in allowed:
            raise InvalidMove(f"lead #{lead_id} is '{lead['status']}'. Research needs status: {', '.join(allowed)}.")
        if lead.get("company_domain") and self.store.get_block(lead["company_domain"]):
            raise Blocked(f"{lead['company_domain']} is on the block list. No research.")
        cap = self._cap("max_research_per_day")
        since = start_of_today_utc(self.tz, self.now()).isoformat(timespec="seconds")
        if cap and self.store.count_events("research_started", since) >= cap:
            raise CapReached(f"daily limit reached: {cap} research runs today (config/policy.yaml). Try tomorrow.")
        self.store.add_event("research_started", actor, lead_id, {"round": rounds + 1})
        return rounds + 1

    # snapshots
    def snapshot_path(self, sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
            raise DeskError(f"'{sha}' is not a snapshot sha256 (64 letters 0-9 a-f)")
        return self.snapshot_dir / f"{sha}.txt"

    def save_snapshot(self, url: str, text: str, http_status: int | None, title: str | None) -> tuple[str, Path, bool]:
        """Saves the page text file (never overwritten or deleted) and the DB row. Returns (sha, path, new)."""
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        path = self.snapshot_path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8", newline="\n")
        try:
            text_path = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            text_path = str(path)
        new = self.store.add_snapshot({"sha256": sha, "url": url.strip(), "http_status": http_status,
                                       "text_path": text_path, "title": (title or "")[:300] or None})
        return sha, path, new

    def snapshot_text(self, sha: str) -> str:
        path = self.snapshot_path(sha)
        if not self.store.get_snapshot(sha):
            raise NotFound(f"snapshot {sha[:12]}... is not in the database. Run scripts/snapshot.py first.")
        if not path.exists():
            raise NotFound(f"snapshot file {path.name} is missing on this PC. Run scripts/snapshot.py again.")
        return path.read_text(encoding="utf-8")

    # evidence
    def _lead_for_research(self, lead_id: int, states=RESEARCH_STATES) -> dict:
        lead = self.get(lead_id)
        if lead["status"] not in states:
            raise InvalidMove(f"lead #{lead_id} is '{lead['status']}'. This needs status: {', '.join(states)}.")
        return lead

    def _parse_depends(self, lead_id: int, depends_on: list[int] | None) -> list[int]:
        ids = sorted(set(depends_on or []))
        for eid in ids:
            ev = self.store.get_evidence(eid)
            if not ev or ev["opportunity_id"] != lead_id:
                raise DeskError(f"--depends-on E{eid} is not evidence of lead #{lead_id}")
        return ids

    def add_evidence(self, lead_id: int, *, claim: str, url: str, quote: str, grade: str, source_type: str,
                     topic: str, observed_at: str | None = None, snapshot: str | None = None,
                     depends_on: list[int] | None = None, actor: str = "role:researcher") -> tuple[int, str, list[str]]:
        """Saves one fact. The code checks the quote; it can lower the grade.
        Returns (evidence id, saved grade, warnings)."""
        self._lead_for_research(lead_id)
        claim, url, quote = (claim or "").strip(), (url or "").strip(), (quote or "").strip()
        grade = (grade or "").strip().upper()
        if grade not in qc.GRADES:
            raise DeskError(f"unknown grade '{grade}'. Use: {', '.join(qc.GRADES)}")
        if source_type not in SOURCE_TYPES:
            raise DeskError(f"unknown source type '{source_type}'. Use: {', '.join(SOURCE_TYPES)}")
        if topic not in TOPICS:
            raise DeskError(f"unknown topic '{topic}'. Use: {', '.join(TOPICS)}")
        if not claim:
            raise DeskError("--claim is required")
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise DeskError("--url must start with http:// or https://")
        if len(quote) > qc.MAX_QUOTE_CHARS:
            raise DeskError(f"quote is {len(quote)} chars; max {qc.MAX_QUOTE_CHARS}. Copy a shorter exact part.")
        observed = None
        if observed_at:
            try:
                observed = qc.parse_date(observed_at)
            except ValueError:
                raise DeskError(f"--date '{observed_at}' must be YYYY-MM-DD") from None
            if observed > self.today():
                raise DeskError(f"--date {observed} is in the future")
        deps = self._parse_depends(lead_id, depends_on)
        if grade == "INFERENCE" and not deps:
            raise DeskError("INFERENCE needs --depends-on E1,E2 (the facts it is based on)")

        warnings: list[str] = []
        if grade in qc.QUOTED_GRADES:
            if len(quote) < qc.MIN_QUOTE_CHARS:
                raise DeskError(f"{grade} needs an exact quote of at least {qc.MIN_QUOTE_CHARS} chars")
            if not snapshot:
                raise DeskError(f"{grade} needs --snapshot SHA (save the page first with scripts/snapshot.py)")
            snap = self.store.get_snapshot(snapshot)
            if snap and _same_page(snap["url"], url) is False:
                raise DeskError(f"snapshot {snapshot[:12]}... is of {snap['url']}, not {url}")
            status, score, _ = qc.find_quote(quote, self.snapshot_text(snapshot))
            if not qc.is_found(status):
                warnings.append(f"quote NOT FOUND in the saved page (similarity {score:.2f}) -> grade set to UNKNOWN")
                grade = "UNKNOWN"
            elif status == qc.FOUND_FUZZY:
                warnings.append(f"quote found with a small difference (similarity {score:.2f}). Copy it exactly.")
        elif snapshot:
            self.snapshot_text(snapshot)   # must exist

        row = {"opportunity_id": lead_id, "claim": claim, "url": url, "quote": quote,
               "snapshot_sha256": snapshot or None, "observed_at": observed.isoformat() if observed else None,
               "source_type": source_type, "topic": topic, "grade": grade, "depends_on": deps}
        eid = self.store.add_evidence(row)
        self.store.add_event("evidence_added", actor, lead_id,
                             {"evidence_id": eid, "grade": grade, "topic": topic, "warnings": warnings})
        return eid, grade, warnings

    def check_evidence(self, evidence_id: int, snapshot: str | None = None) -> tuple[dict, dict]:
        """Runs all code checks on one evidence item. Does not save anything."""
        ev = self.store.get_evidence(evidence_id)
        if not ev:
            raise NotFound(f"evidence E{evidence_id} not found")
        lead = self.get(ev["opportunity_id"])
        sha = snapshot or ev.get("snapshot_sha256")
        report = {"quote_status": None, "score": 0.0, "match": "", "skip_reason": ""}
        if ev["grade"] in qc.QUOTED_GRADES or (sha and ev.get("quote")):
            if not sha:
                report.update(quote_status=qc.NOT_FOUND, skip_reason="no snapshot")
            else:
                if snapshot:
                    snap = self.store.get_snapshot(snapshot)
                    if snap and _same_page(snap["url"], ev["url"]) is False:
                        raise DeskError(f"snapshot {snapshot[:12]}... is of {snap['url']}, not {ev['url']}")
                status, score, match = qc.find_quote(ev["quote"], self.snapshot_text(sha))
                report.update(quote_status=status, score=score, match=match)
        else:
            report["skip_reason"] = "no quote to check (INFERENCE / UNKNOWN)"
        deps_ok = True
        if ev["grade"] == "INFERENCE":
            deps = [self.store.get_evidence(d) for d in (ev.get("depends_on") or [])]
            deps_ok = bool(deps) and all(d and d["verified"] and d["grade"] != "UNKNOWN" for d in deps)
        decay = qc.decay_days_for(lead.get("pattern_id"), ev.get("source_type"), self.problems)
        stale = qc.is_stale(ev.get("observed_at"), decay, self.today())
        top, reasons = qc.max_grade(ev["grade"], quote_status=report["quote_status"],
                                    source_type=ev.get("source_type"), observed_at=ev.get("observed_at"),
                                    decay_days=decay, today=self.today(), depends_ok=deps_ok)
        report.update(decay_days=decay, stale=stale, max_grade=top, reasons=reasons)
        return ev, report

    def verify_evidence(self, evidence_id: int, grade: str, note: str, claim: str | None = None,
                        snapshot: str | None = None, actor: str = "role:checker") -> tuple[dict, str, list[str]]:
        """Checker's final grade. The checker can lower a grade, never raise it; the code can lower it more.
        Returns (evidence before, final grade, reasons)."""
        grade = (grade or "").strip().upper()
        if grade not in qc.GRADES:
            raise DeskError(f"unknown grade '{grade}'. Use: {', '.join(qc.GRADES)}")
        if not note or not note.strip():
            raise DeskError("a note is required (--note TEXT): why this grade")
        ev, report = self.check_evidence(evidence_id, snapshot)
        self._lead_for_research(ev["opportunity_id"], CHECK_STATES)
        reasons = list(report["reasons"])
        if qc.GRADE_RANK[grade] > qc.GRADE_RANK[ev["grade"]]:
            reasons.append(f"checker cannot raise {ev['grade']} to {grade}")
        final = qc.lowest(grade, ev["grade"], report["max_grade"])
        fields = {"grade": final, "verified": True, "verifier_note": note.strip()}
        if claim and claim.strip():
            fields["claim"] = claim.strip()
        if snapshot:
            fields["snapshot_sha256"] = snapshot
        self.store.update_evidence(evidence_id, fields)
        self.store.add_event("evidence_verified", actor, ev["opportunity_id"],
                             {"evidence_id": evidence_id, "from": ev["grade"], "to": final,
                              "quote": report["quote_status"], "reasons": reasons})
        return ev, final, reasons

    # people and research notes
    def set_contact(self, lead_id: int, *, title: str, name: str | None = None, role_type: str | None = None,
                    email: str | None = None, email_status: str | None = None, profile_url: str | None = None,
                    evidence_id: int | None = None, actor: str = "role:researcher") -> int:
        lead = self._lead_for_research(lead_id)
        if not title or not title.strip():
            raise DeskError("--title is required (for example: Owner, Office Manager)")
        if role_type and role_type not in ROLE_TYPES:
            raise DeskError(f"unknown role type '{role_type}'. Use: {', '.join(ROLE_TYPES)}")
        if evidence_id is not None:
            ev = self.store.get_evidence(evidence_id)
            if not ev or ev["opportunity_id"] != lead_id:
                raise DeskError(f"--evidence E{evidence_id} is not evidence of lead #{lead_id}")
        email = (email or "").strip().lower() or None
        if email:
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise DeskError(f"'{email}' is not an email")
            if email_status not in EMAIL_OK:
                raise DeskError("an email needs --email-status published or verified. "
                                "Never save a guessed email.")
            if evidence_id is None:
                raise DeskError("an email needs --evidence EID (the page where it is published)")
            if self.is_blocked(email):
                raise Blocked(f"{email} is on the block list. Not saved.")
        elif email_status:
            raise DeskError("--email-status without --email")
        row = {"company_id": lead["company_id"], "full_name": (name or "").strip() or None,
               "title": title.strip(), "role_type": role_type, "email": email,
               "email_status": email_status if email else None,
               "profile_url": (profile_url or "").strip() or None, "evidence_id": evidence_id}
        pid = self.store.add_person(row)
        if role_type == "owner" or not lead.get("owner_person_id"):
            self.store.update_lead(lead_id, {"owner_person_id": pid})
        self.store.add_event("contact_added", actor, lead_id,
                             {"person_id": pid, "title": row["title"], "role_type": role_type,
                              "email_status": row["email_status"]})
        return pid

    def set_research(self, lead_id: int, *, pattern: str | None = None, why_now: str | None = None,
                     unknowns: list[str] | None = None, channel: str | None = None,
                     company_name: str | None = None, domain: str | None = None, industry: str | None = None,
                     size: str | None = None, country: str | None = None,
                     actor: str = "role:researcher") -> list[str]:
        """Saves research notes on the lead and its company. Returns the names of changed fields."""
        lead = self._lead_for_research(lead_id, CHECK_STATES)
        lead_fields: dict = {}
        company_fields: dict = {}
        if pattern is not None:
            ids = [p.get("id") for p in self.problems.get("patterns") or []]
            if pattern not in ids:
                raise DeskError(f"unknown pattern '{pattern}'. Patterns in config/problems.yaml: {', '.join(ids)}")
            lead_fields["pattern_id"] = pattern
        if why_now is not None:
            lead_fields["why_now"] = why_now.strip()
        if unknowns is not None:
            lead_fields["unknowns"] = [u.strip() for u in unknowns if u.strip()]
        if channel is not None:
            lead_fields["channel"] = normalize_channel(channel, list(self.policy.get("channels_allowed") or []))
        if company_name is not None and company_name.strip():
            company_fields["name"] = company_name.strip()
            company_fields["name_key"] = name_key(company_name)
        if domain is not None and domain.strip():
            d = registered_domain(host_of(domain))
            if not d or "." not in d or is_platform(d):
                raise DeskError(f"'{domain}' is not a company website domain")
            if self.store.get_block(d):
                raise Blocked(f"{d} is on the block list.")
            for c in self.store.list_companies():
                if c.get("domain") == d and c["id"] != lead["company_id"]:
                    raise DuplicateLead(f"{d} already belongs to company #{c['id']} '{c['name']}'.")
            company_fields["domain"] = d
        for key, value in (("industry", industry), ("size_band", size), ("country", country)):
            if value is not None and value.strip():
                company_fields[key] = value.strip()
        if not lead_fields and not company_fields:
            raise DeskError("nothing to save. Give at least one option (see --help).")
        if lead_fields:
            self.store.update_lead(lead_id, lead_fields)
        if company_fields:
            self.store.update_company(lead["company_id"], company_fields)
        changed = sorted(set(lead_fields) | set(company_fields))
        self.store.add_event("research_saved", actor, lead_id, {"fields": changed})
        return changed

    def proof_gaps(self, lead: dict) -> list[str]:
        """What is still missing before a lead may be 'verified' ([] = nothing missing)."""
        gaps = []
        evidence = [e for e in self.store.evidence_for(lead["id"]) if e["verified"] and e.get("topic") == "pain"]
        strong = [e for e in evidence if e["grade"] in ("CONFIRMED_FACT", "STRONG_SIGNAL")]
        weak_sources = {e.get("source_type") for e in evidence if e["grade"] == "WEAK_SIGNAL"}
        if not strong and len(weak_sources) < 2:
            gaps.append("no checked problem proof (need 1 STRONG_SIGNAL / CONFIRMED_FACT, "
                        "or 2 WEAK_SIGNAL from different source types)")
        people = [p for p in self.store.people_for(lead["company_id"]) if (p.get("title") or "").strip()]
        if not people:
            gaps.append("no contact person with a title (owner role unknown)")
        if lead.get("company_domain") and self.store.get_block(lead["company_domain"]):
            gaps.append(f"{lead['company_domain']} is on the block list")
        return gaps


def _same_page(a: str, b: str) -> bool | None:
    """True/False if two URLs are the same page (ignores http/https, www, trailing slash, #part)."""
    def key(u: str) -> str:
        parts = urlsplit(u.strip())
        host = (parts.hostname or "").lower().removeprefix("www.")
        path = parts.path.rstrip("/")
        return f"{host}{path}?{parts.query}" if parts.query else f"{host}{path}"
    try:
        return key(a) == key(b)
    except ValueError:
        return None


def open_store(backend: str | None = None, db_path: Path | None = None, env_file: Path | None = None) -> Store:
    env = load_env(env_file)
    backend = (backend or env.get("DESK_BACKEND") or "supabase").lower()
    if backend == "sqlite":
        from store_sqlite import SqliteStore
        return SqliteStore(db_path or DEFAULT_SQLITE)
    if backend == "supabase":
        from store_supabase import SupabaseStore
        url, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SECRET_KEY", "")
        if not url or not key:
            raise ConfigError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env "
                              "(see .env.example). Or use --backend sqlite.")
        return SupabaseStore(url, key)
    raise ConfigError(f"unknown backend '{backend}' (use supabase or sqlite)")
