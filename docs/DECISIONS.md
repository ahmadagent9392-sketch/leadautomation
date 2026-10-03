# Decisions

Record every change from docs/PLAN.md here: date, what changed, why.

- 2026-10: No API key / no Python Agent SDK. All AI runs inside Claude Code on the subscription
  (subagents, skills, commands, hooks). Python is only for database, dates, checks, reports.
- 2026-10: Outreach is multi-channel (email, LinkedIn message, Upwork proposal, agency pitch, referral ask).
  The system only drafts; Ahmad sends.
- 2026-10: Ahmad decided to collect Google Maps business listings and reviews with Playwright (not the Places API).
  Known risk: against Google's terms; Google may block the IP or show captchas. Controls: public data only,
  no login, slow pace, daily caps, stop on captcha, no reviewer names stored, Places API / Apify as fallback.
- 2026-10-04 (Stage 1): The offer is NOT fixed in Stage 1. Ahmad sets the problem, customer, price, proof and skills
  later, for each client, in the dashboard (Stage 8). Why: the system itself should help find the problems.
  So config_check treats empty offer fields as warnings; `config_check --strict` must pass before outreach (Stage 5).
- 2026-10-04 (Stage 1): problems.yaml has 2 starter patterns (manual data entry, missed leads / slow replies)
  instead of one fixed pattern. They are examples to edit later.
- 2026-10-04 (Stage 1): Added deny rule `mcp__claude_ai_Gmail__reply` to .claude/settings.json (Ahmad approved),
  because guard.py does not catch "reply". Only adds a block; nothing removed.

