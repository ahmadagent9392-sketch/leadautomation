"""Tests for Stage 5: drafts, critic review, Ahmad's approval and export (SQLite, no internet, no Gmail)."""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import checks as ck  # noqa: E402
import db  # noqa: E402
import desk as desk_cli  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)          # 11:00 in Karachi
TOMORROW = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)
URL = "https://brightsmile.com/careers"
PAGE = """Bright Smile Dental - Careers
We are hiring a Front Desk Coordinator.
Our phones ring all day and we can't keep up with patient calls and web form inquiries.
Contact our office manager Sara Khan at sara@brightsmile.com
Posted: September 20, 2026"""
BODY = ("Hi Sara, I saw your Sept 20 post: your phones ring all day and you can't keep up with patient calls. "
        "A small auto-reply and call-back list could catch those requests for you. "
        "Want me to send a 2-minute video of how it would work at your clinic?")
GOOD = dict(specific=2, true=2, relevant=2, short=2, tone=2, next_step=2, compliance=1)   # 13/14


def ready_config(tmp_path) -> Path:
    """A copy of config/ where config_check --strict passes (offer, address, proof filled)."""
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    me = yaml.safe_load((cfg / "me.yaml").read_text(encoding="utf-8"))
    me.update(business_name="Ahmad Automation", postal_address="PO Box 12, Lahore, Pakistan",
              skills=["Python"], portfolio_links=["https://example.com/demo"])
    (cfg / "me.yaml").write_text(yaml.safe_dump(me), encoding="utf-8")
    offer = yaml.safe_load((cfg / "offer.yaml").read_text(encoding="utf-8"))
    offer["status"] = "decided"
    offer["offers"][0].update(name="Missed call catcher", problem="missed calls", customer="clinics",
                              result="every call answered", proof=[{"title": "demo", "link": "https://example.com/v"}])
    (cfg / "offer.yaml").write_text(yaml.safe_dump(offer), encoding="utf-8")
    return cfg


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "d.db")
    yield s
    s.close()


def make_desk(store, tmp_path, now=NOW, config_dir=None):
    return db.Desk(store, config_dir=config_dir, now=lambda: now, snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def desk(store, tmp_path):
    return make_desk(store, tmp_path)


@pytest.fixture
def ready(store, tmp_path):
    return make_desk(store, tmp_path, config_dir=ready_config(tmp_path))


def set_status(store, lead_id, status):
    with store.conn:
        store.conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (status, lead_id))


def make_qualified(desk, *, url="https://brightsmile.com", channel="email", email="sara@brightsmile.com"):
    """A qualified lead with one checked STRONG pain proof (E1) and an owner with a published email."""
    lead_id = desk.add_lead(url, "slow replies", channel)
    desk.set_research(lead_id, pattern="missed-leads-slow-replies")
    sha = desk.save_snapshot(URL, PAGE, 200, "careers")[0]
    pain, _, _ = desk.add_evidence(lead_id, claim="cannot keep up with calls", url=URL,
                                   quote="we can't keep up with patient calls", grade="STRONG_SIGNAL",
                                   source_type="job_post", topic="pain", observed_at="2026-09-20", snapshot=sha)
    desk.verify_evidence(pain, "STRONG_SIGNAL", "quote found")
    contact, _, _ = desk.add_evidence(lead_id, claim="Sara is office manager", url=URL,
                                      quote="Contact our office manager Sara Khan", grade="CONFIRMED_FACT",
                                      source_type="website", topic="contact", snapshot=sha)
    desk.verify_evidence(contact, "CONFIRMED_FACT", "quote found")
    kw = dict(email=email, email_status="published", evidence_id=contact) if email else {}
    desk.set_contact(lead_id, title="Office Manager", name="Sara Khan", role_type="owner", **kw)
    set_status(desk.store, lead_id, "qualified")
    return lead_id, pain


