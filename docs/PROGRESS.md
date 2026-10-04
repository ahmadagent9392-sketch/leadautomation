# Progress diary

Claude Code updates this file at the end of every stage.

| Stage | Name | Status | Date |
|---|---|---|---|
| 0 | Get ready (Ahmad) | not started | |
| 1 | Foundation, offer, safety check | done | 2026-10-04 |
| 2 | Database + CLI | done (Supabase setup by Ahmad) | 2026-10-04 |
| 3 | Researcher + Checker | built + committed; real-lead checks still to do (database is empty) | 2026-10-04 |
| 4 | Ranking + opportunity cards | built, waiting for Ahmad's checks | 2026-10-04 |
| 5 | Writer + Critic + Gmail drafts | not started | |
| 6 | Follow-ups + reply reader | not started | |
| 7 | Scout: automatic finding | not started | |
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
