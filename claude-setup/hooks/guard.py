#!/usr/bin/env python3
"""Opportunity Desk safety guard (Claude Code PreToolUse hook).

Blocks:
  - any MCP tool that sends/submits/publishes (drafts are allowed)
  - any LinkedIn automation (MCP tools, web fetch, browser, shell)
  - shell commands that send email or delete the data folder

Exit code 2 = block the tool call; the message on stderr is shown to Claude.
Do not edit or delete this file.
"""
import json
import re
import sys

SEND_WORDS = ("send", "submit", "publish", "post_message", "create_post", "reply_to", "forward")
ALLOW_WORDS = ("draft",)
SHELL_BLOCK = [
    r"smtplib", r"sendmail", r"messages\(\)\.send", r"messages/send", r"\.send_message\(",
    r"users\.messages\.send", r"(curl|wget|requests|httpx|playwright).*linkedin\.com", r"rm\s+-rf?\s+.*data",
    r"Remove-Item\s+.*data.*-Recurse",
]


def block(reason: str) -> None:
    print(f"BLOCKED by guard.py: {reason}. Opportunity Desk only creates drafts; Ahmad sends by hand.",
          file=sys.stderr)
    sys.exit(2)


def check(event: dict) -> None:
    tool = str(event.get("tool_name", ""))
    tool_l = tool.lower()
    tool_input = event.get("tool_input", {}) or {}
    blob = json.dumps(tool_input).lower()

    # 1) LinkedIn: never automate (saving a profile URL Ahmad found by hand is fine)
    if "linkedin" in tool_l:
        block("LinkedIn automation is not allowed")
    if "linkedin.com" in blob and (tool == "WebFetch" or tool_l.startswith("mcp__")):
        block("opening LinkedIn pages automatically is not allowed")

    # 2) MCP tools that send/submit/publish
    if tool_l.startswith("mcp__"):
        action = tool_l.split("__")[-1]
        if any(w in action for w in SEND_WORDS) and not any(a in action for a in ALLOW_WORDS):
            block(f"tool '{tool}' looks like it sends or publishes")

    # 3) Shell commands
    if tool in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command", ""))
        for pattern in SHELL_BLOCK:
            if re.search(pattern, cmd, flags=re.IGNORECASE):
                block(f"shell command matches blocked pattern '{pattern}'")


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # not our business; let normal permissions decide
    check(event)
    sys.exit(0)


if __name__ == "__main__":
    main()
