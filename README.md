# Opportunity Desk

A personal AI client-acquisition system built with **Claude Code**: subagents, skills, hooks and MCP.

It finds businesses that show **real, proven problems**, verifies every fact with a second AI checker,
drafts short outreach for the right channel, and tracks every conversation to a result.
It never sends anything by itself: a human approves and sends.

## How it works
```
Scout → Researcher → Checker → Rank → Writer → Critic → Human approves & sends → Follow-ups → Reply reader → Weekly learning
```

- **6 AI helpers** (Claude Code subagents), each with limited tools and its own skill
- **Evidence model**: every claim = URL + exact quote + date + grade (fact / strong / weak / guess / unknown)
- **Maker-checker**: a separate, skeptical agent verifies the researcher's work
- **Safety guard**: a PreToolUse hook blocks sending, posting and LinkedIn automation
- **Memory**: SQLite with a full event history
- **Runs on a Claude subscription**, no API key

## Status
See `docs/PROGRESS.md`.

## Demo
Run with `--demo` to use made-up sample businesses. No real prospect data is published in this repo.
