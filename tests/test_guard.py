"""Tests for .claude/hooks/guard.py (the file itself is never changed)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / ".claude" / "hooks" / "guard.py"
SETTINGS = ROOT / ".claude" / "settings.json"

spec = importlib.util.spec_from_file_location("guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def is_blocked(tool_name: str, tool_input: dict | None = None) -> bool:
    try:
        guard.check({"tool_name": tool_name, "tool_input": tool_input or {}})
    except SystemExit as exc:
        return exc.code == 2
    return False


@pytest.mark.parametrize("tool, tool_input", [
    ("mcp__claude_ai_Gmail__send_message", {"to": "a@example.com"}),
    ("mcp__claude_ai_Gmail__forward", {}),
    ("mcp__claude_ai_Lovable__send_message", {}),
    ("mcp__claude_ai_Apollo_io__apollo_emailer_messages_send_now", {}),
    ("mcp__claude_ai_Higgsfield__publish_website", {}),
    ("mcp__linkedin__get_profile", {}),
    ("WebFetch", {"url": "https://www.linkedin.com/in/someone"}),
    ("Bash", {"command": "python -c 'import smtplib'"}),
    ("Bash", {"command": "rm -rf data/"}),
    ("Bash", {"command": "curl https://linkedin.com/feed"}),
    ("PowerShell", {"command": "Remove-Item data -Recurse"}),
])
def test_blocked(tool, tool_input):
    assert is_blocked(tool, tool_input)


@pytest.mark.parametrize("tool, tool_input", [
    ("mcp__claude_ai_Gmail__create_draft", {"to": "a@example.com"}),
    ("mcp__claude_ai_Gmail__update_draft", {}),
    ("mcp__claude_ai_Gmail__search_threads", {}),
    # Stage 6: follow-up draft in the same thread, and the read-only tools /sync uses
    ("mcp__claude_ai_Gmail__create_draft", {"to": ["a@example.com"], "body": "hi", "replyToMessageId": "g-1"}),
    ("mcp__claude_ai_Gmail__get_thread", {"threadId": "t-1", "messageFormat": "PLAIN_TEXT"}),
    ("mcp__claude_ai_Gmail__list_drafts", {}),
    ("Read", {"file_path": "README.md"}),
    ("Bash", {"command": "python -m pytest -q"}),
    ("WebFetch", {"url": "https://example.com"}),
])
def test_allowed(tool, tool_input):
    assert not is_blocked(tool, tool_input)


@pytest.mark.xfail(strict=True, reason="KNOWN GAP: guard.py does not block Gmail 'reply'; "
                                       "settings.json deny rule covers it (see next test)")
def test_gmail_reply_blocked_by_guard():
    assert is_blocked("mcp__claude_ai_Gmail__reply")


def test_gmail_reply_denied_in_settings():
    deny = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "mcp__claude_ai_Gmail__reply" in deny


def run_guard_script(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD)], input=payload,
                          capture_output=True, text=True, timeout=10)


def test_script_blocks_send_with_exit_code_2():
    result = run_guard_script(json.dumps({"tool_name": "mcp__claude_ai_Gmail__send_message",
                                          "tool_input": {}}))
    assert result.returncode == 2
    assert "BLOCKED by guard.py" in result.stderr


def test_script_allows_draft_with_exit_code_0():
    result = run_guard_script(json.dumps({"tool_name": "mcp__claude_ai_Gmail__create_draft",
                                          "tool_input": {}}))
    assert result.returncode == 0
    assert result.stderr == ""


def test_script_ignores_bad_json():
    assert run_guard_script("not json").returncode == 0


# ---------- Stage 5: every tool the claude.ai Gmail connector exposes (list seen on 2026-10-04) ----------
GMAIL = "mcp__claude_ai_Gmail__"
GMAIL_TOOLS = (
    "apply_sensitive_message_label", "apply_sensitive_thread_label", "create_draft", "create_label",
    "delete_draft", "delete_label", "forward", "get_draft", "get_message", "get_thread", "label_message",
    "label_thread", "list_drafts", "list_labels", "mark_message_spam", "mark_thread_spam", "search_threads",
    "send_message", "trash_message", "trash_thread", "unlabel_message", "unlabel_thread", "unmark_message_spam",
    "unmark_thread_spam", "untrash_message", "untrash_thread", "update_draft", "update_label",
    "update_message_labels",
)
GMAIL_SENDS = {"send_message", "forward"}          # the only tools that put an email out


@pytest.mark.parametrize("action", GMAIL_TOOLS)
def test_every_gmail_tool(action):
    assert is_blocked(GMAIL + action) == (action in GMAIL_SENDS)


def test_gmail_send_tools_are_in_the_list():
    assert GMAIL_SENDS <= set(GMAIL_TOOLS)


@pytest.mark.xfail(strict=True, reason="KNOWN GAP: a future Gmail tool named 'send_draft' would pass guard.py, "
                                       "because any name with 'draft' is allowed. Not exposed today.")
def test_gmail_send_draft_blocked_by_guard():
    assert is_blocked(GMAIL + "send_draft")


# ---------- Stage 7b: work with my screen ----------
LI = "https://www." + "linkedin.com"      # split so this file's own text never looks like a fetch


@pytest.mark.parametrize("tool, tool_input", [
    ("mcp__claude-in-chrome__navigate", {"url": LI + "/feed", "tabId": 1}),
    ("mcp__claude-in-chrome__navigate", {"url": "linkedin.com/in/someone"}),
    ("mcp__claude_ai_Apify__call-actor", {"input": {"startUrls": [LI + "/in/x"]}}),
    ("Bash", {"command": "python -c \"import requests; requests.get('" + LI + "/in/x')\""}),
])
def test_linkedin_opening_still_blocked(tool, tool_input):
    assert is_blocked(tool, tool_input)


@pytest.mark.parametrize("tool, tool_input", [
    # /read-my-tab: read the ONE tab Ahmad put in the Claude group (no URL in the call)
    ("mcp__claude-in-chrome__tabs_context_mcp", {"createIfEmpty": False}),
    ("mcp__claude-in-chrome__get_page_text", {"tabId": 7}),
    # storing a LinkedIn link Ahmad gave (never fetched)
    ("Bash", {"command": "python scripts/screen.py save --text-file data/paste/tab-1.txt "
                         "--url \"" + LI + "/posts/x\" --source chrome-tab"}),
    ("Bash", {"command": "python scripts/scout.py keep-warm R3 --channel referral_ask --reason \"talked twice\""}),
    ("Write", {"file_path": "data/paste/tab-1.txt", "content": "URL " + LI + "/posts/x"}),
    ("Read", {"file_path": "data/linkedin/Connections.csv"}),
])
def test_read_my_tab_and_stored_links_allowed(tool, tool_input):
    assert not is_blocked(tool, tool_input)


def test_read_my_tab_command_allows_only_two_chrome_tools():
    text = (ROOT / ".claude" / "commands" / "read-my-tab.md").read_text(encoding="utf-8")
    loads = text.split("select:", 1)[1].split("`", 1)[0]
    assert sorted(loads.split(",")) == ["mcp__claude-in-chrome__get_page_text",
                                        "mcp__claude-in-chrome__tabs_context_mcp"]
    for words in ("no `navigate`", "no `computer`", "One tab, one read", "Never run this from `/daily-run`"):
        assert words in text


def test_unattended_runs_have_no_chrome_tools():
    sys.path.insert(0, str(ROOT / "scripts"))
    import daily
    assert not any("chrome" in t for t in daily.ALLOWED_TOOLS)