def drafted(desk, **kw):
    lead_id, pain = make_qualified(desk, **kw)
    channel = kw.get("channel", "email")
    mid, problems = desk.save_draft(lead_id, body=BODY, subject="your front desk" if channel == "email" else None,
                                    evidence_ids=[pain])
    return lead_id, mid, problems


def passed(desk, **kw):
    lead_id, mid, _ = drafted(desk, **kw)
    verdict, _ = desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=GOOD, reasons=[])
    assert verdict == "APPROVE_FOR_HUMAN"
    return lead_id, mid


def events(desk, lead_id, type_):
    return [e for e in desk.store.events_for(lead_id) if e["type"] == type_]


# ---------- save-draft ----------
def test_save_draft_stores_message_without_footer(desk):
    lead_id, mid, problems = drafted(desk)
    msg = desk.store.get_message(mid)
    assert msg["body"] == BODY and msg["subject"] == "your front desk" and msg["evidence_ids"] == [1]
    assert msg["channel"] == "email" and msg["direction"] == "out" and msg["touch_number"] == 1
    assert "won't email you again" not in msg["body"]
    # only warning now: the real config has no postal address yet
    assert not ck.errors(problems)
    assert "no postal address" in " ".join(p.text for p in problems)
    assert events(desk, lead_id, "draft_saved")[0]["payload"]["message_id"] == mid


def test_save_draft_needs_qualified_lead(desk):
    lead_id = desk.add_lead("https://brightsmile.com", "x", "email")
    with pytest.raises(db.InvalidMove, match="qualified, draft_ready"):
        desk.save_draft(lead_id, body=BODY, evidence_ids=[1])


def test_save_draft_refused_for_blocked_domain(desk):
    lead_id, pain = make_qualified(desk)
    desk.block("brightsmile.com", "opt-out")
    with pytest.raises(db.Blocked):
        desk.save_draft(lead_id, body=BODY, subject="hi there", evidence_ids=[pain])


def test_two_rewrites_max_per_day(desk, store, tmp_path):
    lead_id, pain = make_qualified(desk)
    for _ in range(3):
        desk.save_draft(lead_id, body=BODY, subject="your front desk", evidence_ids=[pain])
    with pytest.raises(db.CapReached, match="first \\+ 2 rewrites"):
        desk.save_draft(lead_id, body=BODY, subject="your front desk", evidence_ids=[pain])
    make_desk(store, tmp_path, now=TOMORROW).save_draft(lead_id, body=BODY, subject="your front desk",
                                                         evidence_ids=[pain])


def test_daily_draft_cap(store, tmp_path):
    cfg = tmp_path / "capcfg"
    shutil.copytree(ROOT / "config", cfg)
    policy = yaml.safe_load((cfg / "policy.yaml").read_text(encoding="utf-8"))
    policy["caps"]["max_drafts_per_day"] = 1
    (cfg / "policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    d = make_desk(store, tmp_path, config_dir=cfg)
    drafted(d)
    lead2, pain2 = make_qualified(d, url="https://othersmile.com", email="bob@othersmile.com")
    with pytest.raises(db.CapReached, match="1 drafts today"):
        d.save_draft(lead2, body=BODY, subject="your front desk", evidence_ids=[pain2])


# ---------- critic ----------
def test_critic_approval_moves_lead_to_draft_ready(desk):
    lead_id, mid = passed(desk)
    assert desk.get(lead_id)["status"] == "draft_ready"
    critic = desk.store.get_message(mid)["critic"]
    assert critic["verdict"] == "APPROVE_FOR_HUMAN" and critic["total"] == 13
    assert desk.draft_state(desk.store.get_message(mid)) == "ready for Ahmad (/approve)"


@pytest.mark.parametrize("scores, why", [
    ({**GOOD, "compliance": 0}, "lowest 0"),
    (dict(specific=2, true=2, relevant=2, short=1, tone=1, next_step=1, compliance=1), "total 10/14"),
])
def test_critic_cannot_approve_below_the_rule(desk, scores, why):
    lead_id, mid, _ = drafted(desk)
    verdict, notes = desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=scores, reasons=[])
    assert verdict == "REWRITE" and why in notes[0]
    assert desk.get(lead_id)["status"] == "qualified"


