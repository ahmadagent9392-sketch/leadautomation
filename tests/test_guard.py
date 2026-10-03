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
