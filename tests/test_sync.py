"""Tests for Stage 6: sent messages, follow-up drafts, replies and their effects (SQLite, no Gmail).

Dates: 2026-10-04 is a Sunday. The first email is sent then, so follow-up 2 is due Wed 2026-10-07
(3 business days), follow-up 3 on Tue 2026-10-13 (7 business days).
"""
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
import today as today_mod  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402
from test_drafts import BODY, GOOD, NOW, events, make_desk, make_qualified, passed, ready_config  # noqa: E402

WED = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)
TUE13 = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
FU = ("Sara, one more idea for your front desk: a missed-call text that sends patients your booking link "
      "within a minute. Want me to show you a 1-minute example for your clinic?")
FU3 = ("Sara, a last small example: your web form could put each request on one call-back list, so nothing "
       "waits overnight. Shall I send a screenshot of how that list would look for you?")
SARA = "sara@brightsmile.com"


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "d.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return ready_config(tmp_path)


@pytest.fixture
def desk(store, tmp_path, cfg):
    return make_desk(store, tmp_path, config_dir=cfg)


def at(desk, when):
    """The same desk on another day."""
    return db.Desk(desk.store, config_dir=desk.config_dir, now=lambda: when, snapshot_dir=desk.snapshot_dir)


def contacted(desk, **kw):
    """Lead with an approved first email that Ahmad sent (Gmail message g-1)."""
    lead_id, mid = passed(desk, **kw)
    desk.approve(mid, "approve", "ok")
    if kw.get("channel", "email") == "email":
        desk.export_draft(mid)
        desk.set_gmail_draft(mid, "r-1", "t-1")
    desk.mark_sent(mid, "g-1")
    return lead_id, mid


def follow_up(desk, lead_id, body=FU, pain=1):
    """Write, pass and approve follow-up text on the desk's day. Returns the message id."""
    mid, _ = desk.save_draft(lead_id, body=body, evidence_ids=[pain])
    verdict, notes = desk.save_review(mid, verdict="APPROVE_FOR_HUMAN", scores=GOOD, reasons=[])
    assert verdict == "APPROVE_FOR_HUMAN", notes
    desk.approve(mid, "approve", "ok")
    return mid


def pending(desk, lead_id):
    return [(f["kind"], f.get("touch_number"), str(f["due_on"])[:10]) for f in desk.pending_follow_ups(lead_id)]


