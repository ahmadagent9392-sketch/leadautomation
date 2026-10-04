"""SQLite store: a local database file. Used by pytest and later by --demo mode (data/demo.db).

The tables are the same as supabase/schema.sql. If you change one, change both.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db import TABLES, DuplicateLead, StatusConflict

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, name_key TEXT NOT NULL DEFAULT '', domain TEXT UNIQUE,
  country TEXT, industry TEXT, size_band TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  source_url TEXT UNIQUE, note TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL CHECK (channel IN
    ('email','linkedin_message','upwork_proposal','agency_pitch','referral_ask')),
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN
    ('new','researched','verified','qualified','draft_ready','approved','contacted',
     'replied','meeting','proposal','won','lost','no_response','rejected','opted_out')),
  pattern_id TEXT,
  fit INTEGER CHECK (fit BETWEEN 0 AND 3),
  value_band INTEGER CHECK (value_band BETWEEN 1 AND 3),
  urgency INTEGER CHECK (urgency BETWEEN 0 AND 2),
  priority INTEGER, owner_person_id INTEGER, why_now TEXT,
  unknowns TEXT NOT NULL DEFAULT '[]', rank_info TEXT NOT NULL DEFAULT '{}', closed_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  sha256 TEXT PRIMARY KEY, url TEXT NOT NULL, fetched_at TEXT NOT NULL,
  http_status INTEGER, text_path TEXT NOT NULL, title TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  claim TEXT NOT NULL, url TEXT NOT NULL, quote TEXT NOT NULL,
  snapshot_sha256 TEXT REFERENCES snapshots(sha256), observed_at TEXT,
  source_type TEXT CHECK (source_type IN
    ('job_post','help_request','review','website','news','profile','manual')),
  topic TEXT NOT NULL DEFAULT 'company' CHECK (topic IN
    ('pain','company','why_now','owner','contact','impact')),
  grade TEXT NOT NULL CHECK (grade IN
    ('CONFIRMED_FACT','STRONG_SIGNAL','WEAK_SIGNAL','INFERENCE','UNKNOWN')),
  depends_on TEXT NOT NULL DEFAULT '[]', verified INTEGER NOT NULL DEFAULT 0,
  verifier_note TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  full_name TEXT, title TEXT,
  role_type TEXT CHECK (role_type IN ('owner','technical','buyer','influencer')),
  email TEXT,
  email_status TEXT CHECK (email_status IN ('published','verified','inferred','invalid')),
  profile_url TEXT, evidence_id INTEGER REFERENCES evidence(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  touch_number INTEGER NOT NULL DEFAULT 1,
  direction TEXT NOT NULL CHECK (direction IN ('out','in')),
  channel TEXT, subject TEXT, body TEXT NOT NULL, angle TEXT, cta_type TEXT,
  evidence_ids TEXT NOT NULL DEFAULT '[]', critic TEXT,
  gmail_draft_id TEXT, gmail_message_id TEXT, thread_id TEXT, sent_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER REFERENCES messages(id),
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  body TEXT NOT NULL, category TEXT, objections TEXT NOT NULL DEFAULT '[]',
  requested_action TEXT, follow_up_date TEXT, classified_at TEXT, created_at TEXT NOT NULL,
  gmail_message_id TEXT, sender TEXT, subject TEXT, next_action TEXT, note TEXT, handled_at TEXT
);
CREATE TABLE IF NOT EXISTS follow_ups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
  due_on TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('followup','nurture','stale_check')),
  touch_number INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','cancelled')),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_type TEXT NOT NULL, object_id INTEGER NOT NULL, body_sha256 TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','edited','rejected')),
  reason TEXT, decided_at TEXT NOT NULL, expires_at TEXT
);
CREATE TABLE IF NOT EXISTS suppression (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  value TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('email','domain')),
  reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, actor TEXT NOT NULL,
  opportunity_id INTEGER REFERENCES opportunities(id),
  type TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS raw_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL CHECK (source IN ('hn','jobs','agency','gmaps','web','manual','linkedin')),
  url TEXT NOT NULL UNIQUE, title TEXT, text TEXT NOT NULL DEFAULT '', posted_at TEXT,
  found_at TEXT NOT NULL, query TEXT, idea TEXT,
  matched TEXT NOT NULL DEFAULT '{}', extra TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','kept','rejected','expired')),
  reason TEXT, lead_id INTEGER REFERENCES opportunities(id)
);
CREATE INDEX IF NOT EXISTS raw_items_status_idx ON raw_items(status, source);
CREATE INDEX IF NOT EXISTS opportunities_status_idx ON opportunities(status);
CREATE INDEX IF NOT EXISTS events_opportunity_idx ON events(opportunity_id);
CREATE INDEX IF NOT EXISTS events_type_ts_idx ON events(type, ts);
CREATE INDEX IF NOT EXISTS follow_ups_due_idx ON follow_ups(due_on, status);
CREATE INDEX IF NOT EXISTS people_email_idx ON people(email);
CREATE INDEX IF NOT EXISTS replies_opportunity_idx ON replies(opportunity_id);
CREATE INDEX IF NOT EXISTS follow_ups_opportunity_idx ON follow_ups(opportunity_id);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'events table is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'events table is append-only'); END;
"""

