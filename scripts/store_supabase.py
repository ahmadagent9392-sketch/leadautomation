"""Supabase store: the real database, reached over Supabase's REST API with httpx.

Tables and the two functions (add_lead, change_status) come from supabase/schema.sql.
The secret key is sent only in request headers. It is never printed or put in error messages.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from db import TABLES, ConfigError, DeskError, DuplicateLead, StatusConflict

# columns added after Stage 2: init reports them as missing until supabase/schema.sql is run again
NEW_COLUMNS = (("evidence", "topic"), ("opportunities", "rank_info"), ("replies", "gmail_message_id"),
               ("replies", "handled_at"), ("opportunities", "idea"))

LEAD_SELECT = "*,companies(name,domain)"


def _flatten(row: dict) -> dict:
    company = row.pop("companies", None) or {}
    row["company_name"] = company.get("name")
    row["company_domain"] = company.get("domain")
    return row


class SupabaseStore:
    def __init__(self, url: str, key: str, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # old-style JWT keys also need the Authorization header
            headers["Authorization"] = f"Bearer {key}"
        self.client = httpx.Client(base_url=url.rstrip("/") + "/rest/v1", headers=headers,
                                   timeout=20.0, transport=transport)

    def __repr__(self) -> str:  # never show the key
        return f"SupabaseStore({self.client.base_url})"

    def close(self) -> None:
        self.client.close()

    # ---------- low level ----------
    def _send(self, method: str, path: str, *, params=None, json=None, prefer: str | None = None) -> httpx.Response:
        headers = {"Prefer": prefer} if prefer else None
        try:
            resp = self.client.request(method, path, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise DeskError(f"cannot reach Supabase ({type(exc).__name__}). Check internet, SUPABASE_URL, "
                            "and that the project is not paused (supabase.com dashboard).") from exc
        if resp.status_code in (401, 403):
            raise ConfigError("Supabase refused the key. Check SUPABASE_SECRET_KEY in .env "
                              "(use the secret / service_role key).")
        return resp

    @staticmethod
    def _error(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _ok(self, resp: httpx.Response):
        if resp.status_code >= 400:
            err = self._error(resp)
            raise DeskError(f"Supabase error {resp.status_code}: {err.get('message') or resp.text[:200]}")
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def _get(self, table: str, params: list[tuple[str, str]]) -> list[dict]:
        return self._ok(self._send("GET", f"/{table}", params=params)) or []

    # ---------- store methods ----------
    def check(self) -> list[str]:
        missing = []
        for table in TABLES:
            resp = self._send("GET", f"/{table}", params=[("select", "*"), ("limit", "0")])
            if resp.status_code == 200:
                continue
            code = self._error(resp).get("code")
            if resp.status_code == 404 or code in ("PGRST205", "42P01"):
                missing.append(table)
            else:
                self._ok(resp)
        for table, column in NEW_COLUMNS:
            if table in missing:
                continue
            resp = self._send("GET", f"/{table}", params=[("select", column), ("limit", "0")])
            if resp.status_code != 200:
                missing.append(f"{table}.{column}")
        return missing

    def list_companies(self) -> list[dict]:
        return self._get("companies", [("select", "id,name,name_key,domain"), ("order", "id")])

    def find_lead_by_url(self, url: str) -> dict | None:
        rows = self._get("opportunities", [("select", "id"), ("source_url", f"eq.{url}")])
        return rows[0] if rows else None

    def create_lead(self, company: dict, lead: dict, actor: str) -> int:
        resp = self._send("POST", "/rpc/add_lead", json={
            "p_company_name": company["name"], "p_name_key": company["name_key"], "p_domain": company["domain"],
            "p_source_url": lead["source_url"], "p_note": lead["note"], "p_channel": lead["channel"],
            "p_actor": actor,
        })
        if resp.status_code == 409 or self._error(resp).get("code") == "23505":
            raise DuplicateLead("this company or URL is already in the database. Not added.")
        return int(self._ok(resp))

    def get_lead(self, lead_id: int) -> dict | None:
        rows = self._get("opportunities", [("select", LEAD_SELECT), ("id", f"eq.{lead_id}")])
        return _flatten(rows[0]) if rows else None

    def list_leads(self, status: str | None = None) -> list[dict]:
        params = [("select", LEAD_SELECT), ("order", "id")]
        if status:
            params.append(("status", f"eq.{status}"))
        return [_flatten(r) for r in self._get("opportunities", params)]

    def change_status(self, lead_id: int, old: str, new: str, reason: str, actor: str,
                      closed_reason: str | None) -> None:
        resp = self._send("POST", "/rpc/change_status", json={
            "p_lead_id": lead_id, "p_from": old, "p_to": new, "p_reason": reason,
            "p_actor": actor, "p_closed_reason": closed_reason,
        })
        if resp.status_code >= 400 and "status_changed" in str(self._error(resp).get("message")):
            raise StatusConflict(f"lead #{lead_id} changed while you were working. Run 'show {lead_id}' again.")
        self._ok(resp)

    def add_event(self, type_: str, actor: str, lead_id: int | None, payload: dict) -> None:
        self._ok(self._send("POST", "/events", prefer="return=minimal", json={
            "type": type_, "actor": actor, "opportunity_id": lead_id, "payload": payload}))

    def count_events(self, type_: str, since_iso: str) -> int:
        return len(self._get("events", [("select", "id"), ("type", f"eq.{type_}"), ("ts", f"gte.{since_iso}")]))

    def events_for(self, lead_id: int) -> list[dict]:
        return self._get("events", [("select", "*"), ("opportunity_id", f"eq.{lead_id}"), ("order", "id")])

    def people_for(self, company_id: int) -> list[dict]:
        return self._get("people", [("select", "*"), ("company_id", f"eq.{company_id}"), ("order", "id")])

    def evidence_for(self, lead_id: int) -> list[dict]:
        return self._get("evidence", [("select", "*"), ("opportunity_id", f"eq.{lead_id}"), ("order", "id")])

    def add_block(self, value: str, kind: str, reason: str) -> bool:
        rows = self._ok(self._send(
            "POST", "/suppression", params=[("on_conflict", "value")],
            prefer="resolution=ignore-duplicates,return=representation",
            json={"value": value, "kind": kind, "reason": reason}))
        return bool(rows)

    def get_block(self, value: str) -> dict | None:
        rows = self._get("suppression", [("select", "*"), ("value", f"eq.{value}")])
        return rows[0] if rows else None

    def list_blocks(self) -> list[dict]:
        return self._get("suppression", [("select", "*"), ("order", "id")])

    # ---------- research (Stage 3) ----------
    def _insert(self, table: str, row: dict) -> int:
        rows = self._ok(self._send("POST", f"/{table}", prefer="return=representation", json=row))
        return int(rows[0]["id"])

    def _update(self, table: str, column: str, value, fields: dict) -> None:
        resp = self._send("PATCH", f"/{table}", params=[(column, f"eq.{value}")], prefer="return=minimal",
                          json=fields)
        if resp.status_code == 409 or self._error(resp).get("code") == "23505":
            raise DuplicateLead("this value is already used by another row.")
        self._ok(resp)

    def add_snapshot(self, row: dict) -> bool:
        rows = self._ok(self._send(
            "POST", "/snapshots", params=[("on_conflict", "sha256")],
            prefer="resolution=ignore-duplicates,return=representation", json=row))
        return bool(rows)

    def get_snapshot(self, sha: str) -> dict | None:
        rows = self._get("snapshots", [("select", "*"), ("sha256", f"eq.{sha}")])
        return rows[0] if rows else None

    def add_evidence(self, row: dict) -> int:
        return self._insert("evidence", row)

    def get_evidence(self, evidence_id: int) -> dict | None:
        rows = self._get("evidence", [("select", "*"), ("id", f"eq.{evidence_id}")])
        return rows[0] if rows else None

    def update_evidence(self, evidence_id: int, fields: dict) -> None:
        self._update("evidence", "id", evidence_id, fields)

    def add_person(self, row: dict) -> int:
        return self._insert("people", row)

    def update_lead(self, lead_id: int, fields: dict) -> None:
        self._update("opportunities", "id", lead_id, {**fields, "updated_at": _now()})

    def update_company(self, company_id: int, fields: dict) -> None:
        self._update("companies", "id", company_id, {**fields, "updated_at": _now()})

    # ---------- drafts and approvals (Stage 5) ----------
    def add_message(self, row: dict) -> int:
        return self._insert("messages", row)

    def get_message(self, message_id: int) -> dict | None:
        rows = self._get("messages", [("select", "*"), ("id", f"eq.{message_id}")])
        return rows[0] if rows else None

    def update_message(self, message_id: int, fields: dict) -> None:
        self._update("messages", "id", message_id, fields)

    def messages_for(self, lead_id: int) -> list[dict]:
        return self._get("messages", [("select", "*"), ("opportunity_id", f"eq.{lead_id}"), ("order", "id")])

    def add_approval(self, row: dict) -> int:
        return self._insert("approvals", row)

    def approvals_for(self, object_type: str, object_id: int) -> list[dict]:
        return self._get("approvals", [("select", "*"), ("object_type", f"eq.{object_type}"),
                                       ("object_id", f"eq.{object_id}"), ("order", "id")])

    def list_messages(self) -> list[dict]:
        return self._get("messages", [("select", "*"), ("order", "id")])

    def events_since(self, type_: str, since_iso: str) -> list[dict]:
        return self._get("events", [("select", "*"), ("type", f"eq.{type_}"), ("ts", f"gte.{since_iso}"),
                                    ("order", "id")])

    # ---------- replies and follow-ups (Stage 6) ----------
    def update_person(self, person_id: int, fields: dict) -> None:
        self._update("people", "id", person_id, fields)

    def add_reply(self, row: dict) -> int:
        resp = self._send("POST", "/replies", prefer="return=representation", json=row)
        if resp.status_code == 409 or self._error(resp).get("code") == "23505":
            raise DuplicateLead("this reply is already saved.")
        return int(self._ok(resp)[0]["id"])

    def get_reply(self, reply_id: int) -> dict | None:
        rows = self._get("replies", [("select", "*"), ("id", f"eq.{reply_id}")])
        return rows[0] if rows else None

    def find_reply_by_gmail_id(self, gmail_message_id: str) -> dict | None:
        rows = self._get("replies", [("select", "*"), ("gmail_message_id", f"eq.{gmail_message_id}")])
        return rows[0] if rows else None

    def update_reply(self, reply_id: int, fields: dict) -> None:
        self._update("replies", "id", reply_id, fields)

    def replies_for(self, lead_id: int) -> list[dict]:
        return self._get("replies", [("select", "*"), ("opportunity_id", f"eq.{lead_id}"), ("order", "id")])

    def list_replies(self) -> list[dict]:
        return self._get("replies", [("select", "*"), ("order", "id")])

    def add_follow_up(self, row: dict) -> int:
        return self._insert("follow_ups", row)

    def update_follow_up(self, follow_up_id: int, fields: dict) -> None:
        self._update("follow_ups", "id", follow_up_id, fields)

    def follow_ups_for(self, lead_id: int) -> list[dict]:
        return self._get("follow_ups", [("select", "*"), ("opportunity_id", f"eq.{lead_id}"), ("order", "id")])

    def list_follow_ups(self, status: str | None = None) -> list[dict]:
        params = [("select", "*"), ("order", "due_on,id")]
        if status:
            params.append(("status", f"eq.{status}"))
        return self._get("follow_ups", params)

    # ---------- found posts and places (Stage 7) ----------
    def add_raw_item(self, row: dict) -> int | None:
        rows = self._ok(self._send(
            "POST", "/raw_items", params=[("on_conflict", "url")],
            prefer="resolution=ignore-duplicates,return=representation", json=row))
        return int(rows[0]["id"]) if rows else None

    def get_raw_item(self, raw_id: int) -> dict | None:
        rows = self._get("raw_items", [("select", "*"), ("id", f"eq.{raw_id}")])
        return rows[0] if rows else None

    def find_raw_by_url(self, url: str) -> dict | None:
        rows = self._get("raw_items", [("select", "*"), ("url", f"eq.{url}")])
        return rows[0] if rows else None

    def list_raw_items(self, status: str | None = None, source: str | None = None,
                       idea: str | None = None) -> list[dict]:
        params = [("select", "*"), ("order", "id")]
        for col, val in (("status", status), ("source", source), ("idea", idea)):
            if val:
                params.append((col, f"eq.{val}"))
        return self._get("raw_items", params)

    def update_raw_item(self, raw_id: int, fields: dict) -> None:
        self._update("raw_items", "id", raw_id, fields)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