def edit_yaml(cfg, name, change):
    data = yaml.safe_load((cfg / f"{name}.yaml").read_text(encoding="utf-8"))
    change(data)
    (cfg / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


# ---------- sent ----------
def test_mark_sent_moves_to_contacted_and_plans_follow_up(desk):
    lead_id, mid = contacted(desk)
    assert desk.get(lead_id)["status"] == "contacted"
    msg = desk.store.get_message(mid)
    assert msg["sent_at"] and msg["gmail_message_id"] == "g-1"
    assert desk.draft_state(msg).startswith("sent 2026-10-04")
    assert pending(desk, lead_id) == [("followup", 2, "2026-10-07")]
    ev = events(desk, lead_id, "message_sent")[0]["payload"]
    assert ev["to"] == SARA and ev["touch"] == 1


def test_mark_sent_rules(desk):
    lead_id, mid = passed(desk)
    with pytest.raises(db.InvalidMove, match="no valid approval"):
        desk.mark_sent(mid)
    desk.approve(mid, "approve", "ok")
    with pytest.raises(db.DeskError, match="in the future"):
        desk.mark_sent(mid, sent_at="2027-01-01")
    desk.mark_sent(mid, sent_at="2026-10-03")
    assert desk.local_date(desk.store.get_message(mid)["sent_at"]).isoformat() == "2026-10-03"
    with pytest.raises(db.InvalidMove, match="already marked as sent"):
        desk.mark_sent(mid)
    with pytest.raises(db.InvalidMove, match="already sent"):
        desk.approve(mid, "approve", "again")


def test_draft_missing_lets_export_run_again(desk):
    lead_id, mid = passed(desk)
    desk.approve(mid, "approve", "ok")
    desk.export_draft(mid)
    desk.set_gmail_draft(mid, "r-1", "t-1")
    desk.draft_missing(mid)
    msg = desk.store.get_message(mid)
    assert msg["gmail_draft_id"] is None and desk.draft_state(msg) == "approved by Ahmad"
    assert desk.export_draft(mid)["to"] == [SARA]
    with pytest.raises(db.InvalidMove, match="no Gmail draft"):
        desk.draft_missing(mid)


# ---------- follow-up drafts ----------
def test_follow_up_not_before_its_day(desk):
    lead_id, _ = contacted(desk)
    with pytest.raises(db.InvalidMove, match="no follow-up is due today. Next one is due 2026-10-07"):
        desk.save_draft(lead_id, body=FU, evidence_ids=[1])


def test_follow_up_flow_same_thread(desk):
    lead_id, first = contacted(desk)
    wed = at(desk, WED)
    mid = follow_up(wed, lead_id)
    msg = wed.store.get_message(mid)
    assert msg["touch_number"] == 2 and wed.get(lead_id)["status"] == "contacted"
    out = wed.export_draft(mid)
    assert out["reply_to_message_id"] == "g-1" and out["subject"] == "Re: your front desk"
    assert out["body"].startswith(FU) and "won't email you again" in out["body"]
    wed.set_gmail_draft(mid, "r-2", "t-1")
    text = wed.mark_sent(mid, "g-2")
    assert "touch 2" in text and "2026-10-13" in text
    assert pending(wed, lead_id) == [("followup", 3, "2026-10-13")]
    assert [f["status"] for f in wed.store.follow_ups_for(lead_id)] == ["done", "pending"]


def test_follow_up_checks(desk):
    lead_id, _ = contacted(desk)
    wed = at(desk, WED)
    _, problems = wed.save_draft(lead_id, body="Just following up on my last email. " + FU, evidence_ids=[1])
    assert any("following up" in p.text for p in ck.errors(problems))
    _, problems = wed.save_draft(lead_id, body=BODY, evidence_ids=[1])
    assert any("repeats earlier message" in p.text for p in ck.errors(problems))
    _, problems = wed.save_draft(lead_id, body=FU, evidence_ids=[1])
    assert not ck.errors(problems)             # no subject is fine for a follow-up


def test_max_touches_refuses_more(store, tmp_path, cfg):
    edit_yaml(cfg, "cadence", lambda c: c.update(max_touches=2))
    d = make_desk(store, tmp_path, config_dir=cfg)
    lead_id, _ = contacted(d)
    wed = at(d, WED)
    mid = follow_up(wed, lead_id)
    wed.mark_sent(mid)
    assert pending(wed, lead_id) == []
    with pytest.raises(db.CapReached, match="max 2 touches"):
        at(d, TUE13).save_draft(lead_id, body=FU3, evidence_ids=[1])


def test_no_response_after_last_touch(store, tmp_path, cfg):
    edit_yaml(cfg, "cadence", lambda c: c.update(max_touches=1))
    d = make_desk(store, tmp_path, config_dir=cfg)
    lead_id, _ = contacted(d)
    assert at(d, datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)).sync_followups() == []
    later = at(d, datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc))      # 10 business days after Sun Oct 4
    assert later.sync_followups(dry_run=True) and later.get(lead_id)["status"] == "contacted"
    changes = later.sync_followups()
    assert "no_response" in changes[0] and later.get(lead_id)["status"] == "no_response"
    # events get the real clock time, so look at the report "now"
    lines = dict(today_mod.report(at(d, datetime.now(timezone.utc))))["Closed by the desk in the last 7 days"]
    assert "contacted -> no_response" in lines[0]


def test_first_email_cap_ignores_follow_ups(store, tmp_path, cfg):
    edit_yaml(cfg, "policy", lambda p: p["caps"].update(max_first_emails_per_day=1))
    d = make_desk(store, tmp_path, config_dir=cfg)
    lead_id, _ = contacted(d)
    wed = at(d, WED)
    mid = follow_up(wed, lead_id)
    wed.export_draft(mid)
    wed.set_gmail_draft(mid, "r-2")
    _, m2 = passed(wed, url="https://othersmile.com", email="bob@othersmile.com")
    wed.approve(m2, "approve", "ok")
    assert wed.export_draft(m2)["to"] == ["bob@othersmile.com"]          # the follow-up did not use the cap


def test_follow_up_other_channel_file(desk, tmp_path):
    lead_id, _ = contacted(desk, channel="upwork_proposal", email=None)
    wed = at(desk, WED)
    mid = follow_up(wed, lead_id)
    out = wed.export_draft(mid, out_dir=tmp_path / "cards")
    assert Path(out["path"]).name == f"{lead_id}-touch2-message.txt"


