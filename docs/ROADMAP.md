# Build Roadmap — 10 stages

How to use this file: Ahmad types
`Read CLAUDE.md, docs/PROGRESS.md and docs/ROADMAP.md. Do Stage N only.`
Claude Code then does ONLY that stage: plan → wait for OK → build → tests → update PROGRESS.md → explain how to test.

Reference for details: `docs/PLAN.md` (Part II = build spec). Where PLAN.md says "Agent SDK" or "API key",
use Claude Code subagents/skills/commands instead (see docs/DECISIONS.md).

---

## Stage 0 — Get ready (Ahmad only)
- Install Git, Python 3.11+, Node.js 20+; run `claude update`.
- Fill in `docs/MY_OFFER.md`.
- `git init`, first commit.

---

## Stage 1 — Foundation, offer, safety check
**Goal:** a clear offer, filled config files, and proof that the safety guard works.

Tasks:
1. Read `docs/MY_OFFER.md`. Interview Ahmad with short questions until it is specific:
   problem, customer type, result, price, days, proof, who NOT to work with.
   Push back if it is vague ("AI automation" is too vague).
2. Fill `config/me.yaml`, `config/offer.yaml`, `config/problems.yaml` (1–2 problem patterns) from the answers.
   Keep `config/policy.yaml`, `config/cadence.yaml`, `config/sources.yaml` as given unless Ahmad wants changes.
3. Create `scripts/config_check.py` that loads all YAML files and fails with a clear message if a required field is empty.
   Add `tests/test_config.py`.
4. Test the guard hook: show Ahmad how `.claude/hooks/guard.py` blocks a fake "send email" tool call
   (run `python .claude/hooks/guard.py` with sample JSON on stdin). Add `tests/test_guard.py`.
5. Create `requirements.txt` (pyyaml, httpx, pytest).
6. Update PROGRESS.md.

**Ahmad checks:** offer.yaml is really his offer · `python scripts/config_check.py` passes · guard test blocks sending.
**Done when:** checks pass → `git commit -m "stage 1"`.

---

## Stage 2 — Database + CLI (the memory)
**Goal:** a SQLite database and simple commands to add and track leads by hand.

Tasks:
1. `scripts/db.py`: create tables in `data/desk.db` — companies, people, evidence, snapshots, opportunities,
   messages, replies, follow_ups, approvals, suppression, events (see PLAN.md Part II §M, simplified).
2. Status flow (PLAN.md §F, simplified): new → researched → verified → qualified → draft_ready → approved →
   contacted → replied → meeting → proposal → won / lost / no_response / rejected / opted_out.
   Invalid moves raise an error. Every change writes to `events`.
3. `scripts/desk.py` CLI: `init`, `add-lead --url URL --note TEXT --channel {email,linkedin,upwork,agency,referral}`,
   `list [--status S]`, `show ID`, `move ID STATUS --reason TEXT`, `history ID`, `block EMAIL_OR_DOMAIN --reason`.
4. Duplicate check on add: same domain or very similar company name → warn and do not add.
5. pytest: all allowed/blocked moves, add/list/show, duplicate check, block list.

**Ahmad checks:** add 3 real links · `list` shows them · try `move 1 won` from new → refused · tests pass.
**Done when:** commit "stage 2".

---

## Stage 3 — Researcher + Checker
**Goal:** proven facts about each lead. Fake or weak facts are caught.

Tasks:
1. Improve `.claude/agents/researcher.md` and `.claude/agents/checker.md` and their skills
   (`research-company`, `check-evidence`) to use `scripts/desk.py` for saving.
2. `scripts/snapshot.py`: fetch a URL, save page text to `data/snapshots/<sha256>.txt`, record in `snapshots`.
3. `scripts/quote_check.py`: check a quote exists in a saved snapshot (normalize spaces/case; small fuzzy tolerance).
   Quote not found → grade becomes UNKNOWN.
4. Add `desk.py` commands the agents use: `add-evidence`, `set-contact`, `set-research`, `verify-evidence`.
5. `.claude/commands/research.md` → `/research ID`: run researcher, then checker, then move status
   (verified / rejected with reason). One extra research round max if checker says NEED_MORE.
6. pytest for snapshot + quote check (present, missing, paraphrased, stale date).

**Ahmad checks:** `/research` on 3 leads · open 5 proof links himself, quotes are really there ·
add 1 fake lead with an invented claim → checker FAILS it.
**Done when:** commit "stage 3". Post idea: "My AI checker caught my AI researcher's made-up fact."

---

## Stage 4 — Ranking + opportunity cards (first real value)
**Goal:** only strong leads pass; best first; each lead readable in 1 minute.

Tasks:
1. `scripts/rank.py`: hard gates (PLAN.md §6/§P): problem proof ≥ STRONG (or 2 WEAK from different sources),
   fresh (decay days in config/problems.yaml), fit ≥ 2, owner role known, proof exists in offer.yaml,
   not blocked, not excluded. Pass → qualified; fail → rejected + reason.