def test_critic_cannot_approve_when_code_checks_fail(desk):
    lead_id, pain = make_qualified(desk)
    mid, problems = desk.save_draft(lead_id, body=BODY + " Can we hop on a quick call?", subject="your front desk",
                                    evidence_ids=[pain])
    assert ck.errors(problems)
    verdict, notes = desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=GOOD, reasons=[])
    assert verdict == "REWRITE" and "banned phrase" in notes[0]


def test_review_input_rules(desk):
    _, mid, _ = drafted(desk)
    with pytest.raises(db.DeskError, match="missing: compliance"):
        desk.save_review(mid, verdict="REWRITE", scores={k: 1 for k in db.CRITIC_KEYS[:-1]}, reasons=["x"])
    with pytest.raises(db.DeskError, match="must be 0, 1 or 2"):
        desk.save_review(mid, verdict="REWRITE", scores={**GOOD, "tone": 3}, reasons=["x"])
    with pytest.raises(db.DeskError, match="needs at least one --reason"):
        desk.save_review(mid, verdict="REWRITE", scores=GOOD, reasons=[])
    with pytest.raises(db.DeskError, match="unknown verdict"):
        desk.save_review(mid, verdict="MAYBE", scores=GOOD, reasons=[])


def test_old_draft_cannot_be_reviewed(desk):
    lead_id, mid, _ = drafted(desk)
    desk.save_draft(lead_id, body=BODY, subject="your front desk", evidence_ids=[1])
    with pytest.raises(db.InvalidMove, match="old draft"):
        desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=GOOD, reasons=[])


def test_no_rewrites_left_note(desk):
    lead_id, pain = make_qualified(desk)
    for _ in range(3):
        mid, _ = desk.save_draft(lead_id, body=BODY, subject="your front desk", evidence_ids=[pain])
    _, notes = desk.save_review(mid, verdict="REWRITE", scores=GOOD, reasons=["shorter"])
    assert "no rewrites left" in notes[0]


# ---------- Ahmad's approval ----------
def test_approve_saves_hash_and_moves_lead(desk):
    lead_id, mid = passed(desk)
    target, _ = desk.approve(mid, "approve", "looks true")
    assert target == mid and desk.get(lead_id)["status"] == "approved"
    row = desk.store.approvals_for("message", mid)[-1]
    assert row["decision"] == "approved" and row["body_sha256"] == ck.body_sha256("your front desk", BODY)
    assert desk.valid_approval(desk.store.get_message(mid)) is not None


def test_approve_needs_critic_pass(desk):
    lead_id, mid, _ = drafted(desk)
    set_status(desk.store, lead_id, "draft_ready")
    with pytest.raises(db.InvalidMove, match="critic did not pass"):
        desk.approve(mid, "approve", "ok")


def test_approve_needs_reason(desk):
    _, mid = passed(desk)
    with pytest.raises(db.DeskError, match="reason is required"):
        desk.approve(mid, "approve", " ")


def test_edit_makes_new_draft_and_approves_it(desk):
    lead_id, mid = passed(desk)
    new_body = BODY.replace("2-minute", "short")
    target, text = desk.approve(mid, "edit", "my wording", body=new_body)
    assert target != mid and desk.get(lead_id)["status"] == "approved"
    new = desk.store.get_message(target)
    assert new["body"] == new_body and new["subject"] == "your front desk" and new["evidence_ids"] == [1]
    assert new["critic"]["verdict"] == "EDITED_BY_AHMAD" and new["critic"]["from_message"] == mid
    assert desk.store.approvals_for("message", target)[-1]["decision"] == "edited"


def test_edit_from_qualified_when_critic_failed(desk):
    lead_id, mid, _ = drafted(desk)
    desk.save_review(mid, verdict="REWRITE", scores=GOOD, reasons=["too vague"])
    target, _ = desk.approve(mid, "edit", "fixed it myself", body=BODY)
    assert desk.get(lead_id)["status"] == "approved"


