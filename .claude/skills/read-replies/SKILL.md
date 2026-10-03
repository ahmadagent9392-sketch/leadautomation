---
name: read-replies
description: How the Opportunity Desk reply reader classifies replies, detects opt-outs and objections, and proposes next actions.
---
# Reading replies

| Category | Signs | Next action |
|---|---|---|
| positive | "sounds good", "send the video", "let's talk" | draft_reply (Ahmad approves) |
| question | asks price, how, timeline | draft_reply; price → ask_ahmad |
| objection | "too expensive", "we use X", "no time" | draft_reply addressing it, or stop |
| referral | "talk to my colleague X" | create new lead for X, thank-you draft |
| not_now | "maybe in March", "next quarter" | schedule_followup at that date (or +60 days) |
| not_interested | "no thanks" | stop_sequence |
| opt_out | "remove me", "unsubscribe", "stop emailing", "don't contact" | block immediately, stop |
| out_of_office | auto-reply | move follow-up after return date |
| bounce | delivery failure | block address, mark contact invalid |

Angry, legal, contract or pricing negotiation → ask_ahmad. Never send.
