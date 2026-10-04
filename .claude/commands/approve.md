---
description: Ahmad approves, edits or rejects the newest draft of a lead. Approved emails become Gmail drafts (label desk); other channels become a copy-paste file. Nothing is sent.
argument-hint: LEAD_ID
---
Approval for lead #$ARGUMENTS. Follow these steps exactly. **Never send anything. Never call a Gmail send,
forward or reply tool.** Only `create_draft`, `list_labels`, `create_label`, `label_thread` are used here.

1. Run `python scripts/desk.py drafts $ARGUMENTS --full`. Show Ahmad the whole draft as printed: recipient,
   subject, text, footer (email), the proof (quote + link + date + grade), the critic score, the code checks.
   If there is no draft, stop: "Run /draft $ARGUMENTS first."
   If the state is already "approved by Ahmad" (approved earlier, waiting for config), ask Ahmad only
   "Make the Gmail draft / copy-paste file now?" and on yes go to step 3.
   If the state is "in Gmail drafts", stop: it is done.

2. Ask Ahmad (AskUserQuestion): **Approve**, **Edit**, or **Reject** — and one short reason.
   Never decide for him. Never approve because he said "ok" to something else.
   - Approve → `python scripts/desk.py approve M<id> --decision approve --reason "<his reason>"`
   - Edit → get his new text (he types it, or tells you the exact changes and then confirms your full new text).
     Save it with the Write tool to `data/drafts/lead-$ARGUMENTS-edit-N.txt` (no footer, no "Subject:" line), then
     `python scripts/desk.py approve M<id> --decision edit --reason "<his reason>" --body-file data/drafts/lead-$ARGUMENTS-edit-N.txt [--subject "<new subject>"]`
   - Reject → `python scripts/desk.py approve M<id> --decision reject --reason "<his reason>"`.
     Add `--close-lead` only if Ahmad says the lead itself is bad (not only the text). Then stop.
   If the code prints REFUSED / ERROR, show the message and stop.

3. Run `python scripts/desk.py export-draft M<id>` (the id printed by approve).
   - REFUSED because `config_check --strict` fails → tell Ahmad the draft is approved and saved, but it goes to
     Gmail / a copy-paste file only after he fills the missing items (offer, postal address, proof…).
     Show the list from `python scripts/config_check.py --strict`. Then stop. Later he runs `/approve $ARGUMENTS`
     again (choose Approve) to finish.
   - Any other REFUSED → show the message and stop.

4. Email (export printed JSON with `to`, `subject`, `body`):
   a. `list_labels`. If there is no label named `desk`, `create_label` with displayName `desk`.
   b. `create_draft` with exactly that `to`, `subject` and `body` (plain text `body`, no htmlBody, no changes).
      If the JSON has `replyToMessageId` (a follow-up), pass it too: the draft goes in the same Gmail thread.
   c. `label_thread` with the returned `threadId` and the `desk` label id (also for a follow-up).
   d. `python scripts/desk.py set-gmail-draft M<id> --draft-id <draft id> --thread-id <threadId>`
   e. Tell Ahmad: "It is in Gmail → Drafts (label desk). Read it there and press Send yourself.
      /sync will see that you sent it."
   If a Gmail step fails, say which one and stop. Do not try other tools.

5. Other channels (export printed a file path): tell Ahmad to open that file in `cards/`, copy the
   text, and send it himself on LinkedIn / Upwork / email to the agency. Never open LinkedIn or Upwork for him.
   After he sends it, he tells you, and you run `python scripts/desk.py mark-sent M<id>` (this starts the
   follow-up timer).
