---
name: find-signals
description: How to judge raw posts and pages as buying signals for Opportunity Desk - which signals are strong, weak or useless, and how to reject noise. Use when scouting or adding new leads.
---
# Finding signals

## Signal strength
| Signal | Tier | Notes |
|---|---|---|
| Someone asks for help / posts a job with budget (Upwork, HN, r/forhire) | 1 | Best. Check it is still open and fresh. |
| Job post for work you can automate (data entry, VA, admin, "Zapier expert") | 1 | Strongest if it names the apps used. |
| Same role re-posted / open > 60 days | 1 | Pain continues. |
| Agency hiring a developer / saying it is overloaded | 1 | Partner opportunity (channel: agency_pitch). |
| Reviews showing slow replies / lost bookings | 2 | Needs 2+ separate reviews. |
| Slow or broken website / form | 2 | Needs a second signal. |
| Funding, expansion, new manager | 2 | Only a "why now" bonus. Never enough alone. |
| "Industry is growing", company size only | – | Reject. |

## Rules
- A lead needs 1 Tier-1 signal, or 2 Tier-2 signals from different sources.
- Too old (past decay_days in config/problems.yaml) → reject.
- Upwork: prefer payment verified, real money spent, hire rate > 50%. Skip "cheap", "quick", "test task".
- One lead per company. Check duplicates first (`desk.py list`).
- Pick the channel: help request → upwork_proposal or email; job post → email; agency → agency_pitch.