2. Priority = evidence(1–3) × fit(1–3) × urgency(0–2) × value(1–3). Save each factor + a one-line "why".
3. `scripts/cards.py`: `cards/<id>.md` + `cards/index.html` (qualified, best first): company, problem,
   proof links + quotes + grades, why now, who to contact, channel, unknowns.
4. `/cards` command. pytest for gates and priority math.

**Ahmad checks:** open `cards/index.html`; for each card, "would I really contact them?" — target ≥ 6 of 10 yes.
**Done when:** commit "stage 4".

---

## Stage 5 — Writer + Critic + approvals + Gmail drafts
**Goal:** short, proven, multi-channel drafts; Ahmad approves; email drafts appear in Gmail. Nothing is sent.

Tasks:
1. Improve `.claude/agents/writer.md`, `critic.md`, skills `write-outreach`, `review-outreach`.
   Channels: email, linkedin_message, upwork_proposal, agency_pitch, referral_ask.
2. `scripts/checks.py`: word limits per channel, banned phrases (config/policy.yaml), email footer
   (name, postal address, opt-out line), block-list check, evidence ids present. pytest.
3. `/draft ID` command: writer → checks → critic (max 2 rewrites) → status draft_ready.
4. `/approve ID`: show draft + proof; Ahmad answers approve / edit / reject + reason; save in `approvals`
   with a hash of the text (edited after approval → approval invalid).
5. Gmail: check `/mcp` for a Gmail connector. If none, help Ahmad set up an official Google OAuth based Gmail MCP
   or a small Python script using the Gmail API with **draft + read scopes only**. Approved emails → Gmail draft
   with label `desk`. LinkedIn/Upwork/agency drafts → saved to `cards/<id>-message.txt` for copy-paste.
6. Confirm `guard.py` blocks every send tool name that the Gmail connection exposes.

**Ahmad checks:** read 5 drafts (short, true, specific) · ask Claude to send one → blocked ·
approved email appears in Gmail Drafts · he sends 1 himself.
**Before volume:** second domain + SPF/DKIM/DMARC; start 5 emails/day, max 20.
**Done when:** commit "stage 5".

---

## Stage 6 — Follow-ups + reply reader
**Goal:** no thread is forgotten; replies are sorted; opt-outs are respected.

Tasks:
1. `scripts/followups.py`: schedule from `config/cadence.yaml` (business days 3, 7, 14, 24; max 5 touches).
   Stop on reply, opt-out, bounce, max touches. not_now → remind at their date or +60 days.
   No activity 21 days → "stale".
2. `/sync`: read Gmail threads labelled `desk`; mark sent messages as contacted; new replies → reply-reader agent.
   For LinkedIn/Upwork: `desk.py log-reply ID --text "..."` (Ahmad pastes replies by hand).
3. Improve `.claude/agents/reply-reader.md` + skill `read-replies`. Opt-out/bounce → block list immediately.
4. Each follow-up adds something new (new fact, mini idea, example). Writer + critic reused; Ahmad approves.
5. `/today`: what needs Ahmad today (approvals, follow-ups due, replies, stale).
6. `tests/fixtures/replies/` with 15 sample replies (including different opt-out wordings); test classification
   rules that are deterministic (keywords for opt-out/bounce/out-of-office).

**Ahmad checks:** reply "please remove me" to a test email → blocked · `/today` correct · samples sorted correctly.
**Done when:** full loop works by hand. Commit "stage 6".

---

## Stage 7 — Scout: automatic finding
**Goal:** fresh leads from free sources, not only pasted links.

Tasks:
1. `scripts/sources/hn.py`: Hacker News Algolia API (no key): "Who is hiring", "Freelancer? Seeking freelancer?",
   "Ask HN" in the last 30 days, keywords from config/problems.yaml.
2. `scripts/sources/jobs.py`: job post / career page URLs from config/sources.yaml; save new posts.
3. `scripts/sources/pagespeed.py`: Google PageSpeed API (free key in `.env`) → website evidence.
4. `scripts/sources/agencies.py`: helper to add agency candidates from a list of URLs Ahmad collects (Clutch,
   Shopify Partners pages he opens by hand) — no scraping of logged-in sites.
5. Improve `.claude/agents/scout.md` + skill `find-signals`. Max new leads/day from config/policy.yaml.
6. `/discover` command. pytest: duplicates, daily cap, old posts ignored.
7. **`/search-idea "IDEA"` command** (Ahmad types any idea, e.g. "dental clinics that need booking automation"):
   turn the idea into keywords + a temporary problem pattern → search HN, job pages and web search →
   Scout keeps only real signals → save leads tagged `idea=<short name>`. Max 10 leads per search.
   Show the keywords used, so Ahmad can see why each lead was found. Save good ideas as new patterns
   in config/problems.yaml only if Ahmad says yes.
