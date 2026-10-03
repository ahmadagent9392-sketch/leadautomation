# Architecture (simple version)

```
                YOU  (30 min/day: approve, edit, send, talk to clients)
                 ▲            │ approvals
     dashboard / digest       ▼
┌───────────────────────────────────────────────────────────┐
│ CLAUDE CODE (on your subscription) — the boss              │
│ /daily-run · /discover · /research · /cards · /draft ·      │
│ /sync · /today · /weekly-report                             │
│ Safety: CLAUDE.md rules + settings.json + hooks/guard.py    │
└──────┬───────────┬───────────┬───────────┬─────────┬───────┘
       ▼           ▼           ▼           ▼         ▼
    Scout     Researcher    Checker    Writer →   Reply
   (haiku)    (sonnet)      (opus)     Critic     reader
                                    (sonnet/opus) (haiku)
       │  each helper follows a skill in .claude/skills/
       ▼
  TOOLS: web search/fetch (built in) · Gmail (drafts + read) ·
         Hunter (50 free/month) · Playwright (optional) · Upwork (later)
       │
       ▼
  SOURCES: partner agencies · help requests with budget (Upwork, HN, r/forhire) ·
           job posts · your network/referrals · websites with visible problems
       │
       ▼
  MEMORY: data/desk.db (SQLite) — companies, people, evidence (url+quote+date),
          opportunities + status, messages, replies, follow-ups, block list, events
```

## Daily flow
Find → Research → Prove (Checker) → Rank (rules) → Write + Critic → **You approve & send** →
Follow up (day 3, 7, 14, 24) → Read replies → Learn (weekly report)

## Channels (not only email)
The Writer drafts for: cold email (Gmail draft), LinkedIn message (you paste & send),
Upwork proposal (you submit), agency partnership pitch, referral ask.

## Statuses
new → researched → verified → qualified → draft_ready → approved → contacted →
replied → meeting → proposal → won | lost | no_response | rejected | opted_out
