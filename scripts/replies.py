#!/usr/bin/env python3
"""Fixed rules for replies (Stage 6). The reply-reader agent sorts replies; these rules have the last word
for the cases that must never be missed: opt-out, bounce and out-of-office.

    strip_quoted(text)                 -> only the new part of a reply (no "> ..." lines, no "On ... wrote:")
    quick_class(text, sender, subject) -> "bounce" | "opt_out" | "out_of_office" | None

Our own email footer says: reply "no" and I won't email you again. So a short "no" is an opt-out.
The footer is also inside the quoted old message of every reply, so quoted text is cut off first.

Try it:  python scripts/replies.py FILE [--sender ADDRESS] [--subject TEXT]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CATEGORIES = ("positive", "question", "objection", "referral", "not_now", "not_interested",
              "opt_out", "out_of_office", "bounce", "other")
NEXT_ACTIONS = ("draft_reply", "schedule_followup", "stop_sequence", "block", "ask_ahmad")

# where the quoted old message starts
QUOTE_START = re.compile(
    r"^\s*(On\s.{0,200}?\bwrote:\s*$"                         # Gmail / Apple: On Mon, ... Ahmad <a@b> wrote:
    r"|-{2,}\s*Original Message\s*-{2,}"                      # Outlook
    r"|_{10,}\s*$"                                            # Outlook line
    r"|From:\s.+$"                                            # Outlook header block
    r"|Le\s.{0,200}?a écrit\s*:\s*$|Am\s.{0,200}?schrieb.{0,80}:\s*$)",
    re.IGNORECASE | re.MULTILINE)
SIGNATURE = re.compile(r"^(--\s*$|Sent from my \w+|Get Outlook for \w+)", re.IGNORECASE | re.MULTILINE)

BOUNCE_SENDER = re.compile(r"mailer-daemon|postmaster|mail delivery (subsystem|system)", re.IGNORECASE)
BOUNCE_TEXT = re.compile(
    r"delivery status notification \(failure\)|undeliverable|address not found|mail delivery failed"
    r"|delivery has failed|message (wasn't|was not|could not be) delivered|couldn't be delivered"
    r"|recipient address rejected|user unknown|no such user|mailbox (unavailable|not found|does not exist)"
    r"|\b550[ -]5\.1\.1\b|\b5\.1\.1\b|returned mail: see transcript",
    re.IGNORECASE)

OPT_OUT_TEXT = re.compile(
    r"\bremove (me|us|my (email|address|name))\b|\btake (me|us) off\b|\bunsubscribe\b|\bopt[- ]?out\b|\bopt (me|us) out\b"
    r"|\bstop (e-?mailing|emailing|contacting|messaging|sending|writing)\b"
    r"|\b(don'?t|do not|never|please don'?t) (e-?mail|contact|message|write to|write|reach out to|send)"
    r" (me|us)\b"
    r"|\bno more (e-?mails|emails|messages)\b|\bleave (me|us) alone\b|\bnot interested[,.!]?\s*(please\s*)?stop\b"
    r"|\b(delete|remove) (me|us|my details) from your (list|database)\b",
    re.IGNORECASE)
# the whole new text is just "no", "no thanks", "stop", "unsubscribe" ...
SHORT_NO = re.compile(r"^(no|nope|stop|unsubscribe|remove)( thanks| thank you| please| pls)?[\s.!]*$", re.IGNORECASE)

OOO_TEXT = re.compile(
    r"\bout of (the )?office\b|\bautomatic reply\b|\bauto-?reply\b|\bautoreply\b|\bon (annual |parental |sick )?leave\b"
    r"|\bon (vacation|holiday)\b|\baway from (the|my) (office|desk)\b|\blimited access to (my )?e-?mail\b"
    r"|\bi am currently (away|out|travelling|traveling)\b|\bi'?m currently (away|out|travelling|traveling)\b"
    r"|\bwill be back (on|in|by)\b|\breturning (on|to the office)\b",
    re.IGNORECASE)


def normalize(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def strip_quoted(text: str | None) -> str:
    """Only what the person wrote now: no quoted old message, no "> " lines, no phone signature."""
    text = normalize(text)
    m = QUOTE_START.search(text)
    if m:
        text = text[: m.start()]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    text = "\n".join(lines)
    m = SIGNATURE.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


def is_bounce(text: str | None, sender: str | None = None, subject: str | None = None) -> bool:
    if sender and BOUNCE_SENDER.search(sender):
        return True
    head = f"{subject or ''}\n{normalize(text)[:2000]}"
    return bool(BOUNCE_TEXT.search(head)) and bool(re.search(r"deliver|recipient|address|mailbox|550", head, re.I))


def is_opt_out(new_text: str) -> bool:
    if OPT_OUT_TEXT.search(new_text):
        return True
    first = next((ln.strip() for ln in new_text.split("\n") if ln.strip()), "")
    words = len(re.findall(r"\S+", new_text))
    return bool(SHORT_NO.match(first)) and words <= 6


def quick_class(text: str | None, sender: str | None = None, subject: str | None = None) -> str | None:
    """bounce > opt_out > out_of_office > None (None = the reply-reader decides)."""
    if is_bounce(text, sender, subject):
        return "bounce"
    new = strip_quoted(text)
    if is_opt_out(new):
        return "opt_out"
    if OOO_TEXT.search(f"{subject or ''}\n{new}"):
        return "out_of_office"
    return None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Check one reply with the fixed rules (opt-out, bounce, out-of-office).")
    p.add_argument("file")
    p.add_argument("--sender")
    p.add_argument("--subject")
    args = p.parse_args(argv)
    text = Path(args.file).read_text(encoding="utf-8-sig")
    print(f"new text: {strip_quoted(text)[:300]!r}")
    print(f"rule result: {quick_class(text, args.sender, args.subject) or 'none (the reply-reader decides)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