# ---------- replies ----------
def test_reply_needs_a_sent_message(desk):
    lead_id, _ = passed(desk)
    with pytest.raises(db.InvalidMove, match="nothing was sent"):
        desk.log_reply(lead_id, "hello")


def test_opt_out_blocks_at_once(desk):
    lead_id, _ = contacted(desk)
    rid, rule, done, new = desk.log_reply(lead_id, "Please remove me from your list.", gmail_message_id="g-9",
                                          sender="Sara Khan <sara@brightsmile.com>")
    assert new and rule == "opt_out"
    assert desk.is_blocked(SARA)
    assert desk.get(lead_id)["status"] == "opted_out"
    assert pending(desk, lead_id) == []
    reply = desk.store.get_reply(rid)
    assert reply["category"] == "opt_out" and reply["handled_at"]
    assert any("blocked sara@brightsmile.com" in d for d in done)
    # the same Gmail message again: nothing new
    assert desk.log_reply(lead_id, "Please remove me", gmail_message_id="g-9")[3] is False
    # the agent cannot undo it
    final, notes = desk.classify_reply(rid, category="positive", next_action="draft_reply", note="x")
    assert final == "opt_out" and "kept opt_out" in notes[0]
    assert desk.get(lead_id)["status"] == "opted_out"


def test_opt_out_from_other_address_blocks_both(desk):
    lead_id, _ = contacted(desk)
    desk.log_reply(lead_id, "Do not contact us again.", sender="Front Desk <office@brightsmile.com>")
    assert desk.is_blocked(SARA) and desk.is_blocked("office@brightsmile.com")


def test_positive_reply_waits_for_ahmad(desk):
    lead_id, _ = contacted(desk)
    rid, rule, _, _ = desk.log_reply(lead_id, "Yes please send the video.", gmail_message_id="g-5")
    assert rule is None and desk.get(lead_id)["status"] == "contacted"
    assert pending(desk, lead_id) == [("followup", 2, "2026-10-07")]          # paused, not cancelled
    with pytest.raises(db.InvalidMove, match="not sorted yet"):
        at(desk, WED).save_draft(lead_id, body=FU, evidence_ids=[1])
    assert "not sorted yet" in dict(today_mod.report(desk))["Replies that need you"][0]
    final, notes = desk.classify_reply(rid, category="positive", next_action="draft_reply",
                                       note="asks for the video", asked="the 2-minute video")
    assert final == "positive" and desk.get(lead_id)["status"] == "replied"
    assert pending(desk, lead_id) == []
    line = dict(today_mod.report(desk))["Replies that need you"][0]
    assert "[positive]" in line and "the 2-minute video" in line
    with pytest.raises(db.InvalidMove, match="already sorted"):
        desk.classify_reply(rid, category="question", next_action="draft_reply", note="x")
    desk.reply_done(rid, "sent the video by hand")
    assert "Replies that need you" not in dict(today_mod.report(desk))


def test_agent_cannot_miss_a_short_no(desk):
    lead_id, _ = contacted(desk)
    rid, rule, _, _ = desk.log_reply(lead_id, "No")
    assert rule == "opt_out" and desk.get(lead_id)["status"] == "opted_out"


def test_not_now_sets_a_reminder(desk):
    lead_id, _ = contacted(desk)
    rid, _, _, _ = desk.log_reply(lead_id, "Not now, try me in January.")
    final, notes = desk.classify_reply(rid, category="not_now", next_action="schedule_followup", note="busy",
                                       follow_up_date="2027-01-11")
    assert desk.get(lead_id)["status"] == "replied"
    assert pending(desk, lead_id) == [("nurture", None, "2027-01-11")]
    assert desk.store.get_reply(rid)["handled_at"]
    jan = at(desk, datetime(2027, 1, 11, 6, 0, tzinfo=timezone.utc))
    assert "not now" in dict(today_mod.report(jan))["Try again ('not now' reminders)"][0]
    mid, _ = jan.save_draft(lead_id, body=FU, evidence_ids=[1])
    assert jan.store.get_message(mid)["touch_number"] == 2


def test_not_interested_closes(desk):
    lead_id, _ = contacted(desk)
    rid, _, _, _ = desk.log_reply(lead_id, "Thanks, but we are not interested.")
    desk.classify_reply(rid, category="not_interested", next_action="stop_sequence", note="said no politely")
    assert desk.get(lead_id)["status"] == "lost"
    assert not desk.is_blocked(SARA)                     # polite no is not an opt-out


