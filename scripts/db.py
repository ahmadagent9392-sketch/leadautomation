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

import checks as ck
import followups as fu
import quote_check as qc
import replies as rp

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
DEFAULT_SQLITE = ROOT / "data" / "desk.db"
DEFAULT_SNAPSHOTS = ROOT / "data" / "snapshots"
DEFAULT_CARDS = ROOT / "cards"

TABLES = ("companies", "opportunities", "snapshots", "evidence", "people", "messages",
          "replies", "follow_ups", "approvals", "suppression", "events", "raw_items")

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
SCORE_STATES = ("verified", "qualified")        # fit / value can be set only in these (Stage 4)
DRAFT_STATES = ("qualified", "draft_ready")     # the writer can save a draft only in these (Stage 5)
MAX_REWRITES = 2                                # per lead per day: first draft + 2 rewrites
CRITIC_KEYS = ("specific", "true", "relevant", "short", "tone", "next_step", "compliance")
CRITIC_VERDICTS = ("APPROVE_FOR_HUMAN", "REWRITE", "REJECT")
MIN_CRITIC_TOTAL = 11                           # of 14; and every score >= 1
DECISIONS = {"approve": "approved", "edit": "edited", "reject": "rejected"}
FOLLOWUP_STATES = ("contacted", "replied")      # follow-up drafts (touch 2-5) only in these (Stage 6)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# reply categories the code deals with by itself; the others wait for Ahmad ("your move")
AUTO_HANDLED = ("opt_out", "out_of_office", "not_now", "not_interested")


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
    def add_message(self, row: dict) -> int: ...
    def get_message(self, message_id: int) -> dict | None: ...
    def update_message(self, message_id: int, fields: dict) -> None: ...
    def messages_for(self, lead_id: int) -> list[dict]: ...
    def add_approval(self, row: dict) -> int: ...
    def approvals_for(self, object_type: str, object_id: int) -> list[dict]: ...
    def list_messages(self) -> list[dict]: ...
    def events_since(self, type_: str, since_iso: str) -> list[dict]: ...
    def update_person(self, person_id: int, fields: dict) -> None: ...
    def add_reply(self, row: dict) -> int: ...
    def get_reply(self, reply_id: int) -> dict | None: ...
    def find_reply_by_gmail_id(self, gmail_message_id: str) -> dict | None: ...
    def update_reply(self, reply_id: int, fields: dict) -> None: ...
    def replies_for(self, lead_id: int) -> list[dict]: ...
    def list_replies(self) -> list[dict]: ...
    def add_follow_up(self, row: dict) -> int: ...
    def update_follow_up(self, follow_up_id: int, fields: dict) -> None: ...
    def follow_ups_for(self, lead_id: int) -> list[dict]: ...
    def list_follow_ups(self, status: str | None = None) -> list[dict]: ...
    def add_raw_item(self, row: dict) -> int | None: ...  # None = same URL already saved
    def get_raw_item(self, raw_id: int) -> dict | None: ...
    def find_raw_by_url(self, url: str) -> dict | None: ...
    def list_raw_items(self, status: str | None = None, source: str | None = None,
                       idea: str | None = None) -> list[dict]: ...
    def update_raw_item(self, raw_id: int, fields: dict) -> None: ...


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


