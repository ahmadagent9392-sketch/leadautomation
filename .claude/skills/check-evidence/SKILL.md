---
name: check-evidence
description: How the Opportunity Desk checker verifies evidence - quote-in-snapshot checks, freshness, claim support, grade fixes, disqualifiers and the PASS / NEED_MORE / FAIL decision.
---
# Checking evidence

For each evidence item:
1. Quote in snapshot? (`scripts/quote_check.py`). No → grade UNKNOWN.
2. Date fresh? Past decay_days → lower grade by one level.
3. Does the quote really support the claim? Overstated claim → lower grade and rewrite claim.
4. Source quality: company's own pages > job boards > review sites > blogs/SEO sites.
5. INFERENCE must list the evidence it depends on; otherwise UNKNOWN.

Then check disqualifiers (config/problems.yaml) and the block list.

Decision:
- PASS: pain ≥ STRONG (or 2 WEAK from different source types), fresh, owner role known, no disqualifier.
- NEED_MORE: max 3 exact questions. Only one extra round is allowed.
- FAIL: list reasons. Common: stale post, already filled, quote not found, giant company, agency reposting.

Be skeptical. Your job is to protect Ahmad from embarrassing or wrong messages.
