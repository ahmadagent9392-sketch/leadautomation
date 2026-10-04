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

- 2026-10-04 (Stage 2): **Supabase instead of SQLite** (Ahmad's choice). Real prospect data is now in the cloud:
  private project, Row Level Security ON with no policies (public key reads nothing), secret key only in `.env`.
  Free-tier projects pause after ~1 week without use; the daily run (Stage 8) keeps it awake, or un-pause in the dashboard.
- 2026-10-04 (Stage 2): Supabase is reached with httpx over REST (no new package). Tables are made by
  `supabase/schema.sql` (Ahmad runs it once in the SQL Editor). Status change + event, and lead + company + event,
  are saved in one go by Postgres functions (`change_status`, `add_lead`).
- 2026-10-04 (Stage 2): SQLite stays as a second backend (`--backend sqlite`): pytest uses it (no cloud in tests)
  and Stage 8 demo mode will use it. The rules live once in `scripts/db.py`, so both behave the same.
- 2026-10-04 (Stage 2): Channel names stored = policy.yaml names (`linkedin_message`...). Short names from the roadmap
  (`linkedin, upwork, agency, referral`) are accepted as aliases.
- 2026-10-04 (Stage 2): Simplified from PLAN.md §M/§F: no FTS5, jobs, timers, outcomes, lessons tables; `follow_ups`
  instead of `timers`; status list = roadmap list (nurture/stale/awaiting_send can come in Stage 6).
  Name similarity uses `difflib` (stdlib) instead of `rapidfuzz`. Block list stores plain lower-case email/domain, no hash.
- 2026-10-04 (Stage 2): Leads from platform URLs (Upwork, HN, LinkedIn, Google Maps...) get no company domain,
  because the URL host is not the company. Duplicate check then uses the exact URL and `--company` name.

- 2026-10-04 (Stage 3): **The code enforces proof**, not only the agents: quote must be in the saved snapshot
  (else UNKNOWN), stale / undated proof is lowered, the checker cannot raise grades, and `move ... verified`
  is refused without checked problem proof + a contact. Why: an AI could "agree" with fake proof; code cannot.
- 2026-10-04 (Stage 3): Added `evidence.topic` (pain, company, why_now, owner, contact, impact). Not in the plan
  ("no table changes") — needed so that a company fact ("they are a dental clinic") cannot count as problem proof.
  Stage 4 ranking also needs it. Applied to Supabase with the MCP; `schema.sql` updated (safe to re-run).
- 2026-10-04 (Stage 3): Added `desk.py start-research` (not in the roadmap list): it enforces the daily cap
  `max_research_per_day` and max 2 rounds per lead (1 + one extra after NEED_MORE).
- 2026-10-04 (Stage 3, Ahmad's choice): pages that block bots → Ahmad pastes the text (`snapshot.py --from-file`).
- 2026-10-04 (Stage 3, Ahmad's choice): second NEED_MORE → lead stays `researched`, questions saved as unknowns;
  Ahmad decides. Not auto-rejected.
- 2026-10-04 (Stage 3, Ahmad's choice): checker stays on Opus.
- 2026-10-04 (Stage 3): Quotes are copied from the snapshot .txt file, not from WebFetch (WebFetch returns a
  summary, not the exact page text). Fuzzy match allows only tiny typos (similarity ≥ 0.90).