def load_problems(config_dir: Path | None = None) -> dict:
    """problems.yaml + the temporary patterns made by /search-idea (config/ideas/*.yaml, Stage 7)."""
    problems = load_yaml("problems", config_dir)
    patterns = list(problems.get("patterns") or [])
    ids = {p.get("id") for p in patterns}
    ideas_dir = (config_dir or ROOT / "config") / "ideas"
    for path in sorted(ideas_dir.glob("*.yaml")) if ideas_dir.is_dir() else []:
        idea = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(idea, dict) and idea.get("id") and idea["id"] not in ids:
            patterns.append({**idea, "temporary": True})
            ids.add(idea["id"])
    return {**problems, "patterns": patterns}


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
        self.config_dir = config_dir
        self.policy = load_yaml("policy", config_dir)
        self.problems = load_problems(config_dir)
        self.cadence = load_yaml("cadence", config_dir)
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
        lead = self.get(lead_id)
        after_bounce = lead["status"] == "contacted" and self.open_bounce(lead_id) is not None
        if not after_bounce:
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
        if role_type == "owner" or not lead.get("owner_person_id") or after_bounce:
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
                raise DeskError(f"unknown pattern '{pattern}'. Patterns (config/problems.yaml + config/ideas): {', '.join(ids)}")
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

    # ---------- ranking (Stage 4) ----------
    def set_score(self, lead_id: int, *, fit: int, value: int, fit_why: str, value_why: str,
                  disqualifiers: list[str] | None = None, actor: str = "role:ranker") -> dict:
        """Saves the judgment part of ranking (fit 0-3, value 1-3, reasons, disqualifiers).
        Everything else (gates, urgency, priority) is computed by scripts/rank.py."""
        lead = self._lead_for_research(lead_id, SCORE_STATES)
        if not isinstance(fit, int) or not 0 <= fit <= 3:
            raise DeskError(f"--fit must be 0, 1, 2 or 3 (got {fit})")
        if not isinstance(value, int) or not 1 <= value <= 3:
            raise DeskError(f"--value must be 1, 2 or 3 (got {value})")
        if not (fit_why or "").strip():
            raise DeskError("--fit-why is required: one line, why this fit")
        if not (value_why or "").strip():
            raise DeskError("--value-why is required: one line, why this value")
        dq = [d.strip() for d in (disqualifiers or []) if d and d.strip()]
        info = dict(lead.get("rank_info") or {})
        info.update({"fit_why": fit_why.strip(), "value_why": value_why.strip(), "disqualifiers": dq,
                     "scored_by": actor, "scored_at": self.now().isoformat(timespec="seconds")})
        self.store.update_lead(lead_id, {"fit": fit, "value_band": value, "rank_info": info})
        self.store.add_event("scored", actor, lead_id, {"fit": fit, "value": value, "disqualifiers": dq})
        return info

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

    # ---------- drafts, critic, approvals, export (Stage 5) ----------
    def me(self) -> dict:
        return load_yaml("me", self.config_dir)

    def _since_today(self) -> str:
        return start_of_today_utc(self.tz, self.now()).isoformat(timespec="seconds")

    def get_message(self, message_id: int) -> dict:
        msg = self.store.get_message(message_id)
        if not msg or msg.get("direction") != "out":
            raise NotFound(f"draft M{message_id} not found")
        return msg

    def latest_draft(self, lead_id: int) -> dict | None:
        drafts = [m for m in self.store.messages_for(lead_id) if m.get("direction") == "out"]
        return drafts[-1] if drafts else None

    def _must_be_latest(self, msg: dict) -> None:
        latest = self.latest_draft(msg["opportunity_id"])
        if latest and latest["id"] != msg["id"]:
            raise InvalidMove(f"M{msg['id']} is an old draft. The newest draft of lead #{msg['opportunity_id']} "
                              f"is M{latest['id']}.")

    def recipient(self, lead: dict) -> dict | None:
        """The person the message goes to: the lead's owner contact."""
        people = self.store.people_for(lead["company_id"])
        for p in people:
            if p["id"] == lead.get("owner_person_id"):
                return p
        return people[0] if people else None

    def check_message(self, message_id: int, final: bool = False) -> tuple[dict, dict, list[ck.Problem]]:
        msg = self.get_message(message_id)
        lead = self.get(msg["opportunity_id"])
        problems = ck.check_draft(
            channel=msg.get("channel") or lead["channel"], subject=msg.get("subject"), body=msg["body"],
            evidence_ids=list(msg.get("evidence_ids") or []), lead=lead,
            evidence=self.store.evidence_for(lead["id"]), recipient=self.recipient(lead), policy=self.policy,
            me=self.me(), is_blocked=self.is_blocked, touch_number=int(msg.get("touch_number") or 1), final=final,
            previous_bodies=[m["body"] for m in self.sent_messages(lead["id"]) if m["id"] != msg["id"]])
        return msg, lead, problems

    def drafts_today(self, lead_id: int) -> int:
        since = self._since_today()
        return sum(1 for e in self.store.events_for(lead_id) if e["type"] == "draft_saved" and e["ts"] >= since)

    def save_draft(self, lead_id: int, *, body: str, evidence_ids: list[int], subject: str | None = None,
                   channel: str | None = None, angle: str | None = None, cta: str | None = None,
                   actor: str = "role:writer") -> tuple[int, list[ck.Problem]]:
        """Saves one draft (the email footer is NOT part of it: code adds it at export).
        Returns (message id, problems found by the code checks).
        First message: lead qualified / draft_ready. Follow-up (touch 2-5): lead contacted / replied and a
        follow-up or nurture reminder is due today."""
        lead = self.get(lead_id)
        touch = self.next_touch(lead)
        if lead.get("company_domain") and self.store.get_block(lead["company_domain"]):
            raise Blocked(f"{lead['company_domain']} is on the block list. No draft.")
        channel = normalize_channel(channel or lead["channel"], list(self.policy.get("channels_allowed") or []))
        body = ck.normalize_text(body)
        if not body:
            raise DeskError("the draft text is empty")
        if self.drafts_today(lead_id) >= 1 + MAX_REWRITES:
            raise CapReached(f"lead #{lead_id} already has {1 + MAX_REWRITES} drafts today (first + {MAX_REWRITES} "
                             "rewrites). Ahmad decides now: /approve with an edit, or try again tomorrow.")
        cap = self._cap("max_drafts_per_day")
        if cap and self.store.count_events("draft_saved", self._since_today()) >= cap:
            raise CapReached(f"daily limit reached: {cap} drafts today (config/policy.yaml). Try tomorrow.")
        row = {"opportunity_id": lead_id, "touch_number": touch, "direction": "out", "channel": channel,
               "subject": ck.normalize_text(subject) or None, "body": body, "angle": (angle or "").strip() or None,
               "cta_type": (cta or "").strip() or None, "evidence_ids": sorted(set(evidence_ids or []))}
        mid = self.store.add_message(row)
        _, _, problems = self.check_message(mid)
        self.store.add_event("draft_saved", actor, lead_id,
                             {"message_id": mid, "channel": channel, "touch": touch,
                              "errors": len(ck.errors(problems))})
        return mid, problems

    def save_review(self, message_id: int, *, verdict: str, scores: dict[str, int], reasons: list[str],
                    actor: str = "role:critic") -> tuple[str, list[str]]:
        """Saves the critic's review. The code checks the score rule and the code checks.
        Returns (final verdict, notes for the user)."""
        verdict = (verdict or "").strip().upper()
        if verdict not in CRITIC_VERDICTS:
            raise DeskError(f"unknown verdict '{verdict}'. Use: {', '.join(CRITIC_VERDICTS)}")
        missing = [k for k in CRITIC_KEYS if k not in scores]
        extra = [k for k in scores if k not in CRITIC_KEYS]
        if missing or extra:
            raise DeskError(f"scores need exactly: {', '.join(CRITIC_KEYS)}"
                            + (f" (missing: {', '.join(missing)})" if missing else "")
                            + (f" (unknown: {', '.join(extra)})" if extra else ""))
        for k, v in scores.items():
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 2:
                raise DeskError(f"score '{k}' must be 0, 1 or 2 (got {v})")
        reasons = [r.strip() for r in reasons or [] if r and r.strip()]
        if verdict != "APPROVE_FOR_HUMAN" and not reasons:
            raise DeskError(f"{verdict} needs at least one --reason (what exactly to fix, or why)")
        msg, lead, problems = self.check_message(message_id)
        self._must_be_latest(msg)
        if msg.get("sent_at"):
            raise InvalidMove(f"M{msg['id']} was already sent.")
        states = DRAFT_STATES if int(msg.get("touch_number") or 1) == 1 else FOLLOWUP_STATES
        if lead["status"] not in states:
            raise InvalidMove(f"lead #{lead['id']} is '{lead['status']}'. A review needs: {', '.join(states)}.")

        total = sum(scores.values())
        notes: list[str] = []
        final = verdict
        if verdict == "APPROVE_FOR_HUMAN":
            if min(scores.values()) < 1 or total < MIN_CRITIC_TOTAL:
                final = "REWRITE"
                notes.append(f"scores do not allow approval (total {total}/14, lowest {min(scores.values())}; "
                             f"need every score >= 1 and total >= {MIN_CRITIC_TOTAL}) -> REWRITE")
            bad = ck.errors(problems)
            if bad:
                final = "REWRITE"
                notes.append("code checks have errors -> REWRITE: " + "; ".join(p.text for p in bad))
        if final == "REWRITE" and self.drafts_today(lead["id"]) >= 1 + MAX_REWRITES:
            notes.append(f"no rewrites left today (max {MAX_REWRITES}). Ahmad decides: /approve {lead['id']} "
                         "with an edit, or reject.")
        review = {"verdict": final, "critic_verdict": verdict, "scores": scores, "total": total,
                  "reasons": reasons + notes, "by": actor, "at": self.now().isoformat(timespec="seconds")}
        self.store.update_message(msg["id"], {"critic": review})
        self.store.add_event("draft_reviewed", actor, lead["id"],
                             {"message_id": msg["id"], "verdict": final, "total": total})
        if final == "APPROVE_FOR_HUMAN" and lead["status"] == "qualified":
            self.store.change_status(lead["id"], "qualified", "draft_ready",
                                     f"critic approved M{msg['id']} ({total}/14)", actor, None)
        return final, notes

    def valid_approval(self, msg: dict) -> dict | None:
        """Ahmad's last approval if it still matches the text, else None."""
        rows = self.store.approvals_for("message", msg["id"])
        if not rows or rows[-1]["decision"] == "rejected":
            return None
        last = rows[-1]
        return last if last["body_sha256"] == ck.body_sha256(msg.get("subject"), msg["body"]) else None

    def draft_state(self, msg: dict) -> str:
        if msg.get("sent_at"):
            return f"sent {to_local(msg['sent_at'], self.tz)[:10]}"
        if msg.get("gmail_draft_id"):
            return "in Gmail drafts"
        rows = self.store.approvals_for("message", msg["id"])
        if rows:
            if rows[-1]["decision"] == "rejected":
                return "rejected by Ahmad"
            if self.valid_approval(msg) is None:
                return "CHANGED after approval (approve again)"
            exported = any(e["type"] == "draft_exported" and (e.get("payload") or {}).get("message_id") == msg["id"]
                           for e in self.store.events_for(msg["opportunity_id"]))
            return "approved, copy-paste file written" if exported else "approved by Ahmad"
        critic = msg.get("critic") or {}
        return {"APPROVE_FOR_HUMAN": "ready for Ahmad (/approve)", "REWRITE": "critic: rewrite",
                "REJECT": "critic: reject"}.get(critic.get("verdict"), "waiting for critic")

    def approve(self, message_id: int, decision: str, reason: str, *, body: str | None = None,
                subject: str | None = None, close_lead: bool = False, actor: str = "human") -> tuple[int, str]:
        """Ahmad's decision on a draft: approve, edit (his own text) or reject.
        Returns (id of the approved / rejected draft, short result text)."""
        decision = (decision or "").strip().lower()
        if decision not in DECISIONS:
            raise DeskError(f"unknown decision '{decision}'. Use: approve, edit, reject")
        if not reason or not reason.strip():
            raise DeskError("a reason is required (--reason TEXT)")
        reason = reason.strip()
        msg = self.get_message(message_id)
        self._must_be_latest(msg)
        if msg.get("sent_at"):
            raise InvalidMove(f"M{msg['id']} was already sent. Nothing to decide.")
        lead = self.get(msg["opportunity_id"])
        lid = lead["id"]
        first = int(msg.get("touch_number") or 1) == 1
        if not first and lead["status"] not in FOLLOWUP_STATES:
            raise InvalidMove(f"lead #{lid} is '{lead['status']}'. A follow-up needs: {', '.join(FOLLOWUP_STATES)}.")

        if decision == "reject":
            if first and lead["status"] not in ("qualified", "draft_ready", "approved"):
                raise InvalidMove(f"lead #{lid} is '{lead['status']}'. Nothing to reject.")
            self.store.add_approval({"object_type": "message", "object_id": msg["id"],
                                     "body_sha256": ck.body_sha256(msg.get("subject"), msg["body"]),
                                     "decision": "rejected", "reason": reason})
            self.store.add_event("draft_rejected", actor, lid, {"message_id": msg["id"], "reason": reason})
            if close_lead and first:
                self.move(lid, "rejected", f"Ahmad rejected M{msg['id']}: {reason}", actor)
                return msg["id"], f"M{msg['id']} rejected; lead #{lid} is closed (rejected)."
            if lead["status"] == "approved":
                self.store.change_status(lid, "approved", "draft_ready", f"Ahmad rejected M{msg['id']}", actor, None)
            return msg["id"], f"M{msg['id']} rejected. Lead #{lid} stays open; /draft {lid} can write a new one."

        if decision == "approve":
            if first and lead["status"] not in ("draft_ready", "approved"):
                raise InvalidMove(f"lead #{lid} is '{lead['status']}'. Approve needs a draft the critic passed "
                                  "(status draft_ready). You can still use --decision edit.")
            if (msg.get("critic") or {}).get("verdict") not in ("APPROVE_FOR_HUMAN", "EDITED_BY_AHMAD"):
                raise InvalidMove(f"the critic did not pass M{msg['id']}. Use --decision edit with your own text, "
                                  "or reject.")
            target = msg
        else:  # edit
            if first and lead["status"] not in ("qualified", "draft_ready", "approved"):
                raise InvalidMove(f"lead #{lid} is '{lead['status']}'. Edit needs: qualified, draft_ready, approved.")
            new_body = ck.normalize_text(body)
            if not new_body:
                raise DeskError("edit needs the new text (--body-file FILE)")
            new_subject = ck.normalize_text(subject) if subject is not None else msg.get("subject")
            critic = {"verdict": "EDITED_BY_AHMAD", "from_message": msg["id"],
                      "old_review": msg.get("critic"), "at": self.now().isoformat(timespec="seconds")}
            new_id = self.store.add_message({
                "opportunity_id": lid, "touch_number": msg.get("touch_number") or 1, "direction": "out",
                "channel": msg.get("channel"), "subject": new_subject or None, "body": new_body,
                "angle": msg.get("angle"), "cta_type": msg.get("cta_type"),
                "evidence_ids": list(msg.get("evidence_ids") or []), "critic": critic})
            target = self.get_message(new_id)

        _, _, problems = self.check_message(target["id"])
        bad = ck.errors(problems)
        if bad:
            raise InvalidMove(f"M{target['id']} cannot be approved, the code checks found errors: "
                              + "; ".join(p.text for p in bad))
        self.store.add_approval({"object_type": "message", "object_id": target["id"],
                                 "body_sha256": ck.body_sha256(target.get("subject"), target["body"]),
                                 "decision": DECISIONS[decision], "reason": reason})
        self.store.add_event(f"draft_{DECISIONS[decision]}", actor, lid, {"message_id": target["id"], "reason": reason})
        word = "approved" if decision == "approve" else f"edited, saved as M{target['id']} and approved"
        if not first:
            return target["id"], (f"M{msg['id']} {word} (follow-up {target.get('touch_number')}). "
                                  f"Lead #{lid} stays {lead['status']}.")
        status = lead["status"]
        if status == "qualified":
            self.store.change_status(lid, "qualified", "draft_ready", f"Ahmad edited M{msg['id']}", actor, None)
            status = "draft_ready"
        if status == "draft_ready":
            self.store.change_status(lid, "draft_ready", "approved", f"Ahmad approved M{target['id']}", actor, None)
        return target["id"], f"M{msg['id']} {word}. Lead #{lid} is approved."

    def strict_config_problems(self) -> list[str]:
        """config_check --strict: errors + warnings ([] = ready for outreach)."""
        import config_check
        report = config_check.run_checks(self.config_dir or ROOT / "config")
        return report.errors + report.warnings

    def export_draft(self, message_id: int, out_dir: Path | None = None) -> dict:
        """The last gate before Gmail / copy-paste. Never sends anything.
        email -> returns {to, subject, body} for Gmail create_draft (body includes the footer).
        other channels -> writes cards/<lead id>-message.txt and returns its path."""
        msg = self.get_message(message_id)
        self._must_be_latest(msg)
        lead = self.get(msg["opportunity_id"])
        lid = lead["id"]
        touch = int(msg.get("touch_number") or 1)
        if msg.get("sent_at"):
            raise InvalidMove(f"M{msg['id']} was already sent.")
        if touch == 1 and lead["status"] != "approved":
            raise InvalidMove(f"lead #{lid} is '{lead['status']}'. Export needs an approved draft (/approve {lid}).")
        if touch > 1:
            if lead["status"] not in FOLLOWUP_STATES:
                raise InvalidMove(f"lead #{lid} is '{lead['status']}'. No follow-up can go out now.")
            if self.unsorted_reply(lid) or not self.due_follow_up(lid):
                raise InvalidMove(f"no follow-up of lead #{lid} is due today (a reply came, or it was stopped). "
                                  "Run: python scripts/followups.py")
        cfg = self.strict_config_problems()
        if cfg:
            raise InvalidMove("config_check --strict fails, so nothing goes to Gmail or a copy-paste file yet. "
                              f"{len(cfg)} item(s) to fix, for example: " + "; ".join(cfg[:3])
                              + ". Run: python scripts/config_check.py --strict")
        rows = self.store.approvals_for("message", msg["id"])
        if not rows or rows[-1]["decision"] == "rejected":
            raise InvalidMove(f"M{msg['id']} has no approval from Ahmad (/approve {lid}).")
        if self.valid_approval(msg) is None:
            self.store.add_event("approval_invalid", "code:export", lid,
                                 {"message_id": msg["id"], "reason": "text changed after approval"})
            if touch == 1:
                self.store.change_status(lid, "approved", "draft_ready",
                                         f"M{msg['id']} text changed after approval", "code:export", None)
            raise InvalidMove(f"M{msg['id']} was changed after Ahmad approved it. The approval is no longer valid; "
                              f"lead #{lid} is back to draft_ready. Approve again (/approve {lid}).")
        _, _, problems = self.check_message(msg["id"], final=True)
        bad = ck.errors(problems)
        if bad:
            raise InvalidMove(f"M{msg['id']} fails the final checks: " + "; ".join(p.text for p in bad))
        channel = msg.get("channel") or lead["channel"]

        if channel == "email":
            if msg.get("gmail_draft_id"):
                raise InvalidMove(f"M{msg['id']} is already in Gmail drafts (id {msg['gmail_draft_id']}).")
            cap = self._cap("max_first_emails_per_day")
            if touch == 1 and cap:
                made = [e for e in self.store.events_since("gmail_draft_created", self._since_today())
                        if int((e.get("payload") or {}).get("touch") or 1) == 1]
                if len(made) >= cap:
                    raise CapReached(f"daily limit reached: {cap} first emails today (config/policy.yaml). "
                                     "Try tomorrow.")
            footer = ck.render_footer(self.policy, self.me())
            to = self.recipient(lead)["email"]
            subject, reply_to = msg.get("subject") or "", None
            if touch > 1:
                last = self.sent_messages(lid)[-1]
                if last.get("gmail_message_id") and self.sent_to(last) == to:
                    reply_to = last["gmail_message_id"]          # same Gmail thread
                if not subject:
                    first_subject = next((m.get("subject") for m in self.sent_messages(lid) if m.get("subject")), "")
                    subject = first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
            return {"channel": channel, "message_id": msg["id"], "touch": touch, "to": [to], "subject": subject,
                    "body": f"{msg['body']}\n\n{footer}", "reply_to_message_id": reply_to}

        out = Path(out_dir) if out_dir else DEFAULT_CARDS
        out.mkdir(parents=True, exist_ok=True)
        path = out / (f"{lid}-message.txt" if touch == 1 else f"{lid}-touch{touch}-message.txt")
        text = (f"Subject: {msg['subject']}\n\n" if msg.get("subject") else "") + msg["body"] + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        self.store.add_event("draft_exported", "code:export", lid, {"message_id": msg["id"], "path": path.name})
        return {"channel": channel, "message_id": msg["id"], "path": str(path)}

    def set_gmail_draft(self, message_id: int, draft_id: str, thread_id: str | None = None,
                        actor: str = "role:approve") -> None:
        """Saves the Gmail draft id after Claude made the draft with the Gmail connector."""
        draft_id = (draft_id or "").strip()
        if not draft_id:
            raise DeskError("--draft-id is required (the id Gmail create_draft returned)")
        msg = self.get_message(message_id)
        if (msg.get("channel") or "") != "email":
            raise DeskError(f"M{msg['id']} is not an email")
        if msg.get("gmail_draft_id"):
            raise InvalidMove(f"M{msg['id']} already has Gmail draft {msg['gmail_draft_id']}")
        if self.valid_approval(msg) is None:
            raise InvalidMove(f"M{msg['id']} has no valid approval. Delete the Gmail draft by hand and /approve again.")
        self.store.update_message(msg["id"], {"gmail_draft_id": draft_id,
                                              "thread_id": (thread_id or "").strip() or None})
        self.store.add_event("gmail_draft_created", actor, msg["opportunity_id"],
                             {"message_id": msg["id"], "gmail_draft_id": draft_id,
                              "touch": int(msg.get("touch_number") or 1)})

    # ---------- sent messages, replies, follow-ups (Stage 6) ----------
    def local_date(self, ts: str | date | None) -> date | None:
        if not ts:
            return None
        if isinstance(ts, date) and not isinstance(ts, datetime):
            return ts
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.tz).date()

    def sent_messages(self, lead_id: int) -> list[dict]:
        """Messages Ahmad really sent (oldest first)."""
        sent = [m for m in self.store.messages_for(lead_id) if m.get("direction") == "out" and m.get("sent_at")]
        return sorted(sent, key=lambda m: (m["sent_at"], m["id"]))

    def sent_to(self, msg: dict) -> str | None:
        """The email address a sent message went to (saved when Ahmad sent it)."""
        for e in self.store.events_for(msg["opportunity_id"]):
            p = e.get("payload") or {}
            if e["type"] == "message_sent" and p.get("message_id") == msg["id"]:
                return p.get("to")
        return None

    def pending_follow_ups(self, lead_id: int) -> list[dict]:
        return [f for f in self.store.follow_ups_for(lead_id) if f["status"] == "pending"]

    def due_follow_up(self, lead_id: int) -> dict | None:
        """The pending follow-up / nurture reminder that is due today (or late), if any."""
        today = self.today().isoformat()
        due = [f for f in self.pending_follow_ups(lead_id)
               if f["kind"] in ("followup", "nurture") and str(f["due_on"])[:10] <= today]
        return due[0] if due else None

    def unsorted_reply(self, lead_id: int) -> dict | None:
        return next((r for r in self.store.replies_for(lead_id) if not r.get("category")), None)

    def open_bounce(self, lead_id: int) -> dict | None:
        return next((r for r in self.store.replies_for(lead_id)
                     if r.get("category") == "bounce" and not r.get("handled_at")), None)

    def next_touch(self, lead: dict) -> int:
        """Which message number a new draft for this lead would be. Refuses when no draft is allowed now."""
        lid, status = lead["id"], lead["status"]
        if status in DRAFT_STATES:
            return 1
        if status not in FOLLOWUP_STATES:
            raise InvalidMove(f"lead #{lid} is '{status}'. A first draft needs: {', '.join(DRAFT_STATES)}; "
                              f"a follow-up needs: {', '.join(FOLLOWUP_STATES)} and a due follow-up.")
        waiting = self.unsorted_reply(lid)
        if waiting:
            raise InvalidMove(f"reply R{waiting['id']} of lead #{lid} is not sorted yet. Run /sync first.")
        touch = len(self.sent_messages(lid)) + 1
        limit = fu.max_touches(self.cadence, self.policy)
        if touch > limit:
            raise CapReached(f"lead #{lid} already had {touch - 1} messages (max {limit} touches). No more follow-ups.")
        if not self.due_follow_up(lid):
            nxt = [f for f in self.pending_follow_ups(lid) if f["kind"] in ("followup", "nurture")]
            when = f" Next one is due {str(nxt[0]['due_on'])[:10]}." if nxt else ""
            raise InvalidMove(f"lead #{lid} is '{status}' and no follow-up is due today.{when}")
        return touch

    def mark_sent(self, message_id: int, gmail_message_id: str | None = None, sent_at: str | None = None,
                  actor: str = "human") -> str:
        """Ahmad pressed Send (seen by /sync in Gmail, or told by hand for LinkedIn / Upwork...)."""
        msg = self.get_message(message_id)
        if msg.get("sent_at"):
            raise InvalidMove(f"M{msg['id']} is already marked as sent ({to_local(msg['sent_at'], self.tz)}).")
        self._must_be_latest(msg)
        if self.valid_approval(msg) is None:
            raise InvalidMove(f"M{msg['id']} has no valid approval from Ahmad. Only approved messages are tracked.")
        lead = self.get(msg["opportunity_id"])
        lid, touch = lead["id"], int(msg.get("touch_number") or 1)
        if touch == 1 and lead["status"] != "approved":
            raise InvalidMove(f"lead #{lid} is '{lead['status']}'. The first message needs status approved.")
        if touch > 1 and lead["status"] not in FOLLOWUP_STATES:
            raise InvalidMove(f"lead #{lid} is '{lead['status']}'. A follow-up needs: {', '.join(FOLLOWUP_STATES)}.")
        when = self.now()
        if sent_at:
            try:
                when = datetime.fromisoformat(sent_at.strip().replace("Z", "+00:00"))
            except ValueError:
                raise DeskError(f"--sent-at '{sent_at}' must be like 2026-10-05 or 2026-10-05T10:30") from None
            if when.tzinfo is None:
                when = when.replace(tzinfo=self.tz) if len(sent_at.strip()) > 10 else \
                    datetime.combine(when.date(), datetime.min.time(), self.tz).replace(hour=12)
            if when > self.now() + timedelta(minutes=5):
                raise DeskError(f"--sent-at {sent_at} is in the future")
        fields = {"sent_at": when.astimezone(timezone.utc).isoformat(timespec="seconds")}
        if gmail_message_id and gmail_message_id.strip():
            fields["gmail_message_id"] = gmail_message_id.strip()
        self.store.update_message(msg["id"], fields)
        for f in self.pending_follow_ups(lid):
            if f["kind"] in ("followup", "nurture"):
                self.store.update_follow_up(f["id"], {"status": "done"})
        person = self.recipient(lead) or {}
        self.store.add_event("message_sent", actor, lid,
                             {"message_id": msg["id"], "touch": touch, "channel": msg.get("channel"),
                              "to": person.get("email") if msg.get("channel") == "email" else None})
        if touch == 1:
            self.store.change_status(lid, "approved", "contacted", f"Ahmad sent M{msg['id']}", actor, None)
        self.sync_lead(self.get(lid))
        nxt = [f for f in self.pending_follow_ups(lid) if f["kind"] == "followup"]
        text = f"M{msg['id']} marked as sent (touch {touch}). Lead #{lid} is {self.get(lid)['status']}."
        if nxt:
            text += f" Next follow-up due {str(nxt[0]['due_on'])[:10]}."
        return text

    def draft_missing(self, message_id: int, actor: str = "role:sync") -> None:
        """The Gmail draft is gone but was not sent (Ahmad deleted it). The approval stays; export can run again."""
        msg = self.get_message(message_id)
        if not msg.get("gmail_draft_id"):
            raise InvalidMove(f"M{msg['id']} has no Gmail draft saved.")
        if msg.get("sent_at"):
            raise InvalidMove(f"M{msg['id']} was sent; it is not missing.")
        self.store.update_message(msg["id"], {"gmail_draft_id": None, "thread_id": None})
        self.store.add_event("gmail_draft_missing", actor, msg["opportunity_id"],
                             {"message_id": msg["id"], "old_draft_id": msg["gmail_draft_id"]})

    def log_reply(self, lead_id: int, text: str, *, gmail_message_id: str | None = None, sender: str | None = None,
                  subject: str | None = None, received_at: str | None = None,
                  actor: str = "role:sync") -> tuple[int, str | None, list[str], bool]:
        """Saves one reply. The fixed rules run at once: opt-out and bounce are acted on immediately.
        Returns (reply id, rule result or None, what the code did, new)."""
        lead = self.get(lead_id)
        body = (text or "").replace("\r\n", "\n").strip()
        if not body:
            raise DeskError("the reply text is empty")
        gid = (gmail_message_id or "").strip() or None
        if gid:
            old = self.store.find_reply_by_gmail_id(gid)
            if old:
                return old["id"], old.get("category"), [], False
        sent = self.sent_messages(lead_id)
        if not sent:
            raise InvalidMove(f"nothing was sent to lead #{lead_id} yet. Mark the message as sent first "
                              "(desk.py mark-sent M<id>).")
        rule = rp.quick_class(body, sender, subject)
        row = {"opportunity_id": lead_id, "message_id": sent[-1]["id"], "body": body, "objections": [],
               "gmail_message_id": gid, "sender": (sender or "").strip() or None,
               "subject": (subject or "").strip() or None}
        if rule in ("opt_out", "bounce"):
            row.update(category=rule, next_action="block")
        rid = self.store.add_reply(row)
        self.store.add_event("reply_logged", actor, lead_id,
                             {"reply_id": rid, "rule": rule, "gmail_message_id": gid, "received_at": received_at})
        done: list[str] = []
        if rule in ("opt_out", "bounce"):
            done = self._apply_reply(lead, self.store.get_reply(rid), rule, None, "code:replies")
        return rid, rule, done, True

    def classify_reply(self, reply_id: int, *, category: str, next_action: str, note: str,
                       objections: list[str] | None = None, asked: str | None = None,
                       follow_up_date: str | None = None, actor: str = "role:reply-reader") -> tuple[str, list[str]]:
        """The reply reader's result. The fixed rules win for opt-out and bounce.
        Returns (final category, notes about what the code did)."""
        category = (category or "").strip().lower()
        next_action = (next_action or "").strip().lower()
        if category not in rp.CATEGORIES:
            raise DeskError(f"unknown category '{category}'. Use: {', '.join(rp.CATEGORIES)}")
        if next_action not in rp.NEXT_ACTIONS:
            raise DeskError(f"unknown next action '{next_action}'. Use: {', '.join(rp.NEXT_ACTIONS)}")
        if not (note or "").strip():
            raise DeskError("a note is required (--note TEXT): why this category")
        their_date = None
        if follow_up_date:
            try:
                their_date = qc.parse_date(follow_up_date)
            except ValueError:
                raise DeskError(f"--date '{follow_up_date}' must be YYYY-MM-DD") from None
        reply = self.store.get_reply(reply_id)
        if not reply:
            raise NotFound(f"reply R{reply_id} not found")
        lead = self.get(reply["opportunity_id"])
        notes: list[str] = []
        already = reply.get("category") in ("opt_out", "bounce")
        if already:
            final = reply["category"]
            if category != final:
                notes.append(f"the fixed rules already said {final}; kept {final}")
        elif reply.get("classified_at"):
            raise InvalidMove(f"R{reply_id} is already sorted as '{reply.get('category')}'. "
                              "Ahmad can change the lead by hand (desk.py move).")
        else:
            rule = rp.quick_class(reply["body"], reply.get("sender"), reply.get("subject"))
            final = category
            if rule in ("opt_out", "bounce") and category != rule:
                final = rule
                notes.append(f"the fixed rules found {rule} -> {rule} (not {category})")
        action = next_action if final == category else "block"
        self.store.update_reply(reply_id, {
            "category": final, "objections": [o.strip() for o in objections or [] if o and o.strip()],
            "requested_action": (asked or "").strip() or None,
            "follow_up_date": their_date.isoformat() if their_date else None, "next_action": action,
            "note": note.strip(), "classified_at": self.now().isoformat(timespec="seconds")})
        self.store.add_event("reply_classified", actor, lead["id"],
                             {"reply_id": reply_id, "category": final, "agent_category": category,
                              "next_action": action})
        if not already:
            notes += self._apply_reply(lead, self.store.get_reply(reply_id), final, their_date, actor)
        return final, notes

    def reply_done(self, reply_id: int, note: str, actor: str = "human") -> None:
        """Ahmad answered (or decided) this reply; /today stops showing it."""
        if not (note or "").strip():
            raise DeskError("a note is required (--note TEXT): what you did")
        reply = self.store.get_reply(reply_id)
        if not reply:
            raise NotFound(f"reply R{reply_id} not found")
        if reply.get("handled_at"):
            raise InvalidMove(f"R{reply_id} is already done.")
        self._handled(reply, note.strip(), actor)

    def _handled(self, reply: dict, note: str, actor: str) -> None:
        old = (reply.get("note") or "").strip()
        self.store.update_reply(reply["id"], {"handled_at": self.now().isoformat(timespec="seconds"),
                                              "note": f"{old} | {note}" if old else note})
        self.store.add_event("reply_handled", actor, reply["opportunity_id"], {"reply_id": reply["id"], "note": note})

    def _cancel_pending(self, lead_id: int, reason: str, kinds=("followup", "nurture", "stale_check")) -> list[str]:
        out = []
        for f in self.pending_follow_ups(lead_id):
            if f["kind"] in kinds:
                self.store.update_follow_up(f["id"], {"status": "cancelled"})
                out.append(f"cancelled {f['kind']} due {str(f['due_on'])[:10]} ({reason})")
        return out

    def _try_move(self, lead_id: int, new: str, reason: str, actor: str) -> str | None:
        status = self.get(lead_id)["status"]
        if new not in TRANSITIONS.get(status, set()):
            return None
        self.store.change_status(lead_id, status, new, reason, actor, reason if new in END_STATES else None)
        return f"lead #{lead_id}: {status} -> {new}"

    def _apply_reply(self, lead: dict, reply: dict, category: str, their_date: date | None, actor: str) -> list[str]:
        """What the code does for each kind of reply. Never sends anything."""
        lid, rid, out = lead["id"], reply["id"], []
        person = self.recipient(lead) or {}
        if category == "opt_out":
            emails = {(person.get("email") or "").lower()}
            m = EMAIL_RE.search(reply.get("sender") or "")
            if m and not rp.BOUNCE_SENDER.search(reply.get("sender") or ""):
                emails.add(m.group(0).lower())
            for e in sorted(x for x in emails if x):
                _, _, added, _ = self.block(e, f"opt_out (reply R{rid})", actor)
                out.append(f"blocked {e}" if added else f"{e} was already blocked")
            out += self._cancel_pending(lid, "opt-out")
            moved = self._try_move(lid, "opted_out", f"opt-out in reply R{rid}", actor)
            out += [moved] if moved else []
            self._handled(reply, "opt-out: blocked, never contact again", actor)
        elif category == "bounce":
            if person.get("email"):
                _, _, added, _ = self.block(person["email"], f"bounce (reply R{rid})", actor)
                self.store.update_person(person["id"], {"email_status": "invalid"})
                out.append(f"blocked {person['email']} (bounce), contact P{person['id']} email marked invalid")
            out += self._cancel_pending(lid, "bounce")
            out.append(f"find a new contact (desk.py set-contact {lid} ...) or close it "
                       f"(desk.py move {lid} no_response --reason bounce)")
        elif category == "out_of_office":
            new_due = fu.after_out_of_office(self.today(), their_date, self.cadence)
            for f in self.pending_follow_ups(lid):
                if f["kind"] == "followup" and str(f["due_on"])[:10] < new_due.isoformat():
                    self.store.update_follow_up(f["id"], {"due_on": new_due.isoformat()})
                    out.append(f"follow-up moved to {new_due} (out of office)")
            self._handled(reply, "out of office: follow-up moved", actor)
        elif category == "not_now":
            moved = self._try_move(lid, "replied", f"reply R{rid}: not now", actor)
            out += [moved] if moved else []
            out += self._cancel_pending(lid, "not now")
            due = fu.nurture_on(self.today(), their_date, self.cadence)
            self.store.add_follow_up({"opportunity_id": lid, "due_on": due.isoformat(), "kind": "nurture",
                                      "touch_number": None, "status": "pending"})
            out.append(f"reminder to try again on {due}")
            self._handled(reply, f"not now: reminder {due}", actor)
        elif category == "not_interested":
            for new in ("replied", "lost"):
                moved = self._try_move(lid, new, f"reply R{rid}: not interested", actor)
                out += [moved] if moved else []
            out += self._cancel_pending(lid, "not interested")
            self._handled(reply, "not interested: closed", actor)
        else:
            moved = self._try_move(lid, "replied", f"reply R{rid}: {category}", actor)
            out += [moved] if moved else []
            out += self._cancel_pending(lid, "they replied", kinds=("followup",))
            out.append("your move: answer them yourself, then desk.py reply-done "
                       f"{rid} --note \"what you did\"")
        return out

    def sync_lead(self, lead: dict, dry_run: bool = False) -> list[str]:
        """Makes the follow-up timers of one lead right. Safe to run many times."""
        lid, status = lead["id"], lead["status"]
        pending = self.pending_follow_ups(lid)
        out: list[str] = []

        def cancel(rows, reason):
            for f in rows:
                if not dry_run:
                    self.store.update_follow_up(f["id"], {"status": "cancelled"})
                out.append(f"#{lid}: cancelled {f['kind']} due {str(f['due_on'])[:10]} ({reason})")

        if status in END_STATES:
            cancel(pending, f"lead is {status}")
            return out
        if status != "contacted":
            if status != "replied":
                cancel([f for f in pending if f["kind"] == "followup"], f"lead is {status}")
            else:
                cancel([f for f in pending if f["kind"] == "followup"], "they replied")
            return out
        sent = self.sent_messages(lid)
        if not sent:
            return out
        replies = self.store.replies_for(lid)
        followups = [f for f in pending if f["kind"] == "followup"]
        if any(not r.get("category") for r in replies):
            return out                       # paused until /sync sorts the reply
        new_contact = False
        bounce = next((r for r in replies if r.get("category") == "bounce" and not r.get("handled_at")), None)
        if bounce:
            person = self.recipient(lead) or {}
            email = person.get("email")
            if email and person.get("email_status") in EMAIL_OK and not self.is_blocked(email):
                new_contact = True
                if not dry_run:
                    self._handled(bounce, f"new contact P{person['id']} {email}", "code:followups")
                out.append(f"#{lid}: bounce solved, new contact {email}")
            else:
                cancel(followups, "bounced, no new contact yet")
                return out
        touches = len(sent)
        first, last = self.local_date(sent[0]["sent_at"]), self.local_date(sent[-1]["sent_at"])
        today = self.today()
        due = None if touches >= fu.max_touches(self.cadence, self.policy) else \
            fu.next_touch_due(first, last, touches, self.cadence, self.policy)
        if due is None:
            cancel(followups, "max touches")
            close_on = fu.no_response_on(last, self.cadence)
            if today >= close_on and not [f for f in pending if f["kind"] == "nurture"]:
                if not dry_run:
                    self.store.change_status(lid, "contacted", "no_response",
                                             f"no reply after {touches} messages", "code:followups",
                                             f"no reply after {touches} messages")
                out.append(f"#{lid}: contacted -> no_response (no reply after {touches} messages)")
            return out
        if new_contact:
            due = today
        want = touches + 1
        cancel([f for f in followups if f.get("touch_number") != want], "old touch")
        have = [f for f in followups if f.get("touch_number") == want]
        if new_contact and have:
            for f in have:
                if str(f["due_on"])[:10] > due.isoformat() and not dry_run:
                    self.store.update_follow_up(f["id"], {"due_on": due.isoformat()})
        if not have:
            if not dry_run:
                self.store.add_follow_up({"opportunity_id": lid, "due_on": due.isoformat(), "kind": "followup",
                                          "touch_number": want, "status": "pending"})
            out.append(f"#{lid}: follow-up {want} due {due}")
        return out

    def sync_followups(self, dry_run: bool = False) -> list[str]:
        """Timers for all leads: create the next follow-up, stop them, close leads with no reply."""
        pending = {f["opportunity_id"] for f in self.store.list_follow_ups("pending")}
        out: list[str] = []
        for lead in self.store.list_leads():
            if lead["status"] in FOLLOWUP_STATES or lead["id"] in pending:
                out += self.sync_lead(lead, dry_run=dry_run)
        return out


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


def message_id(text) -> int:
    """'12' or 'M12' -> 12."""
    try:
        return int(str(text).strip().upper().removeprefix("M"))
    except ValueError:
        raise DeskError(f"'{text}' is not a draft id (like 12 or M12)") from None


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
