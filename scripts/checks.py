#!/usr/bin/env python3
"""Code checks for outreach drafts (Stage 5). The AI writes and reviews; this code has the last word.

Usage:
    python scripts/checks.py MESSAGE_ID            # checks for a draft (before approval)
    python scripts/checks.py MESSAGE_ID --final    # checks before Gmail / copy-paste (stricter)

What is checked:
  - word limit per channel (config/policy.yaml word_limits), counted without the email footer
  - email: subject 1-4 words; a published / verified recipient email; no personal email (gmail...)
  - banned phrases (config/policy.yaml)
  - email footer: name, postal address, opt-out line (added by code at export, never typed by the writer)
  - block list: recipient email and company domain
  - evidence ids: at least one, all of this lead, all checked, none UNKNOWN, at least one problem proof
  - linkedin_message: no link in the first message
  - left-over placeholders like {name}, [Company], TODO
  - follow-ups (touch 2-5): no "just following up" / "bumping this"; not a copy of an earlier message;
    an email follow-up may have no subject (it goes in the same Gmail thread)

ERROR = the draft cannot go out. WARNING = Ahmad should look at it.
Also takes --backend supabase|sqlite and --db PATH (like desk.py). Exit code: 0 = no errors, 1 = errors.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ERROR, WARNING = "ERROR", "WARNING"
MAX_SUBJECT_WORDS = 4
PROOF_GRADES = ("CONFIRMED_FACT", "STRONG_SIGNAL", "WEAK_SIGNAL")
EMAIL_OK = ("published", "verified")
PERSONAL_EMAIL_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com",
                          "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
                          "gmx.com", "mail.com", "yandex.com", "zoho.com"}

PLACEHOLDER = re.compile(r"\{[A-Za-z_ ]{1,30}\}|\[[A-Z][A-Za-z _'-]{0,30}\]|<[A-Z][A-Z _]{1,30}>|\bTODO\b|\bTBD\b|XXX")
LINK = re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\.(com|net|org|io|co|ai|dev|app|me|pk|uk)\b(/|\s|$|[.,!?])",
                  re.IGNORECASE)
YOU_WORDS = re.compile(r"\b(you|your|you're|yours)\b", re.IGNORECASE)
ME_WORDS = re.compile(r"\b(i|i'm|i've|me|my|we|we're|our|us)\b", re.IGNORECASE)
BUMP = re.compile(r"\b(just )?following up\b|\bbump(ing)? (this|it|up)\b|\bcircling back\b|\bchecking in\b"
                  r"|\bjust checking\b|\bany update\b|\bdid you (get|see) my (last )?(email|message)\b"
                  r"|\bfloat(ing)? this\b", re.IGNORECASE)
MAX_SIMILAR = 0.60          # a follow-up this similar to an earlier message repeats it
OPT_OUT = re.compile(r"\b(reply|unsubscribe|opt[- ]?out)\b.*\b(no|stop|remove|again|unsubscribe)\b",
                     re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Problem:
    level: str          # ERROR or WARNING
    text: str

    def __str__(self) -> str:
        return f"{self.level:<7} {self.text}"


def errors(problems: list[Problem]) -> list[Problem]:
    return [p for p in problems if p.level == ERROR]


def word_count(text: str | None) -> int:
    return len(re.findall(r"\S+", text or ""))


def normalize_text(text: str | None) -> str:
    """Same text -> same hash on Windows and Linux (line endings, spaces at line ends)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def body_sha256(subject: str | None, body: str | None) -> str:
    """Hash of exactly what Ahmad approves (subject + body). Any change later = approval is no longer valid."""
    text = normalize_text(subject) + "\n\n" + normalize_text(body)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_footer(policy: dict, me: dict) -> str:
    """The email footer from policy.yaml, filled from me.yaml. Empty fields stay empty (checks catch it)."""
    template = str(policy.get("email_footer") or "")
    name = str(me.get("business_name") or "").strip() or str(me.get("name") or "").strip()
    text = template.replace("{name}", name).replace("{postal_address}", str(me.get("postal_address") or "").strip())
    return normalize_text(text)


def footer_problems(policy: dict, me: dict, final: bool) -> list[Problem]:
    footer = render_footer(policy, me)
    level = ERROR if final else WARNING
    out: list[Problem] = []
    if not footer:
        return [Problem(ERROR, "email footer is empty: set email_footer in config/policy.yaml")]
    if not str(me.get("name") or "").strip() and not str(me.get("business_name") or "").strip():
        out.append(Problem(level, "email footer has no name: fill 'name' in config/me.yaml"))
    if not str(me.get("postal_address") or "").strip():
        out.append(Problem(level, "email footer has no postal address (required by law for cold email): "
                                  "fill 'postal_address' in config/me.yaml"))
    if not OPT_OUT.search(footer):
        out.append(Problem(ERROR, "email footer has no opt-out line (like: reply \"no\" and I won't email you again)"))
    if PLACEHOLDER.search(footer):
        out.append(Problem(ERROR, f"email footer still has a placeholder: {PLACEHOLDER.search(footer).group(0)}"))
    return out