def test_edit_with_errors_is_refused(desk):
    lead_id, mid = passed(desk)
    with pytest.raises(db.InvalidMove, match="banned phrase"):
        desk.approve(mid, "edit", "x", body=BODY + " Let's have a quick call.")
    assert desk.get(lead_id)["status"] == "draft_ready"


def test_reject_keeps_lead_open_or_closes_it(desk):
    lead_id, mid = passed(desk)
    desk.approve(mid, "reject", "wrong angle")
    assert desk.get(lead_id)["status"] == "draft_ready"
    assert desk.draft_state(desk.store.get_message(mid)) == "rejected by Ahmad"
    mid2, _ = desk.save_draft(lead_id, body=BODY, subject="your front desk", evidence_ids=[1])
    desk.approve(mid2, "reject", "not a fit", close_lead=True)
    assert desk.get(lead_id)["status"] == "rejected"


def test_reject_after_approval_goes_back_to_draft_ready(desk):
    lead_id, mid = passed(desk)
    desk.approve(mid, "approve", "ok")
    desk.approve(mid, "reject", "changed my mind")
    assert desk.get(lead_id)["status"] == "draft_ready"
    assert desk.valid_approval(desk.store.get_message(mid)) is None


# ---------- export ----------
def test_export_refused_while_strict_config_fails(store, tmp_path):
    cfg = ready_config(tmp_path)
    me = yaml.safe_load((cfg / "me.yaml").read_text(encoding="utf-8"))
    me["postal_address"] = ""
    (cfg / "me.yaml").write_text(yaml.safe_dump(me), encoding="utf-8")
    d = make_desk(store, tmp_path, config_dir=cfg)
    lead_id, mid = passed(d)
    d.approve(mid, "approve", "ok")                    # writing and approving work without the address
    with pytest.raises(db.InvalidMove, match="config_check --strict fails.*postal_address"):
        d.export_draft(mid)


def test_export_email_gives_gmail_payload_with_footer(ready):
    lead_id, mid = passed(ready)
    ready.approve(mid, "approve", "ok")
    out = ready.export_draft(mid)
    assert out["to"] == ["sara@brightsmile.com"] and out["subject"] == "your front desk"
    assert out["body"].startswith(BODY)
    assert "Ahmad Automation · PO Box 12, Lahore, Pakistan" in out["body"]
    assert "won't email you again" in out["body"]
    ready.set_gmail_draft(mid, "r-123", "t-9")
    msg = ready.store.get_message(mid)
    assert msg["gmail_draft_id"] == "r-123" and msg["thread_id"] == "t-9"
    assert ready.draft_state(msg) == "in Gmail drafts"
    assert ready.get(lead_id)["status"] == "approved"           # contacted only after Ahmad sends (Stage 6)
    with pytest.raises(db.InvalidMove, match="already in Gmail drafts"):
        ready.export_draft(mid)
    with pytest.raises(db.InvalidMove, match="already has Gmail draft"):
        ready.set_gmail_draft(mid, "r-456")


def test_export_needs_approved_lead(ready):
    _, mid = passed(ready)
    with pytest.raises(db.InvalidMove, match="needs an approved draft"):
        ready.export_draft(mid)


def test_text_changed_after_approval_invalidates(ready, store):
    lead_id, mid = passed(ready)
    ready.approve(mid, "approve", "ok")
    store.update_message(mid, {"body": BODY + " P.S. extra line for you."})    # changed behind Ahmad's back
    assert ready.draft_state(store.get_message(mid)).startswith("CHANGED")
    with pytest.raises(db.InvalidMove, match="changed after Ahmad approved"):
        ready.export_draft(mid)
    assert ready.get(lead_id)["status"] == "draft_ready"
    assert events(ready, lead_id, "approval_invalid")
    with pytest.raises(db.InvalidMove, match="no valid approval"):
        ready.set_gmail_draft(mid, "r-1")


