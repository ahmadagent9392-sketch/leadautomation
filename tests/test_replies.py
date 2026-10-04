"""Tests for scripts/replies.py: fixed rules for opt-out, bounce and out-of-office (Stage 6)."""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import replies as rp  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "replies"
EXPECTED = yaml.safe_load((FIXTURES / "expected.yaml").read_text(encoding="utf-8"))


def test_there_are_at_least_15_samples():
    assert len(EXPECTED) >= 15
    assert sum(1 for v in EXPECTED.values() if v["rule"] == "opt_out") >= 5      # different opt-out wordings
    for name in EXPECTED:
        assert (FIXTURES / f"{name}.txt").exists()
    for v in EXPECTED.values():
        assert v["category"] in rp.CATEGORIES


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_samples_sorted_by_the_fixed_rules(name):
    exp = EXPECTED[name]
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    assert rp.quick_class(text, exp["sender"], exp["subject"]) == exp["rule"]


def test_strip_quoted_removes_old_message_and_footer():
    text = (FIXTURES / "12_positive.txt").read_text(encoding="utf-8")
    new = rp.strip_quoted(text)
    assert new.startswith("Hi Ahmad, yes please send the video")
    assert "wrote:" not in new and "won't email you again" not in new and ">" not in new


def test_our_footer_in_the_quote_is_not_an_opt_out():
    text = ("Sounds interesting, tell me more.\n\nOn Mon, Oct 5, 2026 at 10:02 AM Ahmad <a@example.com> wrote:\n"
            "> If this isn't relevant, reply \"no\" and I won't email you again.\n> unsubscribe")
    assert rp.quick_class(text) is None


def test_outlook_quote_is_cut():
    text = "Yes, call me.\n\n-----Original Message-----\nFrom: Ahmad\nSubject: x\nreply \"no\" to stop"
    assert rp.strip_quoted(text) == "Yes, call me."
    text = "Yes.\n________________________________\nFrom: Ahmad <a@example.com>\nunsubscribe"
    assert rp.strip_quoted(text) == "Yes."


@pytest.mark.parametrize("text", ["No", "no.", "NO!", "Nope", "Stop", "no thank you", "Unsubscribe please",
                                  "remove", "No thanks\n\nSara"])
def test_short_no_is_opt_out(text):
    assert rp.quick_class(text) == "opt_out"


@pytest.mark.parametrize("text", ["No problem, send it.", "No worries, next week works.",
                                  "Not sure yet, what does it cost?", "Nobody told me about this, explain?",
                                  "Stop by our office on Monday and we can talk."])
def test_words_starting_with_no_are_not_opt_out(text):
    assert rp.quick_class(text) is None


@pytest.mark.parametrize("text", ["Please don't email me again.", "Never contact us.", "No more emails please.",
                                  "Leave me alone", "We are not interested, please stop.",
                                  "Remove my email from your list", "please opt me out", "Opt-out"])
def test_opt_out_wordings(text):
    assert rp.quick_class(text) == "opt_out"


def test_bounce_wins_over_everything():
    text = "Mail delivery failed: returning message to sender. Recipient address rejected. unsubscribe"
    assert rp.quick_class(text, "MAILER-DAEMON@mx.example.com") == "bounce"
    assert rp.quick_class(text) == "bounce"


def test_opt_out_wins_over_out_of_office():
    assert rp.quick_class("I'm out of the office. Also, please remove me from your list.") == "opt_out"


def test_normal_reply_mentioning_delivery_is_not_a_bounce():
    assert rp.quick_class("Our delivery vans are always late, can you help with that?") is None


def test_cli(tmp_path, capsys):
    f = tmp_path / "r.txt"
    f.write_text("Please remove me", encoding="utf-8")
    assert rp.main([str(f)]) == 0
    assert "rule result: opt_out" in capsys.readouterr().out
