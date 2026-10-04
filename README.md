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
- **Memory**: Supabase (Postgres) with a full event history; SQLite for tests and demo
- **Runs on a Claude subscription**, no API key

## Status
See `docs/PROGRESS.md`.

## Daily use
- `python scripts/dashboard.py` → http://127.0.0.1:8765 (only on this PC): today's tasks, cards, pipeline, numbers,
  "Search an idea" box.
- Morning run: `python scripts/daily.py run` (Claude Code `/daily-run`, fixed tool list, never sends).
  Windows schedule: `scripts/schedule_windows.ps1 -On | -Off | -Status`. Pause everything: create `data/STOP`.

## Demo
`python scripts/demo.py` fills `data/demo.db` with made-up sample businesses (`.example` domains).
Then `python scripts/dashboard.py --demo` (every desk command also takes `--demo`).
No real prospect data is published in this repo.
