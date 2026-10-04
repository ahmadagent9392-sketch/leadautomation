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

- 2026-10-04 (Stage 4, Ahmad's choice): offer proof gate = **warning** while `offer.yaml` status is not `decided`;
  hard gate once it is `decided`. Why: the offer is set later; Stage 5 `config_check --strict` blocks outreach anyway.
- 2026-10-04 (Stage 4, Ahmad's choice): fit and value are set by Claude in `/cards` with a written rubric
  (`desk.py set-score`), with one-line reasons. All gates, urgency and priority are code.
- 2026-10-04 (Stage 4, Ahmad's choice): gate fail → `rejected`; a qualified lead whose proof gets old → `rejected`;
  value below `min_value_band` → rejected; urgency 0 is allowed (priority 0, sorts last).
- 2026-10-04 (Stage 4): no score yet → lead stays `verified` ("waiting"), not rejected.
- 2026-10-04 (Stage 4): extra gate from policy.yaml `excluded`: personal email (gmail, yahoo...) on the `email`
  channel → rejected. No email at all is only a warning.
- 2026-10-04 (Stage 4): new column `opportunities.rank_info` (jsonb) for reasons, gate results and the "why" line
  (PLAN.md says "store each factor and its explanation"). Applied to Supabase with the MCP.
- 2026-10-04 (Stage 4): cards are static files (`cards/*.md`, `cards/index.html`), not the FastAPI dashboard from
  PLAN.md Phase 4. The dashboard is Stage 8.

- 2026-10-04 (Stage 5, Ahmad's choice): `config_check --strict` blocks only the **export** step (Gmail draft /
  copy-paste file). Writing, critic review and approval work before the offer is decided, so drafts can be tested.
- 2026-10-04 (Stage 5, Ahmad's choice): Gmail drafts through the **claude.ai Gmail connector**, not an own Python
  Gmail API script. Python checks and prints the draft; Claude calls `create_draft` + `label_thread`. No new package,
  no Google Cloud setup. guard.py already blocks `send_message` and `forward`; settings.json denies `reply`.
- 2026-10-04 (Stage 5): the email footer is **added by code** at export (policy.yaml template + me.yaml), not stored
  in the draft. Why: the address can be filled later without re-writing or re-approving drafts. The approval hash
  covers subject + text (what Ahmad judges); the footer is fixed policy text.
- 2026-10-04 (Stage 5): `max_first_emails_per_day` is counted on Gmail drafts made today (the system never sends,
  so the draft is the closest point to sending that code controls).
- 2026-10-04 (Stage 5): "max 2 rewrites" = max 3 saved drafts per lead per day, enforced in `save-draft`.
- 2026-10-04 (Stage 5): Ahmad rejecting a draft keeps the lead open (`draft_ready`); `--close-lead` moves it to
  `rejected`. Ahmad's edit is saved as a new draft (the old one is kept) and needs no critic.
- 2026-10-04 (Stage 5): no table change; draft state comes from `messages.critic`, `approvals` and events.
  `PERSONAL_EMAIL_DOMAINS` moved from rank.py to checks.py (one list for both).
- 2026-10-04 (Stage 5): the writer agent got the Write tool, only for `data/drafts/` (multi-line text files are
  safer than quoting long text on the Windows command line).

- 2026-10-04 (Stage 6, Ahmad's choice): bounce → block the email, lead stays `contacted`, /today asks Ahmad to find
  a new contact or close it. `set-contact` is allowed on a contacted lead after a bounce.
- 2026-10-04 (Stage 6, Ahmad's choice): after the last touch, no reply for 10 business days → `no_response`
  automatically (`no_response_after_business_days` in cadence.yaml).
- 2026-10-04 (Stage 6, Ahmad's choice): opt-out blocks the **email** (recipient + the reply's sender), not the whole
  domain, and closes the lead (`opted_out`).
- 2026-10-04 (Stage 6, Ahmad's choice): no reply-draft pipeline. Ahmad answers positive replies himself; /today
  shows them until `desk.py reply-done`.
- 2026-10-04 (Stage 6): no new statuses. "Stale" and "bounced" are /today lists, not statuses. Follow-ups keep the
  lead `contacted`; drafting, review and approval of touch 2-5 do not change the status.
- 2026-10-04 (Stage 6): follow-up dates count business days (Mon-Fri) from the **first** sent message (cadence.yaml),
  with a min gap of 2 business days after the last one. Due dates are saved in `follow_ups`.
- 2026-10-04 (Stage 6): fixed rules (`scripts/replies.py`) beat the reply-reader for opt-out and bounce; a short
  "no" is an opt-out (our footer promises it). Quoted text is cut first, so our own footer cannot trigger it.
- 2026-10-04 (Stage 6): Gmail is read with the claude.ai connector in `/sync` (get_thread, list_drafts,
  search_threads), like Stage 5. Python never talks to Gmail. Email follow-ups are drafts in the same thread
  (`replyToMessageId`); a new contact after a bounce gets a new thread.
- 2026-10-04 (Stage 6): `replies` table got 6 columns (gmail_message_id unique for de-duplication, sender, subject,
  next_action, note, handled_at). Applied with the Supabase MCP (migration `stage6_replies`).
- 2026-10-04 (Stage 6): `max_first_emails_per_day` counts only first messages (touch 1); follow-ups use
  `max_drafts_per_day` and the 5-touch limit.
- 2026-10-04 (Stage 6): `/sync` and `log-reply` save reply text in `data/replies/` (git-ignored).