8. **Google Maps with Playwright** (Ahmad's decision, see DECISIONS.md) — `scripts/sources/gmaps.py`:
   - Input: a search like "dental clinic in Houston TX" (from config/sources.yaml or /search-idea).
   - Collect only public business info: name, category, address, website, phone, rating, review count,
     and up to 10 recent review texts with their dates (low ratings first).
   - Reviews that show a problem (e.g. "never called back", "booking didn't work") → evidence with
     source_type=review, grade WEAK_SIGNAL (2+ similar reviews → STRONG_SIGNAL).
   - Then check the business website (Playwright): contact page, booking form works?, owner name.
   - Safety limits (in config/policy.yaml): no Google login, headless with a normal browser profile,
     3–8 second random wait between actions, max 3 searches and 60 businesses per day, stop the run
     immediately on captcha / "unusual traffic" and log it (never try to bypass), cache results 30 days
     so the same place is not opened twice.
   - Store reviewer names? No — store review text and date only.
   - pytest with a saved sample HTML page (no live Google calls in tests).
   - If Google blocks often: fallback option is the official Places API free tier (10k calls/month)
     or the Apify Google Maps actor — ask Ahmad before switching.


**Ahmad checks:** `/discover` then `/research` → how many become good cards? Target ≥ 3 of 10.
**Done when:** commit "stage 7".

## Stage 7b — "Work with my screen" (LinkedIn file + paste/screenshot + current-page helper)
**Goal:** Ahmad can bring leads from LinkedIn and other logged-in sites WITHOUT automating them.

Tasks:
1. `/import-linkedin`: read Ahmad's official LinkedIn data export (`data/linkedin/Connections.csv`,
   optional `messages.csv`). Find warm leads: connections who are founders/owners/agency heads in his target
   customer type, people he already talked to, past clients. Create leads with channel `referral_ask` or
   `linkedin_message`. Never upload this file anywhere; keep it in `data/` (git-ignored).
2. Dashboard "Add from screen" box: Ahmad pastes text from a LinkedIn post/profile/Upwork job OR drops a
   screenshot. Claude reads it, creates a lead with evidence (source_type=manual, the pasted text as quote,
   today's date), then runs research on the company website (not on LinkedIn).
3. Optional "read the page I'm on" helper using Claude in Chrome: ONLY when Ahmad asks, read the text of the
   tab he has open (no navigating, no clicking, no scrolling feeds, no connection requests, no sending).
   One page per request.
4. Drafts for LinkedIn are shown with a Copy button; Ahmad pastes and sends himself.
5. guard.py stays: automatic opening of linkedin.com and any send/connect action remain blocked.

**Ahmad checks:** import his Connections.csv → list of warm leads makes sense · paste 1 LinkedIn post → good card.

---

## Stage 8 — Daily run + dashboard + schedule
**Goal:** one command does the morning job; Windows runs it; one page shows what needs Ahmad.

Tasks:
1. `.claude/commands/daily-run.md`: /sync → /discover → /research (new) → /cards → /draft (top N) →
   write `docs/digest/YYYY-MM-DD.md`. Respect limits. If `data/STOP` exists, do nothing. Never send.
2. `scripts/dashboard.py`: local page on 127.0.0.1 only — Today, Cards, Pipeline counts, Numbers
   (drafted, sent, replies, positive, meetings, won), cost/time notes.
   **Search box:** "Search an idea" → runs `claude -p "/search-idea ..."` in the background, shows "searching…",
   then shows the new cards. Keep a list of past idea searches and how many good leads each one gave.
   Show a note that each search uses part of the Claude subscription limit.
3. `scripts/schedule_windows.ps1`: Task Scheduler job running `claude -p "/daily-run"` in this folder at 07:30
   (Asia/Karachi), "run as soon as possible after a missed start". Show how to turn it on/off.
4. Logs in `logs/`.
5. `--demo` mode: fills a separate `data/demo.db` with made-up businesses for videos and screenshots.

**Ahmad checks:** run once by hand · next morning it ran by itself · `data/STOP` stops it · demo mode shows fake data only.
**Done when:** 3 mornings work. Commit "stage 8". **Version 1 is complete.**

---

## Stage 9 — Use it 2–3 weeks, then weekly report
**Goal:** learn what brings money. No new features until real data exists.

Tasks (after ~50 contacted):
1. `/weekly-report`: replies, positive replies, meetings, wins by channel, source, problem type, angle,
   follow-up number; rejected-lead reasons; lost reasons.
2. Suggest (do not apply) config changes. Only for groups with ≥ 20 contacts. Ahmad approves each one.
3. Plan (do not build) next options: Upwork MCP, partner-agency finder, Google reviews, referral asks after each win,
   public demo repo.

**Ahmad checks:** numbers match reality; every suggestion has a clear reason.