def similarity(a: str | None, b: str | None) -> float:
    a, b = normalize_text(a).lower(), normalize_text(b).lower()
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def check_draft(*, channel: str, subject: str | None, body: str | None, evidence_ids: list[int],
                lead: dict, evidence: list[dict], recipient: dict | None, policy: dict, me: dict,
                is_blocked: Callable[[str], bool], touch_number: int = 1, final: bool = False,
                previous_bodies: list[str] | None = None) -> list[Problem]:
    """All code checks for one draft. Returns problems ([] = clean)."""
    out: list[Problem] = []
    body = normalize_text(body)
    subject = normalize_text(subject)

    allowed = list(policy.get("channels_allowed") or [])
    if channel not in allowed:
        out.append(Problem(ERROR, f"channel '{channel}' is not allowed (config/policy.yaml channels_allowed)"))
    elif channel != lead.get("channel"):
        out.append(Problem(WARNING, f"draft channel '{channel}' is not the lead's channel '{lead.get('channel')}'"))

    # length
    if not body:
        out.append(Problem(ERROR, "the message text is empty"))
    limit = int((policy.get("word_limits") or {}).get(channel, 0) or 0)
    words = word_count(body)
    if limit and words > limit:
        out.append(Problem(ERROR, f"too long: {words} words, limit for {channel} is {limit}"))

    # subject (email only)
    if channel == "email":
        n = word_count(subject)
        if n == 0 and touch_number == 1:
            out.append(Problem(ERROR, "email needs a subject"))
        elif n > MAX_SUBJECT_WORDS:
            out.append(Problem(ERROR, f"subject has {n} words, max {MAX_SUBJECT_WORDS}"))

    # banned phrases and placeholders
    text_l = f"{subject}\n{body}".lower()
    for phrase in policy.get("banned_phrases") or []:
        if str(phrase).lower() in text_l:
            out.append(Problem(ERROR, f"banned phrase: \"{phrase}\""))
    found = PLACEHOLDER.search(f"{subject}\n{body}")
    if found:
        out.append(Problem(ERROR, f"left-over placeholder: {found.group(0)}"))

    # channel rules
    if channel == "linkedin_message" and touch_number == 1 and LINK.search(body):
        out.append(Problem(ERROR, "no links in a first LinkedIn message"))
    if channel == "email" and OPT_OUT.search(body):
        out.append(Problem(WARNING, "the body has an opt-out line; the footer adds it already (remove it from the body)"))
    # follow-ups must add something new
    if touch_number > 1:
        bump = BUMP.search(body)
        if bump:
            out.append(Problem(ERROR, f"follow-up says \"{bump.group(0)}\": add a new fact, idea or example instead"))
        for i, old in enumerate(previous_bodies or [], start=1):
            score = similarity(body, old)
            if score >= MAX_SIMILAR:
                out.append(Problem(ERROR, f"follow-up repeats earlier message {i} (similarity {score:.2f}); "
                                          "say something new"))
                break
    if body and len(ME_WORDS.findall(body)) > len(YOU_WORDS.findall(body)):
        out.append(Problem(WARNING, "talks more about me/we than about them (you/your)"))

    # recipient and block list
    domain = lead.get("company_domain")
    if domain and is_blocked(domain):
        out.append(Problem(ERROR, f"{domain} is on the block list"))
    email = ((recipient or {}).get("email") or "").strip().lower()
    if channel == "email":
        if not email:
            out.append(Problem(ERROR, "no recipient email (save a published email with desk.py set-contact)"))
        else:
            if (recipient or {}).get("email_status") not in EMAIL_OK:
                out.append(Problem(ERROR, f"{email} is not published / verified (never email a guessed address)"))
            if email.rsplit("@", 1)[-1] in PERSONAL_EMAIL_DOMAINS:
                out.append(Problem(ERROR, f"{email} is a personal email; no cold email to personal addresses"))
        out.extend(footer_problems(policy, me, final))
    if email and is_blocked(email):
        out.append(Problem(ERROR, f"{email} is on the block list"))

    # proof
    by_id = {e["id"]: e for e in evidence}
    if not evidence_ids:
        out.append(Problem(ERROR, "no evidence ids: every draft must say which proof it uses (--evidence E1,E2)"))
    cited = []
    for eid in evidence_ids:
        ev = by_id.get(eid)
        if not ev:
            out.append(Problem(ERROR, f"E{eid} is not evidence of lead #{lead.get('id')}"))
            continue
        if not ev.get("verified"):
            out.append(Problem(ERROR, f"E{eid} is not checked by the checker"))
        elif ev.get("grade") == "UNKNOWN":
            out.append(Problem(ERROR, f"E{eid} is UNKNOWN (quote not proven); do not use it"))
        elif ev.get("grade") == "INFERENCE":
            out.append(Problem(WARNING, f"E{eid} is an INFERENCE: the draft must say it as a guess (\"it looks like\")"))
        cited.append(ev)
    if evidence_ids and not any(e.get("verified") and e.get("topic") == "pain" and e.get("grade") in PROOF_GRADES
                                for e in cited):
        out.append(Problem(ERROR, "no checked problem proof (topic 'pain') among the evidence ids"))
    return out


def main(argv: list[str] | None = None) -> int:
    import db  # here, not at the top: db imports this file

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Check one outreach draft.")
    parser.add_argument("message_id", help="draft id, like 12 or M12")
    parser.add_argument("--final", action="store_true", help="checks before Gmail / copy-paste")
    parser.add_argument("--backend", choices=["supabase", "sqlite"], default=None)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)
    store = None
    try:
        store = db.open_store(args.backend, args.db)
        desk = db.Desk(store)
        msg, _, problems = desk.check_message(db.message_id(args.message_id), final=args.final)
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if store is not None:
            store.close()
    for p in problems:
        print(str(p))
    bad = errors(problems)
    print(f"\nM{msg['id']}: {len(bad)} error(s), {len(problems) - len(bad)} warning(s)."
          + (" OK." if not bad else " Fix the errors."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
