---
description: What needs Ahmad today - replies to answer, drafts to approve, emails to send, follow-ups due, bounces, stale leads.
---
Show Ahmad what needs him today. Nothing is sent. Follow these steps:

1. Run `python scripts/followups.py` (updates the follow-up timers first).
2. Run `python scripts/today.py`.
3. Show the result to Ahmad in short, simple sentences, most important first:
   replies that need him → bounced emails → drafts to approve → emails to send → follow-ups due → reminders →
   stale leads → leads the desk closed by itself.
   For each item say the one next step (for example "/draft 4", "/approve 7", "read it in Gmail and press Send").
4. If a section says "not sorted yet", tell him to run /sync first.
5. If nothing is listed: "Nothing needs you today."

Do not start /draft, /approve or /sync by yourself. Ahmad chooses.