LEAD_SELECT = """
SELECT o.*, c.name AS company_name, c.domain AS company_domain
FROM opportunities o JOIN companies c ON c.id = o.company_id
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


JSON_COLUMNS = ("depends_on", "unknowns", "payload", "rank_info", "evidence_ids", "critic", "objections",
                "matched", "extra")


def _decode(row: dict | None) -> dict | None:
    """JSON text columns -> Python lists/dicts, like Supabase returns them."""
    if row is None:
        return None
    for col in JSON_COLUMNS:
        if isinstance(row.get(col), str):
            row[col] = json.loads(row[col] or "null")
    if "verified" in row:
        row["verified"] = bool(row["verified"])
    return row


def _encode(fields: dict) -> dict:
    return {k: json.dumps(v) if k in JSON_COLUMNS else (int(v) if isinstance(v, bool) else v)
            for k, v in fields.items()}


class SqliteStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._upgrade()
        self.conn.execute("PRAGMA user_version = 8")

    def _upgrade(self) -> None:
        """Adds columns that newer stages need to an older database file. Never deletes data."""
        cols = {r["name"] for r in self._all("PRAGMA table_info(evidence)")}
        if "topic" not in cols:   # Stage 3
            with self.conn:
                self.conn.execute("ALTER TABLE evidence ADD COLUMN topic TEXT NOT NULL DEFAULT 'company' "
                                  "CHECK (topic IN ('pain','company','why_now','owner','contact','impact'))")
        cols = {r["name"] for r in self._all("PRAGMA table_info(opportunities)")}
        if "rank_info" not in cols:   # Stage 4
            with self.conn:
                self.conn.execute("ALTER TABLE opportunities ADD COLUMN rank_info TEXT NOT NULL DEFAULT '{}'")
        cols = {r["name"] for r in self._all("PRAGMA table_info(replies)")}
        with self.conn:   # Stage 6
            for col in ("gmail_message_id", "sender", "subject", "next_action", "note", "handled_at"):
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE replies ADD COLUMN {col} TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS replies_gmail_message_id_idx "
                              "ON replies(gmail_message_id)")
        cols = {r["name"] for r in self._all("PRAGMA table_info(opportunities)")}
        if "idea" not in cols:   # Stage 7
            with self.conn:
                self.conn.execute("ALTER TABLE opportunities ADD COLUMN idea TEXT")
        sql = (self._one("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'raw_items'") or {}).get("sql", "")
        if sql and "'linkedin'" not in sql:   # Stage 7b: new source 'linkedin' (copy rows into the new table)
            with self.conn:
                self.conn.execute("PRAGMA foreign_keys = OFF")
                self.conn.execute("ALTER TABLE raw_items RENAME TO raw_items_before_7b")
                self.conn.executescript(SCHEMA)
                self.conn.execute("INSERT INTO raw_items SELECT * FROM raw_items_before_7b")
                self.conn.execute("DROP TABLE raw_items_before_7b")
                self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _event(self, type_: str, actor: str, lead_id: int | None, payload: dict) -> None:
        self.conn.execute("INSERT INTO events(ts, actor, opportunity_id, type, payload) VALUES (?,?,?,?,?)",
                          (_now(), actor, lead_id, type_, json.dumps(payload)))

    def check(self) -> list[str]:
        names = {r["name"] for r in self._all("SELECT name FROM sqlite_master WHERE type='table'")}
        return [t for t in TABLES if t not in names]

    def list_companies(self) -> list[dict]:
        return self._all("SELECT id, name, name_key, domain FROM companies ORDER BY id")

    def find_lead_by_url(self, url: str) -> dict | None:
        return self._one("SELECT id FROM opportunities WHERE source_url = ?", (url,))

    def create_lead(self, company: dict, lead: dict, actor: str) -> int:
        ts = _now()
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO companies(name, name_key, domain, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (company["name"], company["name_key"], company["domain"], ts, ts))
                cur = self.conn.execute(
                    "INSERT INTO opportunities(company_id, source_url, note, channel, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (cur.lastrowid, lead["source_url"], lead["note"], lead["channel"], ts, ts))
                lead_id = cur.lastrowid
                self._event("lead_added", actor, lead_id, {"url": lead["source_url"], "channel": lead["channel"],
                                                           "company": company["name"]})
        except sqlite3.IntegrityError as exc:
            raise DuplicateLead(f"this company or URL is already in the database ({exc}). Not added.") from exc
        return lead_id

    def get_lead(self, lead_id: int) -> dict | None:
        return _decode(self._one(LEAD_SELECT + " WHERE o.id = ?", (lead_id,)))

    def list_leads(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._all(LEAD_SELECT + " WHERE o.status = ? ORDER BY o.id", (status,))
        else:
            rows = self._all(LEAD_SELECT + " ORDER BY o.id")
        return [_decode(r) for r in rows]

    def change_status(self, lead_id: int, old: str, new: str, reason: str, actor: str,
                      closed_reason: str | None) -> None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE opportunities SET status = ?, closed_reason = COALESCE(?, closed_reason), updated_at = ? "
                "WHERE id = ? AND status = ?", (new, closed_reason, _now(), lead_id, old))
            if cur.rowcount != 1:
                raise StatusConflict(f"lead #{lead_id} changed while you were working. Run 'show {lead_id}' again.")
            self._event("status_changed", actor, lead_id, {"from": old, "to": new, "reason": reason})

    def add_event(self, type_: str, actor: str, lead_id: int | None, payload: dict) -> None:
        with self.conn:
            self._event(type_, actor, lead_id, payload)

    def count_events(self, type_: str, since_iso: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM events WHERE type = ? AND ts >= ?", (type_, since_iso))
        return int(row["n"]) if row else 0

    def events_for(self, lead_id: int) -> list[dict]:
        rows = self._all("SELECT * FROM events WHERE opportunity_id = ? ORDER BY id", (lead_id,))
        for r in rows:
            r["payload"] = json.loads(r["payload"] or "{}")
        return rows

    def people_for(self, company_id: int) -> list[dict]:
        return self._all("SELECT * FROM people WHERE company_id = ? ORDER BY id", (company_id,))

    def evidence_for(self, lead_id: int) -> list[dict]:
        return [_decode(r) for r in self._all("SELECT * FROM evidence WHERE opportunity_id = ? ORDER BY id",
                                              (lead_id,))]

    def add_block(self, value: str, kind: str, reason: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO suppression(value, kind, reason, created_at) VALUES (?,?,?,?)",
                (value, kind, reason, _now()))
        return cur.rowcount == 1

    def get_block(self, value: str) -> dict | None:
        return self._one("SELECT * FROM suppression WHERE value = ?", (value,))

    def list_blocks(self) -> list[dict]:
        return self._all("SELECT * FROM suppression ORDER BY id")

    # ---------- research (Stage 3) ----------
    def _insert(self, table: str, row: dict) -> int:
        row = _encode(row)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.conn:
            cur = self.conn.execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(row.values()))
        return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, fields: dict) -> None:
        fields = _encode(fields)
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            with self.conn:
                self.conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))
        except sqlite3.IntegrityError as exc:
            raise DuplicateLead(f"this value is already used by another row ({exc}).") from exc

    def add_snapshot(self, row: dict) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO snapshots(sha256, url, fetched_at, http_status, text_path, title) "
                "VALUES (?,?,?,?,?,?)",
                (row["sha256"], row["url"], _now(), row.get("http_status"), row["text_path"], row.get("title")))
        return cur.rowcount == 1

    def get_snapshot(self, sha: str) -> dict | None:
        return self._one("SELECT * FROM snapshots WHERE sha256 = ?", (sha,))

    def add_evidence(self, row: dict) -> int:
        return self._insert("evidence", {**row, "created_at": _now()})

    def get_evidence(self, evidence_id: int) -> dict | None:
        return _decode(self._one("SELECT * FROM evidence WHERE id = ?", (evidence_id,)))

    def update_evidence(self, evidence_id: int, fields: dict) -> None:
        self._update("evidence", evidence_id, fields)

    def add_person(self, row: dict) -> int:
        return self._insert("people", {**row, "created_at": _now()})

    def update_lead(self, lead_id: int, fields: dict) -> None:
        self._update("opportunities", lead_id, {**fields, "updated_at": _now()})

    def update_company(self, company_id: int, fields: dict) -> None:
        self._update("companies", company_id, {**fields, "updated_at": _now()})

    # ---------- drafts and approvals (Stage 5) ----------
    def add_message(self, row: dict) -> int:
        return self._insert("messages", {**row, "created_at": _now()})

    def get_message(self, message_id: int) -> dict | None:
        return _decode(self._one("SELECT * FROM messages WHERE id = ?", (message_id,)))

    def update_message(self, message_id: int, fields: dict) -> None:
        self._update("messages", message_id, fields)

    def messages_for(self, lead_id: int) -> list[dict]:
        return [_decode(r) for r in self._all("SELECT * FROM messages WHERE opportunity_id = ? ORDER BY id",
                                              (lead_id,))]

    def add_approval(self, row: dict) -> int:
        return self._insert("approvals", {**row, "decided_at": _now()})

    def approvals_for(self, object_type: str, object_id: int) -> list[dict]:
        return self._all("SELECT * FROM approvals WHERE object_type = ? AND object_id = ? ORDER BY id",
                         (object_type, object_id))

    def list_messages(self) -> list[dict]:
        return [_decode(r) for r in self._all("SELECT * FROM messages ORDER BY id")]

    def events_since(self, type_: str, since_iso: str) -> list[dict]:
        rows = self._all("SELECT * FROM events WHERE type = ? AND ts >= ? ORDER BY id", (type_, since_iso))
        return [_decode(r) for r in rows]

    # ---------- replies and follow-ups (Stage 6) ----------
    def update_person(self, person_id: int, fields: dict) -> None:
        self._update("people", person_id, fields)

    def add_reply(self, row: dict) -> int:
        try:
            return self._insert("replies", {**row, "created_at": _now()})
        except sqlite3.IntegrityError as exc:
            raise DuplicateLead(f"this reply is already saved ({exc}).") from exc

    def get_reply(self, reply_id: int) -> dict | None:
        return _decode(self._one("SELECT * FROM replies WHERE id = ?", (reply_id,)))

    def find_reply_by_gmail_id(self, gmail_message_id: str) -> dict | None:
        return _decode(self._one("SELECT * FROM replies WHERE gmail_message_id = ?", (gmail_message_id,)))

    def update_reply(self, reply_id: int, fields: dict) -> None:
        self._update("replies", reply_id, fields)

    def replies_for(self, lead_id: int) -> list[dict]:
        return [_decode(r) for r in self._all("SELECT * FROM replies WHERE opportunity_id = ? ORDER BY id",
                                              (lead_id,))]

    def list_replies(self) -> list[dict]:
        return [_decode(r) for r in self._all("SELECT * FROM replies ORDER BY id")]

    def add_follow_up(self, row: dict) -> int:
        return self._insert("follow_ups", {**row, "created_at": _now()})

    def update_follow_up(self, follow_up_id: int, fields: dict) -> None:
        self._update("follow_ups", follow_up_id, fields)

    def follow_ups_for(self, lead_id: int) -> list[dict]:
        return self._all("SELECT * FROM follow_ups WHERE opportunity_id = ? ORDER BY id", (lead_id,))

    def list_follow_ups(self, status: str | None = None) -> list[dict]:
        if status:
            return self._all("SELECT * FROM follow_ups WHERE status = ? ORDER BY due_on, id", (status,))
        return self._all("SELECT * FROM follow_ups ORDER BY due_on, id")

    # ---------- found posts and places (Stage 7) ----------
    def add_raw_item(self, row: dict) -> int | None:
        row = _encode({**row, "found_at": row.get("found_at") or _now()})
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.conn:
            cur = self.conn.execute(f"INSERT OR IGNORE INTO raw_items({cols}) VALUES ({marks})", tuple(row.values()))
        return int(cur.lastrowid) if cur.rowcount == 1 else None

    def get_raw_item(self, raw_id: int) -> dict | None:
        return _decode(self._one("SELECT * FROM raw_items WHERE id = ?", (raw_id,)))

    def find_raw_by_url(self, url: str) -> dict | None:
        return _decode(self._one("SELECT * FROM raw_items WHERE url = ?", (url,)))

    def list_raw_items(self, status: str | None = None, source: str | None = None,
                       idea: str | None = None) -> list[dict]:
        where, args = [], []
        for col, val in (("status", status), ("source", source), ("idea", idea)):
            if val:
                where.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT * FROM raw_items" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        return [_decode(r) for r in self._all(sql, tuple(args))]

    def update_raw_item(self, raw_id: int, fields: dict) -> None:
        self._update("raw_items", raw_id, fields)