def test_out_of_office_moves_the_follow_up(desk):
    lead_id, _ = contacted(desk)
    rid, rule, _, _ = desk.log_reply(lead_id, "I am out of the office until October 19.",
                                     subject="Automatic reply: your front desk")
    assert rule == "out_of_office"
    final, notes = desk.classify_reply(rid, category="out_of_office", next_action="schedule_followup",
                                       note="auto reply", follow_up_date="2026-10-19")
    assert pending(desk, lead_id) == [("followup", 2, "2026-10-20")]
    assert desk.get(lead_id)["status"] == "contacted"
    assert at(desk, WED).sync_followups() == []          # the moved date stays


def test_bounce_then_new_contact(desk):
    lead_id, _ = contacted(desk)
    text = "Address not found. Your message wasn't delivered to sara@brightsmile.com. 550 5.1.1"
    rid, rule, done, _ = desk.log_reply(lead_id, text, sender="Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
    assert rule == "bounce" and desk.is_blocked(SARA)
    assert not desk.is_blocked("brightsmile.com")
    assert desk.recipient(desk.get(lead_id))["email_status"] == "invalid"
    assert desk.get(lead_id)["status"] == "contacted" and pending(desk, lead_id) == []
    assert "Bounced emails" in dict(today_mod.report(desk))
    assert desk.sync_followups() == []                   # no follow-up while the contact is bad
    # Ahmad finds a new published email
    sha = desk.save_snapshot("https://brightsmile.com/team", "Team: Tom Reed, practice manager, tom@brightsmile.com",
                             200, "team")[0]
    desk.store.add_evidence({"opportunity_id": lead_id, "claim": "Tom is practice manager",
                             "url": "https://brightsmile.com/team", "quote": "Tom Reed, practice manager",
                             "snapshot_sha256": sha, "source_type": "website", "topic": "contact",
                             "grade": "CONFIRMED_FACT", "depends_on": [], "verified": True})
    desk.set_contact(lead_id, title="Practice Manager", name="Tom Reed", email="tom@brightsmile.com",
                     email_status="published", evidence_id=3)
    changes = desk.sync_followups()
    assert "bounce solved" in changes[0]
    assert pending(desk, lead_id) == [("followup", 2, "2026-10-04")]
    mid = follow_up(desk, lead_id, body=FU.replace("Sara", "Tom"))
    out = desk.export_draft(mid)
    assert out["to"] == ["tom@brightsmile.com"] and out["reply_to_message_id"] is None   # new thread
    assert out["subject"] == "Re: your front desk"


def test_classify_input_rules(desk):
    lead_id, _ = contacted(desk)
    rid, _, _, _ = desk.log_reply(lead_id, "Interesting.")
    with pytest.raises(db.DeskError, match="unknown category"):
        desk.classify_reply(rid, category="happy", next_action="draft_reply", note="x")
    with pytest.raises(db.DeskError, match="unknown next action"):
        desk.classify_reply(rid, category="positive", next_action="send_now", note="x")
    with pytest.raises(db.DeskError, match="note is required"):
        desk.classify_reply(rid, category="positive", next_action="draft_reply", note=" ")
    with pytest.raises(db.DeskError, match="YYYY-MM-DD"):
        desk.classify_reply(rid, category="not_now", next_action="draft_reply", note="x", follow_up_date="March")
    with pytest.raises(db.NotFound):
        desk.classify_reply(99, category="positive", next_action="draft_reply", note="x")


def test_reply_after_close_does_not_crash(desk):
    lead_id, _ = contacted(desk)
    rid, _, _, _ = desk.log_reply(lead_id, "Thanks, but we are not interested.")
    desk.classify_reply(rid, category="not_interested", next_action="stop_sequence", note="no")
    _, rule, done, _ = desk.log_reply(lead_id, "Please unsubscribe me.")
    assert rule == "opt_out" and desk.is_blocked(SARA)
    assert desk.get(lead_id)["status"] == "lost"


# ---------- today and sync list ----------
def test_today_report_sections(desk):
    lead_id, mid = passed(desk)
    assert "/approve" in dict(today_mod.report(desk))["Drafts waiting for your OK"][0]
    desk.approve(mid, "approve", "ok")
    desk.export_draft(mid)
    desk.set_gmail_draft(mid, "r-1", "t-1")
    assert "press Send" in dict(today_mod.report(desk))["Waiting for you to press Send"][0]
    desk.mark_sent(mid, "g-1")
    sections = dict(today_mod.report(at(desk, datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc))))
    assert "follow-up 2 on 2026-10-07" in sections["Coming in the next 3 days"][0]
    late = dict(today_mod.report(at(desk, datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc))))
    assert "1 day(s) late" in late["Follow-ups due"][0] and f"/draft {lead_id}" in late["Follow-ups due"][0]


