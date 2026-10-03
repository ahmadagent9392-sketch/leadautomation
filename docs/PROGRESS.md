# Progress diary

Claude Code updates this file at the end of every stage.

| Stage | Name | Status | Date |
|---|---|---|---|
| 0 | Get ready (Ahmad) | not started | |
| 1 | Foundation, offer, safety check | done | 2026-10-04 |
| 2 | Database + CLI | not started | |
| 3 | Researcher + Checker | not started | |
| 4 | Ranking + opportunity cards | not started | |
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

