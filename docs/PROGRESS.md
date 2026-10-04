# Progress diary

Claude Code updates this file at the end of every stage.

| Stage | Name | Status | Date |
|---|---|---|---|
| 0 | Get ready (Ahmad) | not started | |
| 1 | Foundation, offer, safety check | done | 2026-10-04 |
| 2 | Database + CLI | done (Supabase setup by Ahmad) | 2026-10-04 |
| 3 | Researcher + Checker | built + committed; real-lead checks still to do (database is empty) | 2026-10-04 |
| 4 | Ranking + opportunity cards | built, waiting for Ahmad's checks | 2026-10-04 |
| 5 | Writer + Critic + Gmail drafts | built, waiting for Ahmad's checks | 2026-10-04 |
| 6 | Follow-ups + reply reader | built, waiting for Ahmad's checks | 2026-10-04 |
| 7 | Scout: automatic finding | built, waiting for Ahmad's checks (Places key for reviews) | 2026-10-04 |
| 8 | Daily run + dashboard + schedule | not started | |
| 9 | Use for 2–3 weeks + weekly report | not started | |

## Notes
(Claude Code: add one section per stage — what was built, how to test, known issues, next step.)

### Stage 1 — Foundation, offer, safety check (2026-10-04)
**Built**
- `scripts/config_check.py`: checks all 6 config files.
  - ERROR = cannot run safely (broken file, `sending` not "human_only", caps, word limits, patterns).
  - WARNING = "decide later" item is empty (offer, price, proof, skills, address).
  - `--strict` = warnings also fail. Stage 5 must pass this before any outreach.
- `config/offer.yaml`: `status: not_decided`, `pricing: custom`. Offer is set later in the dashboard.
- `config/problems.yaml`: 2 starter patterns (`manual-data-entry`, `missed-leads-slow-replies`).
- `tests/test_config.py`, `tests/test_guard.py` (38 pass, 1 expected "known gap").
- `requirements.txt` (pyyaml, httpx, pytest).
- `.claude/settings.json`: added deny rule `mcp__claude_ai_Gmail__reply` (Ahmad approved).

**How to test**
```
pip install -r requirements.txt
python scripts/config_check.py      # OK + 9 warnings
python -m pytest -q                 # 38 passed, 1 xfailed
echo '{"tool_name":"mcp__claude_ai_Gmail__send_message","tool_input":{}}' | python .claude/hooks/guard.py; echo "exit=$?"   # BLOCKED, exit=2
echo '{"tool_name":"mcp__claude_ai_Gmail__create_draft","tool_input":{}}' | python .claude/hooks/guard.py; echo "exit=$?"   # exit=0
```

**Known issues**
- `guard.py` does not block Gmail `reply` (word list has "reply_to", not "reply"). Covered by the
  settings.json deny rule. Ahmad may fix guard.py himself (Claude must not edit it).
- `guard.py` also does not block tools like Apollo `emailer_campaigns_approve` (starts a campaign). Do not
  connect Apollo sequences; re-check in Stage 5 (task 6).

**Next:** Ahmad commits `git commit -m "stage 1"`. Then Stage 2 (Database + CLI) when Ahmad asks.

### Stage 2 — Database + CLI (2026-10-04)
**Built**
- Database = **Supabase** (Ahmad's choice, see DECISIONS.md). `supabase/schema.sql`: 11 tables (companies, people,
  evidence, snapshots, opportunities, messages, replies, follow_ups, approvals, suppression, events),
  RLS on, `events` append-only, functions `add_lead` and `change_status` (saved in one go).
- `scripts/db.py`: the rules — status flow, duplicate check (domain / same URL / similar name), block list,
  daily cap `max_new_leads_per_day`, Asia/Karachi "today".
