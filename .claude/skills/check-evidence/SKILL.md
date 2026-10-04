---
name: check-evidence
description: How the Opportunity Desk checker verifies evidence - quote-in-snapshot checks, freshness, claim support, grade fixes, disqualifiers and the PASS / NEED_MORE / FAIL decision.
---
# Checking evidence

For each evidence item (check base facts first, INFERENCE items last):
1. Quote in snapshot? `python scripts/quote_check.py --evidence EID`. NOT_FOUND → grade UNKNOWN.
   FOUND_FUZZY = tiny difference; fine, but say so in the note.
2. Snapshot older than 7 days, or a job post that may be closed → `python scripts/snapshot.py URL` again and
   use `verify-evidence --snapshot NEW_SHA`. If the quote is gone now, the code makes it UNKNOWN.
3. Date fresh? Past decay_days (config/problems.yaml) → the code lowers the grade by one level.
4. Does the quote really support the claim? Overstated claim → lower grade and rewrite the claim (`--claim`).
5. Source quality: company's own pages > job boards > review sites > blogs/SEO sites.
6. INFERENCE must stand on checked evidence; otherwise the code makes it UNKNOWN.
7. Topic right? Only real proof of the problem should be `pain`. A company fact marked `pain` → grade it
   UNKNOWN with the note "not problem proof".

Save: `python scripts/desk.py verify-evidence EID --grade G --note "why" [--claim "..."] [--snapshot SHA]`.
You can lower a grade, never raise it.

Then check disqualifiers (config/problems.yaml, the lead's pattern) and the block list (`desk.py blocked`).

Decision:
- PASS: checked `pain` proof ≥ STRONG (or 2 WEAK from different source types), fresh, owner role known,
  no disqualifier.
- NEED_MORE: max 3 exact questions. Only one extra round is allowed.
- FAIL: list reasons. Common: stale post, already filled, quote not found, giant company, agency reposting,
  invented claim.

Be skeptical. Your job is to protect Ahmad from embarrassing or wrong messages.
