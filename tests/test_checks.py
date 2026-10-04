"""Tests for scripts/checks.py: the code checks every outreach draft must pass (no database)."""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import checks as ck  # noqa: E402

POLICY = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))
ME_READY = {"name": "Ahmad", "business_name": "Ahmad Automation", "postal_address": "PO Box 12, Lahore, Pakistan"}
LEAD = {"id": 1, "channel": "email", "company_domain": "brightsmile.com"}
EVIDENCE = [
    {"id": 1, "topic": "pain", "grade": "STRONG_SIGNAL", "verified": True},
    {"id": 2, "topic": "company", "grade": "CONFIRMED_FACT", "verified": True},
    {"id": 3, "topic": "pain", "grade": "WEAK_SIGNAL", "verified": False},
    {"id": 4, "topic": "pain", "grade": "UNKNOWN", "verified": True},
    {"id": 5, "topic": "impact", "grade": "INFERENCE", "verified": True},
]
PERSON = {"email": "sara@brightsmile.com", "email_status": "published"}
GOOD_BODY = ("Hi Sara, I saw your Sept 20 post: your phones ring all day and you can't keep up with patient calls. "
             "A small auto-reply and call-back list could catch those requests for you. "
             "Want me to send a 2-minute video of how it would work at your clinic?")


def run(body=GOOD_BODY, *, channel="email", subject="your front desk", evidence_ids=(1,), lead=None,
        recipient=PERSON, me=ME_READY, blocked=(), touch=1, final=False, policy=POLICY):
    lead = dict(lead or LEAD, channel=(lead or LEAD).get("channel", channel))
    return ck.check_draft(channel=channel, subject=subject, body=body, evidence_ids=list(evidence_ids), lead=lead,
                          evidence=EVIDENCE, recipient=recipient, policy=policy, me=me,
                          is_blocked=lambda v: v in blocked, touch_number=touch, final=final)


def texts(problems, level=None):
    return " | ".join(p.text for p in problems if level is None or p.level == level)


def test_good_email_is_clean():
    assert run() == []


def test_word_count_and_hash_ignore_line_endings():
    assert ck.word_count("a  b\nc") == 3
    assert ck.body_sha256("Hi", "line one\r\nline two  \n") == ck.body_sha256("Hi", "line one\nline two")
    assert ck.body_sha256("Hi", "one") != ck.body_sha256("Hi", "one.")
    assert ck.body_sha256("Hi", "one") != ck.body_sha256("Hey", "one")


@pytest.mark.parametrize("channel", ["email", "linkedin_message", "upwork_proposal", "agency_pitch", "referral_ask"])
def test_word_limit_per_channel(channel):
    limit = POLICY["word_limits"][channel]
    ok = " ".join(["you"] * limit)
    long = " ".join(["you"] * (limit + 1))
    kw = dict(channel=channel, lead={**LEAD, "channel": channel})
    assert "too long" not in texts(run(ok, **kw))
    assert f"too long: {limit + 1} words" in texts(run(long, **kw), ck.ERROR)


def test_subject_rules_email_only():
    assert "needs a subject" in texts(run(subject=""), ck.ERROR)
    assert "subject has 5 words" in texts(run(subject="a note about your clinic"), ck.ERROR)
    assert "subject" not in texts(run(channel="upwork_proposal", subject=None, lead={**LEAD, "channel": "upwork_proposal"}))


def test_banned_phrase_any_case():
    out = run(GOOD_BODY + " Can we hop on a Quick Call?")
    assert 'banned phrase: "quick call"' in texts(out, ck.ERROR)
    assert "banned phrase" in texts(run(subject="Synergy for you"), ck.ERROR)


@pytest.mark.parametrize("bad", ["Hi {first_name},", "Hi [Company] team", "TODO add proof", "<YOUR NAME>"])
def test_placeholders(bad):
    assert "placeholder" in texts(run(bad + " " + GOOD_BODY), ck.ERROR)


def test_footer_address_missing_is_warning_then_error():
    me = {"name": "Ahmad", "postal_address": ""}
    assert "no postal address" in texts(run(me=me), ck.WARNING)
    assert "no postal address" in texts(run(me=me, final=True), ck.ERROR)


def test_footer_needs_opt_out_line():
    policy = {**POLICY, "email_footer": "{name} · {postal_address}"}
    assert "no opt-out line" in texts(run(policy=policy), ck.ERROR)
    assert ck.render_footer(POLICY, ME_READY).startswith("Ahmad Automation · PO Box 12")


def test_opt_out_in_body_is_only_a_warning():
    out = run(GOOD_BODY + " Reply no and I won't email you again.")
    assert "footer adds it already" in texts(out, ck.WARNING)
    assert not ck.errors(out)


def test_footer_not_checked_for_other_channels():
    out = run(channel="upwork_proposal", subject=None, me={"name": "Ahmad", "postal_address": ""},
              lead={**LEAD, "channel": "upwork_proposal"}, final=True)
    assert "postal" not in texts(out)


def test_block_list_email_and_domain():
    assert "sara@brightsmile.com is on the block list" in texts(run(blocked={"sara@brightsmile.com"}), ck.ERROR)
    assert "brightsmile.com is on the block list" in texts(run(blocked={"brightsmile.com"}), ck.ERROR)


def test_recipient_rules():
    assert "no recipient email" in texts(run(recipient=None), ck.ERROR)
    assert "not published / verified" in texts(run(recipient={"email": "a@brightsmile.com",
                                                              "email_status": "inferred"}), ck.ERROR)
    assert "personal email" in texts(run(recipient={"email": "sara@gmail.com", "email_status": "published"}),
                                     ck.ERROR)
    # LinkedIn needs no email
    assert "recipient" not in texts(run(channel="linkedin_message", subject=None, recipient=None,
                                        lead={**LEAD, "channel": "linkedin_message"}))


def test_evidence_ids_rules():
    assert "no evidence ids" in texts(run(evidence_ids=()), ck.ERROR)
    assert "E99 is not evidence" in texts(run(evidence_ids=(1, 99)), ck.ERROR)
    assert "E3 is not checked" in texts(run(evidence_ids=(1, 3)), ck.ERROR)
    assert "E4 is UNKNOWN" in texts(run(evidence_ids=(1, 4)), ck.ERROR)
    assert "E5 is an INFERENCE" in texts(run(evidence_ids=(1, 5)), ck.WARNING)
    assert "no checked problem proof" in texts(run(evidence_ids=(2,)), ck.ERROR)


def test_linkedin_first_message_no_link():
    kw = dict(channel="linkedin_message", subject=None, lead={**LEAD, "channel": "linkedin_message"})
    for body in ("See https://example.com for you", "look at www.example.com for you", "see mysite.com for you"):
        assert "no links" in texts(run(body, **kw), ck.ERROR)
    assert "no links" not in texts(run("See https://example.com for you", touch=2, **kw))
    assert "no links" not in texts(run("You said you use Shopify. Is it still slow for you?", **kw))


def test_channel_rules():
    assert "not allowed" in texts(run(channel="sms"), ck.ERROR)
    assert "not the lead's channel" in texts(run(channel="email", lead={**LEAD, "channel": "upwork_proposal"}),
                                             ck.WARNING)


def test_me_words_warning():
    out = run("I build tools. We are fast. Our team is great. My work is good. Do you want it?")
    assert "talks more about me/we" in texts(out, ck.WARNING)


def test_empty_body():
    assert "empty" in texts(run(""), ck.ERROR)