- `scripts/store_supabase.py` (real DB, httpx REST) and `scripts/store_sqlite.py` (tests + later demo mode).
- `scripts/desk.py`: `init, add-lead, list, show, move, history, block, blocked`. Options `--backend`, `--db`.
- Status flow: new → researched → verified → qualified → draft_ready → approved → contacted → replied → meeting →
  proposal → won. Also: rejected (before contact), approved → draft_ready, contacted → no_response,
  replied/meeting/proposal → lost, replied → proposal, opted_out from any open status. Closed leads cannot move.
- Tests: `test_db.py` (all 225 status pairs + rules), `test_store_supabase.py` (fake server), `test_desk_cli.py`.

**Setup (Ahmad, once, ~10 min)**
1. supabase.com → New project (region Mumbai / ap-south-1). Save the database password.
2. Project Settings → API keys: copy Project URL and the **secret** key. Copy `.env.example` to `.env` and fill
   `SUPABASE_URL=` and `SUPABASE_SECRET_KEY=`. Never share this key, never commit `.env`.
3. SQL Editor → paste all of `supabase/schema.sql` → Run.

**How to test**
```
python -m pytest -q                       # 346 passed, 1 xfailed
python scripts/desk.py init               # "Supabase OK, 11 tables found."
python scripts/desk.py add-lead --url https://REAL-LINK-1 --note "why" --channel email
python scripts/desk.py add-lead --url https://REAL-LINK-2 --note "why" --channel upwork --company "Name"
python scripts/desk.py add-lead --url https://REAL-LINK-3 --note "why" --channel linkedin
python scripts/desk.py add-lead --url https://REAL-LINK-1 --note "again" --channel email   # REFUSED: duplicate
python scripts/desk.py list
python scripts/desk.py move 1 won --reason test           # REFUSED: new -> won is not allowed
python scripts/desk.py move 1 researched --reason test    # OK
python scripts/desk.py history 1
python scripts/desk.py block spam-example.com --reason manual
python scripts/desk.py blocked
```
Then look in Supabase → Table Editor → `opportunities` and `events`.
No internet / no Supabase yet? Add `--backend sqlite` to any command (uses `data/desk.db`).

**Known issues**
- `schema.sql` is not run by pytest (no Postgres in tests). `desk.py init` checks it live.
- Free Supabase projects pause after ~1 week with no use. Un-pause in the dashboard.
- Daily cap is checked before saving, not inside the database (fine for one user).

**Next:** Ahmad does the setup, runs the checks, then `git commit -m "stage 2"`. Then Stage 3 when Ahmad asks.

### Stage 3 — Researcher + Checker (2026-10-04)
**Built**
- `scripts/snapshot.py`: downloads a page, saves its text in `data/snapshots/<sha256>.txt` + a row in `snapshots`.
  `--from-file FILE --url URL` for pages that block bots (Ahmad pastes the text). Never fetches LinkedIn.
- `scripts/quote_check.py`: is the quote really in the saved page? FOUND / FOUND_FUZZY (tiny typo) / NOT_FOUND.
  A paraphrase is NOT_FOUND. Also freshness (decay days from problems.yaml) and grade lowering.
- New `desk.py` commands: `start-research`, `add-evidence`, `set-contact`, `set-research`, `verify-evidence`.
  `show` now shows pattern, why now, unknowns, topic and checked marks.
- **The code checks the proof, not only the AI:**
  - quote not in the saved page → grade UNKNOWN; old proof → one grade lower; job post with no date → one lower;
  - the checker can lower a grade, never raise it;
  - guessed emails refused (only `published` / `verified` + evidence id); blocked emails/domains refused;
  - `move ID verified` is refused without checked `pain` proof (1 STRONG, or 2 WEAK from different source
    types) and a contact person with a title;
  - daily cap `max_research_per_day` (15) and max 2 research rounds per lead.
- New column `evidence.topic` (pain, company, why_now, owner, contact, impact). Applied to Supabase with the
  MCP (migration `stage3_evidence_topic_and_search_path`), plus the `events_append_only` search_path fix.
  Supabase security warning is gone (only the expected INFO "RLS no policy" is left).
