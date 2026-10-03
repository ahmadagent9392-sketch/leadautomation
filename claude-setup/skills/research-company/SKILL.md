---
name: research-company
description: How to research one Opportunity Desk lead with proof - sources to prefer, how to save evidence with quote/date/grade, how to find the problem owner, and when to stop.
---
# Researching a company

## Order of work
1. Open the source link (job post / request). Save snapshot. Save the main pain quote.
2. Company website: what they do, size hints, team/about page, contact page.
3. Why now: date of post, recent news on their own site/blog, new location, hiring.
4. Owner: the person in the post, the founder/owner on the About page, or the manager title matching
   `owner_roles` in config/problems.yaml. If only LinkedIn would show it → write task "look up by hand".
5. Contact: published email on their site > contact form > Hunter (only if Ahmad set it up, max 50/month).
   Never invent an email.

## Evidence format
`python scripts/desk.py add-evidence ID --claim "..." --url URL --quote "exact text" --date YYYY-MM-DD --source-type job_post|help_request|review|website|news|profile|manual --grade GRADE [--depends-on E1,E2]`

## Grades
- CONFIRMED_FACT: exact quote from the company's own source, claim restates it.
- STRONG_SIGNAL: exact quote, claim follows in one obvious step.
- WEAK_SIGNAL: one third-party source (one review) or indirect.
- INFERENCE: no quote; reasoning from listed evidence ids. Never stated as fact later.
- UNKNOWN: not established.

## Stop when
Pain proof + owner + contact path are found, or after ~15 tool calls. Write UNKNOWNs clearly.
