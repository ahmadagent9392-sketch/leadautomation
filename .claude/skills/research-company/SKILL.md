---
name: research-company
description: How to research one Opportunity Desk lead with proof - sources to prefer, how to save evidence with quote/date/grade, how to find the problem owner, and when to stop.
---
# Researching a company

## Order of work
1. Open the source link (job post / request). Save snapshot. Save the main pain quote (`--topic pain`).
2. Company website: what they do, size hints, team/about page, contact page (`--topic company` / `contact`).
3. Why now: date of post, recent news on their own site/blog, new location, hiring (`--topic why_now`).
4. Owner: the person in the post, the founder/owner on the About page, or the manager title matching
   `owner_roles` in config/problems.yaml (`--topic owner`). If only LinkedIn would show it → write the
   unknown "look up owner on LinkedIn by hand" with `set-research --unknowns`.
5. Contact: published email on their site > contact form > Hunter (only if Ahmad set it up, max 50/month).
   Never invent an email.
6. Pick the pattern (`config/problems.yaml`) and the channel.

## Commands
```
python scripts/snapshot.py URL                       # prints SHA; text is in data/snapshots/SHA.txt
python scripts/desk.py add-evidence ID --claim "..." --url URL --quote "exact text from SHA.txt" \
    --date YYYY-MM-DD --source-type job_post|help_request|review|website|news|profile|manual \
    --topic pain|company|why_now|owner|contact|impact --grade GRADE --snapshot SHA [--depends-on E1,E2]
python scripts/desk.py set-contact ID --title "Office Manager" [--name "..."] [--role-type owner] \
    [--email x@company.com --email-status published --evidence E3] [--profile-url URL]
python scripts/desk.py set-research ID --pattern PATTERN_ID --why-now "..." --unknowns "a; b" \
    [--channel email] [--company-name "..."] [--domain company.com] [--industry ...] [--size 11-50] [--country ...]
```
- `--date` = the date shown on the source (post date, review date). No date on the page → leave it out.
  Job posts, help requests, reviews and news without a date are lowered one grade by the code.
- Upwork / HN / Maps leads have no company domain yet. When you find the website, save it with `--domain`.

## The code checks you
- CONFIRMED_FACT / STRONG_SIGNAL / WEAK_SIGNAL need `--snapshot` and a quote of 15–300 chars that is really
  in the saved text. If not, the grade is saved as UNKNOWN. A paraphrase is NOT a quote.
- The snapshot must be of the same URL as `--url`.
- INFERENCE needs `--depends-on` (evidence ids). Its quote may be empty.
- Emails need `--email-status published|verified` and `--evidence`. Blocked emails/domains are refused.

## Grades
- CONFIRMED_FACT: exact quote from the company's own source, claim restates it.
- STRONG_SIGNAL: exact quote, claim follows in one obvious step.
- WEAK_SIGNAL: one third-party source (one review) or indirect.
- INFERENCE: no quote; reasoning from listed evidence ids. Never stated as fact later.
- UNKNOWN: not established.

## Blocked pages
If `snapshot.py` says the site blocked it (or shows a bot check), stop trying that page. Put it under
`NEEDS_PASTE` in your report. Ahmad will paste the page text and run `snapshot.py --from-file`.

## Stop when
Pain proof + owner + contact path are found, or after ~15 tool calls. Write UNKNOWNs clearly.