- Agents `researcher`, `checker` and skills `research-company`, `check-evidence` updated to use the commands.
- New slash command `/research ID` (`.claude/commands/research.md`).
- Tests: `test_quote_check.py`, `test_snapshot.py`, `test_research.py`, more in `test_store_supabase.py`.

**How to test**
```
python -m pytest -q                         # 414 passed, 1 xfailed
python scripts/desk.py init                 # "Supabase OK, 11 tables found."
python scripts/desk.py add-lead --url https://REAL-JOB-POST --note "why" --channel email
```
In Claude Code: `/research 1`. Then `python scripts/desk.py show 1`.
- Open 5 proof links yourself. Is each quote really on the page?
- Do it for 3 real leads.
- Fake test: add a lead, then save an invented fact:
  `python scripts/snapshot.py https://THEIR-SITE` (copy the sha), then
  `python scripts/desk.py add-evidence ID --claim "They lose $10k a month" --url https://THEIR-SITE --quote "we lose ten thousand dollars every month" --source-type website --topic pain --grade STRONG_SIGNAL --snapshot SHA`
  → it must say "quote NOT FOUND ... UNKNOWN", and the lead can never move to `verified`.
- Page blocked (Upwork often is)? `/research` asks you to paste the page text into `data/paste/lead-ID-1.txt`.

**Known issues**
- Pages that need JavaScript give little text. Use the paste way (`--from-file`).
- Snapshot text files live only on this PC (`data/snapshots/`). The database keeps only the sha and URL.
- Evidence + its event are two saves (not one transaction). Fine for one user.

**Next:** Ahmad runs the checks above, then `git commit -m "stage 3"`. Then Stage 4 when Ahmad asks.

### Stage 4 — Ranking + opportunity cards (2026-10-04)
**Built**
- `scripts/rank.py`: hard gates + priority. Pass → `qualified`, fail → `rejected` + reasons.
  - Gates: checked problem proof that is **fresh today** · fit ≥ 2 · value ≥ `min_value_band` (2) · owner role known ·
    offer proof (only a **warning** while `offer.yaml` is `not_decided`) · not blocked (domain + contact email) ·
    no disqualifier · no personal email (gmail...) on the `email` channel.
  - No fit/value score yet → stays `verified` ("waiting"). Qualified leads are checked again every run:
    old proof → `rejected`.
  - Priority = evidence (3 CONFIRMED / 2 STRONG / 1 two WEAK) × fit × urgency (2 strong why-now proof /
    1 weak or text only / 0 none) × value. Ties: newest proof first. Each factor + reason + one "why" line saved.
  - `--dry-run`, `--id N`, `--need-score`.
- `desk.py set-score ID --fit --value --fit-why --value-why [--disqualifier]`: the judgment part (Claude in /cards).
  `show` prints fit, value, priority, why, warnings. `list` has a PRIORITY column.
- `scripts/cards.py`: `cards/<id>.md` + `cards/index.html` (best first). Page text is escaped; only http/https
  links. LinkedIn profiles show "(look up by hand)". `cards/` is git-ignored.
- `/cards` command (`.claude/commands/cards.md`) with the fit/value rubric.
- New column `opportunities.rank_info` (jsonb). Applied to Supabase (migration `stage4_rank_info`); `schema.sql` updated.
- Tests: `test_rank.py`, `test_cards.py`, more in `test_store_supabase.py`.

**How to test**
```
python -m pytest -q                  # 462 passed, 1 xfailed
python scripts/desk.py init          # "Supabase OK, 11 tables found."
```
The database is empty now. So first make some verified leads (Stage 3):
1. `python scripts/desk.py add-lead --url https://REAL-LINK --note "why" --channel email` (about 10 real leads).
2. In Claude Code: `/research ID` for each.
3. In Claude Code: `/cards`.
4. Open `cards/index.html` in your browser. For each card ask: "Would I really contact them?"
   Goal: 6 of 10 say yes.
5. Wrong score? `python scripts/desk.py set-score ID --fit 2 --value 3 --fit-why "..." --value-why "..."`,
   then `python scripts/rank.py` and `python scripts/cards.py`.

