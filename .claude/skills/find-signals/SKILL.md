---
name: find-signals
description: How to judge raw posts and pages as buying signals for Opportunity Desk - which signals are strong, weak or useless, and how to reject noise. Use when scouting or adding new leads.
---
# Finding signals

## Signal strength
| Signal | Tier | `--signal` | Notes |
|---|---|---|---|
| Someone asks for help / posts a job with budget (Upwork, HN "Seeking freelancer", r/forhire) | 1 | help_request | Best. Fresh only (14 days). |
| Job post for work you can automate (data entry, VA, admin, receptionist, "Zapier expert") | 1 | job_post | Strongest if it names the apps used. |
| Same role re-posted / open > 60 days | 1 | role_reposted | Pain continues. |
| Agency hiring a developer / saying it is overloaded | 1 | job_post | Partner opportunity (channel: agency_pitch). |
| Google reviews showing slow replies / missed calls / lost bookings | 2 | bad_review | 2+ separate problem reviews = strong. 1 review = weak. |
| Slow or broken website / form, no booking option | 2 | website_issue | Needs a second signal. |
| Funding, expansion, new manager | 2 | – | Only a "why now" bonus. Never a reason to keep. |
| "Industry is growing", company size only | – | – | Reject. |

## Rules
- A lead needs 1 Tier-1 signal, or 2 Tier-2 signals (for Maps: 2+ problem reviews, or 1 problem review + a
  website problem from `website_check`: no form, no booking link).
- Too old (past decay_days in config/problems.yaml) → the code refuses. Do not argue with it.
- One lead per company. The code checks duplicates; a REFUSED duplicate is final.
- Pick the channel: help request → upwork_proposal (Upwork) or email; job post → email; agency → agency_pitch;
  local business from Maps → email (if it has a website) else reject (no way to reach them in writing).

## By source
- **hn / Who is hiring**: a company hiring for the pattern's work (receptionist, data entry, ops assistant).
  Reject pure engineering roles, big companies (> 1000 staff), recruiters posting for clients.
  `--company` = the name before the first "|".
- **hn / Seeking freelancer, Ask HN**: a person with a concrete operations problem. Reject tech debates,
  job seekers ("SEEKING WORK"), and "how do I learn X" posts.
- **jobs**: the company's own career page. Keep job posts for automatable work. Reject on-site physical roles.
  A job post without a date (`posted_at` empty) is weaker: keep only if the text is very specific.
- **gmaps**: look at `problem_reviews` (stars, date, text) and `website_check`. Keep only if the reviews show the
  pattern's problem (calls not answered, no reply, could not book, order mistakes) — not rude staff, prices or
  medical quality. Reviewer names are never stored; never ask for them.
- **agency**: keep if `hiring_words` + `dev_roles` show they need developers / automation help, or they say they
  are overloaded. Channel agency_pitch. Pattern = the one closest to the work they need.
- **web / manual**: judge the text like the sources above. The idea's keywords must really appear.

## Upwork (pasted by hand)
Prefer payment verified, real money spent, hire rate > 50%. Skip "cheap", "quick", "test task".
