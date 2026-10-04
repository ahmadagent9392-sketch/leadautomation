"""Tests for scripts/store_supabase.py with a fake Supabase server (httpx.MockTransport, no internet)."""
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
from store_supabase import SupabaseStore  # noqa: E402

URL = "https://demo.supabase.co"
KEY = "sb_secret_TESTKEY123"


class FakeSupabase:
    """Answers like PostgREST. `routes` maps 'METHOD /path' to (status, body)."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes.get(f"{request.method} {request.url.path}", (200, []))
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)


def make(routes=None, key=KEY):
    fake = FakeSupabase(routes)
    return SupabaseStore(URL, key, transport=httpx.MockTransport(fake)), fake


def test_headers_new_key_only_apikey():
    store, fake = make()
    store.list_companies()
    req = fake.requests[0]
    assert req.headers["apikey"] == KEY
    assert "authorization" not in req.headers
    assert req.url.path == "/rest/v1/companies"
    assert KEY not in repr(store)


def test_headers_old_jwt_key_adds_bearer():
    store, fake = make(key="eyJhbGciOi.fake.jwt")
    store.list_companies()
    assert fake.requests[0].headers["authorization"] == "Bearer eyJhbGciOi.fake.jwt"


def test_check_finds_missing_tables():
    store, _ = make({"GET /rest/v1/events": (404, {"code": "PGRST205", "message": "not found"})})
    assert store.check() == ["events"]


def test_bad_key_gives_clear_message_without_key():
    store, _ = make({"GET /rest/v1/companies": (401, {"message": "Invalid API key"})})
    with pytest.raises(db.ConfigError) as err:
        store.list_companies()
    assert "SUPABASE_SECRET_KEY" in str(err.value)
    assert KEY not in str(err.value)


def test_no_internet_gives_clear_message():
    def boom(request):
        raise httpx.ConnectError("no route")
    store = SupabaseStore(URL, KEY, transport=httpx.MockTransport(boom))
    with pytest.raises(db.DeskError, match="cannot reach Supabase"):
        store.list_companies()


def test_create_lead_calls_rpc():
    store, fake = make({"POST /rest/v1/rpc/add_lead": (200, 7)})
    lead_id = store.create_lead({"name": "Alpha", "name_key": "alpha", "domain": "alpha.com"},
                                {"source_url": "https://alpha.com", "note": "n", "channel": "email"}, "human")
    assert lead_id == 7
    body = json.loads(fake.requests[0].content)
    assert body["p_domain"] == "alpha.com" and body["p_channel"] == "email" and body["p_actor"] == "human"


def test_create_lead_unique_violation_is_duplicate():
    store, _ = make({"POST /rest/v1/rpc/add_lead": (409, {"code": "23505", "message": "duplicate key"})})
    with pytest.raises(db.DuplicateLead):
        store.create_lead({"name": "A", "name_key": "a", "domain": "a.com"},
                          {"source_url": "https://a.com", "note": "", "channel": "email"}, "human")


def test_change_status_calls_rpc():
    store, fake = make({"POST /rest/v1/rpc/change_status": (204, None)})
    store.change_status(3, "new", "researched", "done", "human", None)
    body = json.loads(fake.requests[0].content)
    assert body == {"p_lead_id": 3, "p_from": "new", "p_to": "researched", "p_reason": "done",
                    "p_actor": "human", "p_closed_reason": None}


def test_change_status_conflict():
    store, _ = make({"POST /rest/v1/rpc/change_status": (400, {"code": "P0001", "message": "status_changed"})})
    with pytest.raises(db.StatusConflict):
        store.change_status(3, "new", "researched", "x", "human", None)


def test_get_lead_flattens_company():
    row = {"id": 1, "status": "new", "companies": {"name": "Alpha", "domain": "alpha.com"}}
    store, fake = make({"GET /rest/v1/opportunities": (200, [row])})
    lead = store.get_lead(1)
    assert lead["company_name"] == "Alpha" and lead["company_domain"] == "alpha.com"
    assert fake.requests[0].url.params["id"] == "eq.1"


def test_count_events_filters():
    store, fake = make({"GET /rest/v1/events": (200, [{"id": 1}, {"id": 2}])})
    assert store.count_events("lead_added", "2026-10-03T19:00:00+00:00") == 2
    params = fake.requests[0].url.params
    assert params["type"] == "eq.lead_added"
    assert params["ts"] == "gte.2026-10-03T19:00:00+00:00"


def test_add_block_new_and_existing():
    store, fake = make({"POST /rest/v1/suppression": (201, [{"value": "spam.com"}])})
    assert store.add_block("spam.com", "domain", "manual") is True
    assert "ignore-duplicates" in fake.requests[0].headers["prefer"]
    store, _ = make({"POST /rest/v1/suppression": (201, [])})
    assert store.add_block("spam.com", "domain", "manual") is False


def test_other_errors_show_message():
    store, _ = make({"GET /rest/v1/companies": (500, {"message": "boom"})})
    with pytest.raises(db.DeskError, match="Supabase error 500: boom"):
        store.list_companies()


# ---------- Stage 3 ----------
def test_check_finds_missing_new_column():
    fake = FakeSupabase()

    def handler(request):
        if request.url.path == "/rest/v1/evidence" and request.url.params.get("select") == "topic":
            return httpx.Response(400, json={"code": "42703", "message": "column evidence.topic does not exist"})
        return fake(request)
    store = SupabaseStore(URL, KEY, transport=httpx.MockTransport(handler))
    assert store.check() == ["evidence.topic"]


def test_add_snapshot_ignores_duplicates():
    store, fake = make({"POST /rest/v1/snapshots": (201, [{"sha256": "a"}])})
    assert store.add_snapshot({"sha256": "a", "url": "https://x.com", "text_path": "p"}) is True
    req = fake.requests[0]
    assert req.url.params["on_conflict"] == "sha256" and "ignore-duplicates" in req.headers["prefer"]
    store, _ = make({"POST /rest/v1/snapshots": (201, [])})
    assert store.add_snapshot({"sha256": "a", "url": "https://x.com", "text_path": "p"}) is False


def test_add_evidence_returns_id():
    store, fake = make({"POST /rest/v1/evidence": (201, [{"id": 12}])})
    assert store.add_evidence({"opportunity_id": 1, "claim": "c", "depends_on": [3]}) == 12
    assert json.loads(fake.requests[0].content)["depends_on"] == [3]
    assert "return=representation" in fake.requests[0].headers["prefer"]


def test_update_evidence_patches_one_row():
    store, fake = make({"PATCH /rest/v1/evidence": (204, None)})
    store.update_evidence(12, {"grade": "UNKNOWN", "verified": True})
    req = fake.requests[0]
    assert req.method == "PATCH" and req.url.params["id"] == "eq.12"
    assert json.loads(req.content) == {"grade": "UNKNOWN", "verified": True}


def test_update_lead_sets_updated_at():
    store, fake = make({"PATCH /rest/v1/opportunities": (204, None)})
    store.update_lead(4, {"why_now": "hiring", "unknowns": ["a"]})
    body = json.loads(fake.requests[0].content)
    assert body["why_now"] == "hiring" and body["unknowns"] == ["a"] and "updated_at" in body


def test_update_company_duplicate_domain():
    store, _ = make({"PATCH /rest/v1/companies": (409, {"code": "23505", "message": "duplicate key"})})
    with pytest.raises(db.DuplicateLead):
        store.update_company(2, {"domain": "alpha.com"})


def test_get_snapshot_and_evidence():
    store, fake = make({"GET /rest/v1/snapshots": (200, [{"sha256": "a"}]), "GET /rest/v1/evidence": (200, [])})
    assert store.get_snapshot("a") == {"sha256": "a"}
    assert fake.requests[0].url.params["sha256"] == "eq.a"
    assert store.get_evidence(5) is None


def test_add_person_returns_id():
    store, _ = make({"POST /rest/v1/people": (201, [{"id": 3}])})
    assert store.add_person({"company_id": 1, "title": "Owner"}) == 3


# ---------- Stage 4 ----------
def test_check_finds_missing_rank_info_column():
    fake = FakeSupabase()

    def handler(request):
        if request.url.path == "/rest/v1/opportunities" and request.url.params.get("select") == "rank_info":
            return httpx.Response(400, json={"code": "42703", "message": "column does not exist"})
        return fake(request)
    store = SupabaseStore(URL, KEY, transport=httpx.MockTransport(handler))
    assert store.check() == ["opportunities.rank_info"]


def test_update_lead_sends_rank_info_as_json():
    store, fake = make({"PATCH /rest/v1/opportunities": (204, None)})
    store.update_lead(4, {"fit": 3, "value_band": 2, "priority": 24,
                          "rank_info": {"why": "x", "fails": [], "factors": {"fit": {"value": 3}}}})
    body = json.loads(fake.requests[0].content)
    assert body["priority"] == 24 and body["rank_info"]["factors"]["fit"]["value"] == 3


# ---------- drafts and approvals (Stage 5) ----------
def test_add_message_returns_id_and_sends_json():
    store, fake = make({"POST /rest/v1/messages": (201, [{"id": 4}])})
    mid = store.add_message({"opportunity_id": 1, "direction": "out", "body": "hi", "evidence_ids": [1, 2]})
    assert mid == 4
    req = fake.requests[0]
    assert req.headers["prefer"] == "return=representation"
    assert json.loads(req.content)["evidence_ids"] == [1, 2]


def test_get_message_and_messages_for():
    store, fake = make({"GET /rest/v1/messages": (200, [{"id": 4, "body": "hi"}])})
    assert store.get_message(4)["body"] == "hi"
    assert ("id", "eq.4") in list(fake.requests[0].url.params.multi_items())
    assert store.messages_for(1)[0]["id"] == 4
    params = list(fake.requests[1].url.params.multi_items())
    assert ("opportunity_id", "eq.1") in params and ("order", "id") in params


def test_get_message_missing():
    store, _ = make({"GET /rest/v1/messages": (200, [])})
    assert store.get_message(9) is None


def test_update_message_patches_one_row():
    store, fake = make({"PATCH /rest/v1/messages": (204, None)})
    store.update_message(4, {"gmail_draft_id": "r-1", "critic": {"verdict": "REWRITE"}})
    req = fake.requests[0]
    assert req.method == "PATCH" and ("id", "eq.4") in list(req.url.params.multi_items())
    assert json.loads(req.content) == {"gmail_draft_id": "r-1", "critic": {"verdict": "REWRITE"}}


def test_approvals():
    store, fake = make({"POST /rest/v1/approvals": (201, [{"id": 2}]),
                        "GET /rest/v1/approvals": (200, [{"id": 2, "decision": "approved"}])})
    assert store.add_approval({"object_type": "message", "object_id": 4, "body_sha256": "ab",
                               "decision": "approved", "reason": "ok"}) == 2
    assert store.approvals_for("message", 4)[0]["decision"] == "approved"
    params = list(fake.requests[1].url.params.multi_items())
    assert ("object_type", "eq.message") in params and ("object_id", "eq.4") in params


# ---------- replies and follow-ups (Stage 6) ----------
def test_check_finds_missing_stage6_reply_columns():
    fake = FakeSupabase()

    def handler(request):
        if request.url.path == "/rest/v1/replies" and request.url.params.get("select") in ("gmail_message_id",
                                                                                            "handled_at"):
            return httpx.Response(400, json={"code": "42703", "message": "column does not exist"})
        return fake(request)
    store = SupabaseStore(URL, KEY, transport=httpx.MockTransport(handler))
    assert store.check() == ["replies.gmail_message_id", "replies.handled_at"]


def test_add_reply_and_duplicate():
    store, fake = make({"POST /rest/v1/replies": (201, [{"id": 7}])})
    assert store.add_reply({"opportunity_id": 1, "body": "no", "objections": [], "gmail_message_id": "g-1"}) == 7
    assert json.loads(fake.requests[0].content)["gmail_message_id"] == "g-1"
    store, _ = make({"POST /rest/v1/replies": (409, {"code": "23505", "message": "duplicate key"})})
    with pytest.raises(db.DuplicateLead):
        store.add_reply({"opportunity_id": 1, "body": "no"})


def test_reply_reads_and_update():
    store, fake = make({"GET /rest/v1/replies": (200, [{"id": 7, "body": "hi"}]),
                        "PATCH /rest/v1/replies": (204, None)})
    assert store.get_reply(7)["id"] == 7
    assert store.find_reply_by_gmail_id("g-1")["id"] == 7
    assert ("gmail_message_id", "eq.g-1") in list(fake.requests[1].url.params.multi_items())
    assert store.replies_for(3)[0]["body"] == "hi"
    assert ("opportunity_id", "eq.3") in list(fake.requests[2].url.params.multi_items())
    assert store.list_replies()
    store.update_reply(7, {"category": "positive", "objections": ["price"]})
    assert json.loads(fake.requests[4].content) == {"category": "positive", "objections": ["price"]}


def test_follow_up_rows():
    store, fake = make({"POST /rest/v1/follow_ups": (201, [{"id": 5}]),
                        "GET /rest/v1/follow_ups": (200, [{"id": 5, "status": "pending"}]),
                        "PATCH /rest/v1/follow_ups": (204, None)})
    assert store.add_follow_up({"opportunity_id": 1, "due_on": "2026-10-07", "kind": "followup",
                                "touch_number": 2, "status": "pending"}) == 5
    assert store.follow_ups_for(1)[0]["id"] == 5
    store.list_follow_ups("pending")
    params = list(fake.requests[2].url.params.multi_items())
    assert ("status", "eq.pending") in params and ("order", "due_on,id") in params
    store.update_follow_up(5, {"status": "done"})
    assert ("id", "eq.5") in list(fake.requests[3].url.params.multi_items())


def test_update_person_list_messages_events_since():
    store, fake = make({"PATCH /rest/v1/people": (204, None), "GET /rest/v1/messages": (200, [{"id": 1}]),
                        "GET /rest/v1/events": (200, [{"id": 2}])})
    store.update_person(4, {"email_status": "invalid"})
    assert json.loads(fake.requests[0].content) == {"email_status": "invalid"}
    assert store.list_messages() == [{"id": 1}]
    assert store.events_since("status_changed", "2026-10-01T00:00:00+00:00") == [{"id": 2}]
    params = list(fake.requests[2].url.params.multi_items())
    assert ("type", "eq.status_changed") in params and ("ts", "gte.2026-10-01T00:00:00+00:00") in params