**Known issues**
- Rejected is final (closed). A lead rejected by mistake must be added again with new proof.
- Old card `.md` files are not deleted; `index.html` shows only leads that are qualified now.
- Fit and value are Claude's judgment. The rubric is in `/cards`; you can change any score by hand.

**Next:** Ahmad does the checks above, then `git commit -m "stage 4"`. Then Stage 5 when Ahmad asks.

### Stage 5 — Writer + Critic + approvals + Gmail drafts (2026-10-04)
**Built**
- `scripts/checks.py`: code checks for every draft. ERROR = cannot go out, WARNING = look at it.
  Word limit per channel · email subject 1-4 words · banned phrases · placeholders ({name}, [Company], TODO) ·
  recipient email published/verified, not personal (gmail...) · block list (email + domain) · evidence ids
  (at least one checked `pain` proof, never UNKNOWN, INFERENCE = warning) · no link in a first LinkedIn message ·
  email footer (name, postal address, opt-out line). `python scripts/checks.py M12 [--final]`.
- **The email footer is added by code** at export, from `policy.yaml` + `me.yaml`. The writer never types it.
- New `desk.py` commands: `save-draft`, `save-review`, `drafts ID [--full]`, `approve`, `export-draft`,
  `set-gmail-draft`. `show` prints the newest draft and its state.
- **The code enforces the rules**, not only the agents:
  - critic APPROVE only if every score ≥ 1 and total ≥ 11/14 **and** code checks are clean (else → REWRITE);
  - max 3 drafts per lead per day (first + 2 rewrites); cap `max_drafts_per_day` (25);
  - Ahmad's approval saves a sha256 of subject + text. Text changed later → approval invalid, lead back to
    `draft_ready`;
  - `export-draft` = last gate: lead approved, valid approval, final checks, not blocked,
    `config_check --strict` passes, cap `max_first_emails_per_day` (20), not already in Gmail.
- Status flow: qualified → (critic pass) draft_ready → (Ahmad) approved. Reject = lead stays open
  (`--close-lead` closes it). Lead stays `approved` after the Gmail draft; `contacted` comes when Ahmad
  really sends (Stage 6 /sync).
- Gmail: the claude.ai Gmail connector (already connected). Python checks and prints `{to, subject, body}`;
  `/approve` calls `create_draft`, puts label `desk` on the thread, saves the draft id. Other channels →
  `cards/<lead id>-message.txt` for copy-paste.
- Guard (task 6): test of every Gmail connector tool. Only `send_message` and `forward` send; guard.py blocks
  both. `reply` is denied in settings.json. Everything else (drafts, labels, read) is allowed.
- Agents `writer`, `critic`, skills `write-outreach`, `review-outreach` updated (examples for all 5 channels).
  Writer can now also use Write, only for `data/drafts/`.
- New slash commands `/draft ID` and `/approve ID`.
- No database change: `messages` and `approvals` tables already existed.
- Tests: `test_checks.py`, `test_drafts.py`, Gmail list in `test_guard.py`, more in `test_store_supabase.py`.

**How to test**
```
python -m pytest -q                         # 550 passed, 2 xfailed
python scripts/desk.py init                 # "Supabase OK, 11 tables found."
python scripts/config_check.py --strict     # FAILS now (offer, address...) - this is expected
```
The database is empty. So first make qualified leads (Stages 3-4): `add-lead` → `/research ID` → `/cards`.
Then, in Claude Code:
1. `/draft ID` for 5 qualified leads. Read each draft: short? true (every fact in the proof)? specific?
2. `/approve ID` → choose Approve. You will see: "config_check --strict fails". This is correct: nothing goes to
   Gmail until your offer, postal address and proof are filled.
3. Fill `config/me.yaml` (business_name, postal_address, skills, portfolio_links) and `config/offer.yaml`
   (name, problem, customer, result, a proof link). Run `python scripts/config_check.py --strict` → OK.