def test_export_refused_when_blocked_after_approval(ready):
    _, mid = passed(ready)
    ready.approve(mid, "approve", "ok")
    ready.block("sara@brightsmile.com", "opt-out")
    with pytest.raises(db.InvalidMove, match="block list"):
        ready.export_draft(mid)


def test_first_email_daily_cap(store, tmp_path):
    cfg = ready_config(tmp_path)
    policy = yaml.safe_load((cfg / "policy.yaml").read_text(encoding="utf-8"))
    policy["caps"]["max_first_emails_per_day"] = 1
    (cfg / "policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    d = make_desk(store, tmp_path, config_dir=cfg)
    _, m1 = passed(d)
    d.approve(m1, "approve", "ok")
    d.export_draft(m1)
    d.set_gmail_draft(m1, "r-1")
    _, m2 = passed(d, url="https://othersmile.com", email="bob@othersmile.com")
    d.approve(m2, "approve", "ok")
    with pytest.raises(db.CapReached, match="1 first emails today"):
        d.export_draft(m2)
    make_desk(store, tmp_path, now=TOMORROW, config_dir=cfg).export_draft(m2)


def test_export_other_channel_writes_copy_paste_file(ready, tmp_path):
    lead_id, mid = passed(ready, channel="upwork_proposal", email=None)
    ready.approve(mid, "approve", "ok")
    out = ready.export_draft(mid, out_dir=tmp_path / "cards")
    path = Path(out["path"])
    assert path.name == f"{lead_id}-message.txt"
    assert path.read_text(encoding="utf-8") == BODY + "\n"
    assert "PO Box" not in path.read_text(encoding="utf-8")
    assert ready.draft_state(ready.store.get_message(mid)) == "approved, copy-paste file written"
    with pytest.raises(db.DeskError, match="not an email"):
        ready.set_gmail_draft(mid, "r-1")


def test_message_id_parsing():
    assert db.message_id("M12") == 12 and db.message_id(" m7 ") == 7 and db.message_id(3) == 3
    with pytest.raises(db.DeskError):
        db.message_id("E3")


# ---------- command line ----------
def test_cli_flow(tmp_path, capsys, monkeypatch):
    db_file = tmp_path / "cli.db"
    s = SqliteStore(db_file)
    d = make_desk(s, tmp_path)
    lead_id, pain = make_qualified(d)
    s.close()

    def run(*args):
        code = desk_cli.main([*args, "--backend", "sqlite", "--db", str(db_file)])
        return code, capsys.readouterr().out

    body_file = tmp_path / "draft.txt"
    body_file.write_text(BODY, encoding="utf-8")
    code, out = run("save-draft", str(lead_id), "--body-file", str(body_file), "--subject", "your front desk",
                    "--evidence", f"E{pain}")
    assert code == 0 and "Saved draft M1" in out and "0 error(s)" in out
    code, out = run("save-review", "M1", "--verdict", "APPROVE_FOR_HUMAN",
                    "--scores", ",".join(f"{k}={v}" for k, v in GOOD.items()))
    assert code == 0 and "APPROVE_FOR_HUMAN" in out
    code, out = run("drafts", str(lead_id), "--full")
    assert code == 0 and "Subject : your front desk" in out and "sara@brightsmile.com" in out
    assert "we can't keep up with patient calls" in out and "Footer added by code" in out
    code, out = run("approve", "M1", "--decision", "approve", "--reason", "true and short")
    assert code == 0 and "Lead #1 is approved" in out
    code, out = run("export-draft", "M1")
    assert code == 1 and "REFUSED" in out and "config_check --strict fails" in out
    code, out = run("show", str(lead_id))
    assert code == 0 and "Newest draft: M1 (email) approved by Ahmad" in out
    code, out = run("save-review", "M1", "--verdict", "REWRITE", "--scores", "specific=2")
    assert code == 1 and "ERROR" in out