def test_today_stale(desk, store):
    lead_id = desk.add_lead("https://quietco.com", "x", "email")
    assert "Stale (no activity for 21+ days)" not in dict(today_mod.report(desk))
    old = "2026-09-01T00:00:00+00:00"
    with store.conn:
        store.conn.execute("UPDATE opportunities SET created_at = ?, updated_at = ? WHERE id = ?", (old, old, lead_id))
    lines = dict(today_mod.report(desk))["Stale (no activity for 21+ days)"]
    assert "quiet" in lines[0].lower() and "33 days" in lines[0]


def test_sync_list(desk):
    lead_id, mid = passed(desk)
    desk.approve(mid, "approve", "ok")
    desk.export_draft(mid)
    desk.set_gmail_draft(mid, "r-1", "t-1")
    data = desk_cli.sync_list(desk)
    assert data["waiting_to_send"] == [{"message_id": f"M{mid}", "lead_id": lead_id, "touch": 1,
                                        "gmail_draft_id": "r-1", "thread_id": "t-1"}]
    assert data["threads"] == []                              # nothing sent yet
    desk.mark_sent(mid, "g-1")
    desk.log_reply(lead_id, "Interesting, tell me more", gmail_message_id="g-7")
    data = desk_cli.sync_list(desk)
    assert data["waiting_to_send"] == []
    assert data["threads"][0]["thread_id"] == "t-1" and data["threads"][0]["known_message_ids"] == ["g-1", "g-7"]


# ---------- command line ----------
def test_cli_stage6(tmp_path, capsys, cfg, monkeypatch):
    db_file = tmp_path / "cli.db"
    s = SqliteStore(db_file)
    d = make_desk(s, tmp_path, config_dir=cfg)
    lead_id, mid = passed(d)
    d.approve(mid, "approve", "ok")
    s.close()

    def run(*args):
        code = desk_cli.main([*args, "--backend", "sqlite", "--db", str(db_file)])
        return code, capsys.readouterr().out

    code, out = run("mark-sent", f"M{mid}", "--gmail-message-id", "g-1")
    assert code == 0 and "marked as sent (touch 1)" in out and "contacted" in out
    code, out = run("followups")
    assert code == 0 and "followup" in out
    reply = tmp_path / "reply.txt"
    reply.write_text("Sounds good, send the video.", encoding="utf-8")
    code, out = run("log-reply", str(lead_id), "--text-file", str(reply), "--gmail-message-id", "g-3")
    assert code == 0 and "Saved reply R1" in out and "nothing found" in out
    code, out = run("log-reply", str(lead_id), "--text-file", str(reply), "--gmail-message-id", "g-3")
    assert "Already saved as R1" in out
    code, out = run("classify-reply", "R1", "--category", "positive", "--next-action", "draft_reply",
                    "--note", "wants the video", "--objections", "time; money")
    assert code == 0 and "R1 sorted as positive" in out and "your move" in out
    code, out = run("replies", str(lead_id))
    assert code == 0 and "[positive]" in out and "Objection: time" in out
    code, out = run("show", str(lead_id))
    assert "touch 1: M1" in out and "R1 [positive]" in out
    code, out = run("reply-done", "R1", "--note", "answered")
    assert code == 0 and "R1 is done" in out
    code, out = run("sync-list")
    assert code == 0 and '"waiting_to_send"' in out and '"threads"' in out
    code, out = run("log-reply", str(lead_id), "--text", "please remove me")
    assert code == 0 and "opt_out" in out and "blocked sara@brightsmile.com" in out


def test_followups_and_today_scripts(tmp_path, capsys, monkeypatch):
    import followups
    db_file = tmp_path / "x.db"
    SqliteStore(db_file).close()
    assert followups.main(["--backend", "sqlite", "--db", str(db_file)]) == 0
    assert "up to date" in capsys.readouterr().out
    assert today_mod.main(["--backend", "sqlite", "--db", str(db_file)]) == 0
    assert "Nothing needs you today" in capsys.readouterr().out