4. `/approve ID` again → the email appears in Gmail → Drafts with label `desk`. Read it there. Send 1 yourself.
5. Safety test: ask Claude "send this email now" → it must be BLOCKED by guard.py.
6. Change test: after approval, edit the text in Supabase (table `messages`, column `body`) and run
   `python scripts/desk.py export-draft M<id>` → REFUSED "changed after Ahmad approved it".

**Before volume:** use a second domain for cold email + SPF/DKIM/DMARC. Start with 5 emails a day, max 20.

**Known issues**
- guard.py allows any tool name that contains "draft". A future Gmail tool called `send_draft` would pass
  (not exposed today; xfail test shows it). Fix = a deny rule `mcp__claude_ai_Gmail__send_draft` in
  settings.json — only with Ahmad's OK.
- guard.py also misses Apollo campaign tools (`apollo_emailer_campaigns_approve`, `..._add_contact_ids`) that can
  start real sending. Do not use Apollo sequences. A deny rule can be added — only with Ahmad's OK.
- Proof freshness is checked by `rank.py`, not again at export. A draft approved long ago with old proof can
  still be exported. Stage 6/8 can add this.
- If a draft is deleted by hand in Gmail, the desk still thinks it is there (Stage 6 /sync will see it).

**Next:** Ahmad does the checks above, then `git commit -m "stage 5"`. Then Stage 6 when Ahmad asks.

### Stage 6 — Follow-ups + reply reader (2026-10-04)
**Built**
- `scripts/replies.py`: fixed rules. Cuts the quoted old email first (our footer is in it), then:
  bounce (mailer-daemon, "address not found", 550 5.1.1) > opt-out ("remove me", "unsubscribe", "stop emailing",
  "don't contact", or just "no" / "stop") > out-of-office. These rules **win over the AI**.
- `scripts/followups.py`: business days (Mon-Fri). Follow-up 2-5 due 3 / 7 / 14 / 24 business days after the first
  message (min 2 business days after the last one). Max 5 touches. After the last touch + 10 business days with no
  reply → `no_response`. `python scripts/followups.py [--dry-run]` updates all timers (safe to run many times).
- `scripts/today.py`: what needs Ahmad today (replies, bounces, drafts to approve, emails to send, follow-ups due,
  "not now" reminders, stale leads, leads the desk closed).
- New `desk.py` commands: `sync-list`, `mark-sent`, `draft-missing`, `log-reply`, `classify-reply`, `replies`,
  `reply-done`, `followups [--due]`. `show` prints sent touches, replies and the next follow-up.
- What each reply does (code, not AI):
  | reply | effect |
  |---|---|
  | opt_out | email(s) blocked **at once**, lead → `opted_out`, follow-ups cancelled |
  | bounce | email blocked, contact marked invalid, follow-ups stop; /today: find new contact or close |
  | out_of_office | follow-up moves after their return day (+1 business day; no date → +5) |
  | not_now | lead → `replied`, reminder at their date (or +60 days) |
  | not_interested | lead → `replied` → `lost` |
  | positive / question / objection / referral / other | lead → `replied`, follow-ups stop, /today: "your move" |
- Follow-ups reuse writer + critic + /approve. Lead stays `contacted`. Code checks: no "just following up" /
  "bumping this"; not a copy of an earlier message; email follow-up has no subject and goes in the **same Gmail
  thread** (`create_draft` with `replyToMessageId`). The daily first-email cap does not count follow-ups.
- After a bounce, `set-contact` works on the `contacted` lead; the next follow-up is due at once, in a new thread.
- New slash commands `/sync` (Gmail read only: get_thread, list_drafts, search_threads) and `/today`.
  `/draft` and `/approve` handle follow-ups. Agent `reply-reader` + skill `read-replies` rewritten; writer, critic
  and their skills have follow-up rules.
- Database: `replies` got `gmail_message_id` (unique), `sender`, `subject`, `next_action`, `note`, `handled_at`.
  Applied to Supabase (migration `stage6_replies`); `schema.sql` updated. Security check: only the expected INFO.
- `config/cadence.yaml`: new timer settings (all checked by `config_check.py`).
- Tests: `test_replies.py` (18 sample replies in `tests/fixtures/replies/`), `test_followups.py`, `test_sync.py`,
  more in `test_store_supabase.py`, `test_config.py`, `test_guard.py`.

**How to test**
```
python -m pytest -q                       # 648 passed, 2 xfailed
python scripts/desk.py init               # "Supabase OK, 11 tables found."
python scripts/today.py                   # "Nothing needs you today." (database is empty)
python scripts/replies.py tests/fixtures/replies/03_just_no.txt    # rule result: opt_out
```
Full loop by hand (needs one approved email; see Stage 5 — the offer + postal address must be filled first):
1. `/approve ID` → the email is in Gmail Drafts. Change the "To" to **your own second address** if you test.
   Better: make a test lead whose published email is your second address.
2. Press Send in Gmail. In Claude Code: `/sync` → "M.. marked as sent", lead `contacted`, follow-up date shown.
3. From the second address reply **"please remove me"**. `/sync` → address on the block list
   (`python scripts/desk.py blocked`), lead `opted_out`.
4. `/today` → correct list.
5. LinkedIn test: `python scripts/desk.py mark-sent M<id>` after you send by hand, then
   `python scripts/desk.py log-reply ID --text "Sounds good, tell me more"` → the reply-reader sorts it
   (ask Claude: "sort reply R<id> of lead ID").
6. Samples: `python -m pytest -q tests/test_replies.py` → all 18 samples sorted as expected.

**Known issues**
- Holidays are not skipped (only weekends).
- If Ahmad changes the text in the Gmail draft before sending, the desk saves the approved text, not the sent one.
- New facts for a follow-up: evidence can be added only before contact. Follow-ups use ideas / examples / other
  angles on the checked proof.
- After a "not now" reminder message is sent, the lead stays `replied`; no automatic follow-ups after it.
- Stage 5 known issues (guard gaps for `send_draft` and Apollo) are still open.

**Next:** Ahmad commits Stage 5 (`git commit -m "stage 5"`) if not done, runs the checks above, then
`git commit -m "stage 6"`. Then Stage 7 when Ahmad asks.

### Stage 7 — Scout: automatic finding (2026-10-04)
**Built**
- New table `raw_items` = posts and places the sources found, before the Scout decides (status new / kept /
  rejected / expired). New column `opportunities.idea`. Applied to Supabase (migration `stage7_raw_items`);
  `schema.sql` + SQLite store updated. `desk.py init` -> "12 tables".
- Sources (`scripts/sources/`). Each saves found items; the same URL is never saved twice; LinkedIn is never opened:
  - `hn.py` — Hacker News (free Algolia API): newest "Who is hiring" (job posts), "Seeking freelancer" (only
    SEEKING FREELANCER posts) and "Ask HN" of the last 30 days, matched with pattern keywords.
  - `jobs.py` — career pages in `sources.yaml job_pages`: opens matching job links (max 10 per page), reads the
    "Posted ..." date.
  - `agencies.py` — agency sites you collect by hand (`agency_candidates` or `--file data/agencies.txt`):
    homepage + careers page, hiring words, developer roles. Clutch / Shopify / LinkedIn pages are refused.
  - `pagespeed.py LEAD_ID` — Google PageSpeed (mobile). Score < 50 -> evidence website / pain / WEAK_SIGNAL.
  - `gmaps.py "dental clinic in Houston TX"` — Playwright, headless, no login: name, category, address,
    website, phone, rating + read-only website check (contact page, form, booking link). Limits in code:
    3 searches + 60 businesses a day, 3-8 s waits, captcha -> stop at once (exit 3, logged, no more Maps today),
    a place found before is never opened again. Reviewer names are never stored.
  - `places.py` — **Google shows a "limited view" (no reviews) to browsers that are not logged in.** We never
    log in or bypass it. So reviews come from the official **Places API (New)** (Ahmad's choice): up to 5 reviews
    per place, lowest first, exact dates, names dropped. Limit `places_api.max_calls_per_day: 30` (2 calls per place).
- `scripts/scout.py`: `pending`, `keep`, `reject`, `add-raw`, `expire`, `stats`. The code enforces: signal decay
  days, daily new-lead cap, max 10 leads per idea per day, duplicates, block list. `keep` saves the post text as a
  snapshot. Maps: each review that shows the problem (`review_keywords` in problems.yaml) -> evidence review /
  pain / WEAK; 2+ such reviews -> the first one is STRONG.
- `scripts/ideas.py`: `/search-idea` patterns in `config/ideas/<name>.yaml` (id `idea-<name>`). Research, ranking
  and cards work with them. `promote NAME` copies one into problems.yaml (only when Ahmad says yes).
- Commands `/discover` and `/search-idea "IDEA"`. Agent `scout` + skill `find-signals` rewritten.
- Config: `review_keywords` in problems.yaml; `sources.yaml` (max_age_days, gmaps searches, pagespeed bad_score);
  `policy.yaml` (`max_leads_per_idea_search: 10`, `places_api`). `config_check.py` checks all of it.
- Playwright + Chromium installed (`requirements.txt`).
- Tests: `test_scout.py`, `test_sources.py`, `test_gmaps.py` (sample pages in `tests/fixtures/sources/`, made-up
  businesses, no live Google), more in `test_store_supabase.py`, `test_config.py`.

**Setup (Ahmad, once, ~10 min): Google Places key for reviews**
1. console.cloud.google.com -> new project -> APIs & Services -> Library -> enable **Places API (New)**.
   Google asks for a billing account (card). Normal use here stays inside the free monthly usage.
2. APIs & Services -> Credentials -> Create API key -> Restrict key -> only "Places API (New)".
3. Safety: Places API (New) -> Quotas -> set requests per day to 60.
4. Put it in `.env`: `GOOGLE_PLACES_API_KEY=...` (never share, never commit).
Without the key Maps still works, but with no reviews (it prints a hint).

**How to test**
```
python -m pytest -q                       # 714 passed, 2 xfailed
python scripts/desk.py init               # "Supabase OK, 12 tables found."
python scripts/sources/hn.py --dry-run    # live: matching HN posts, nothing saved
python scripts/sources/gmaps.py "dental clinic in Houston TX" --max 5     # after the Places key
python scripts/scout.py pending --source gmaps    # reviews + website check, no reviewer names
```
In Claude Code:
1. Add 2-3 Maps searches to `config/sources.yaml` (`gmaps: searches:`) and career pages to `job_pages`.
2. `/discover` -> then `/research ID` on 10 kept leads -> `/cards`. Goal: 3 of 10 become good cards.
3. `/search-idea "dental clinics that need booking automation"` -> keywords shown, max 10 leads tagged with the idea.
4. Run `gmaps.py` 4 times in one day -> the 4th is REFUSED (daily limit).

**Known issues**
- The Maps page layout can change; selectors may need a fix. Live test 2026-10-04: business info + website check OK,
  reviews hidden (limited view) -> Places API.
- Places API gives max 5 reviews per place, chosen by Google (not always the worst ones).
- `role_reposted` is not detected automatically (the Scout can only use it by reading the text).
- Job posts without a date stay "unknown date"; the researcher lowers their grade (Stage 3 rule).
- The live test saved 2 real clinic websites + example.com as snapshots in `data/snapshots/` (local only,
  not in Supabase). Kept (we never delete in `data/`).
- The Scout agent runs on Haiku (cheap). If its choices are weak, it can move to Sonnet.
- Stage 5 known issues (guard gaps for `send_draft` and Apollo) are still open.

**Next:** Ahmad sets up the Places key, runs the checks above, then `git commit -m "stage 7"`.
Then Stage 7b or 8 when Ahmad asks.
