# Sources and evidence

## Every rule has a source

- Shape: `the rule (who, date, where; N-...)`. `wm note add --to <dest>` writes exactly this line, with the note id
  at the end, so the rule can be traced back to the moment it came from and the note forward to where it went.
- `--src` says where the fact came from: a message id or link, a meeting, `file:line`, or `tested <date>`. Left
  out, it records "user, in session".
- A rule with no source is "inferred" and belongs under "Open questions" until someone confirms it.

## When a rule is overturned, change it in place

Keep the new rule with its date and source, and leave one sentence about the old one after it
("(changed 12 March: used to be ...)"). Do not delete it silently, and do not write a second copy somewhere else.

When a people card disagrees with what the person originally said, follow the card: it holds the later correction.
When two files disagree, do not pick a side: mark the conflict in both and ask the user.

## What counts as proof

- Say how far a test went: list "tested up to here" apart from "not tested". One success is not a permanent
  guarantee.
- Evidence comes in layers that do not stand in for each other: code read, offline test, test in the target
  environment, deployment receipt, a result the user can see. Record each separately.
- "Nothing failed in this run's scope" is not "acceptance is complete". Skipped items, known gaps and evidence you
  are still waiting for do not count as passed.
- To tell whether a check really checks: remove the fix it is meant to guard. If the check still passes, it is
  not a check.
- A record in a side system is not the system of record agreeing (the draft was saved, the helper tool received
  it, the official system has nothing). Trust the read-back from the official one.
- For policies, benefits and penalties, the latest official text wins; record its version, effective date and
  page. Training summaries and old announcements are history only.

## Receipts

A receipt is what lets a completion claim go out.

- `wm receipt add "<what was verified, on which pages or environments>"` records one you state yourself
  (`claimed`). A message that relies on it always goes to the user for approval, who sees the receipt's text.
- `wm receipt add "<what>" --by-cmd <command id> --expect <regex>` lets a registered read command prove it
  (`checked`): `wm` runs the command and writes the receipt only if the output matches. Only a checked receipt
  lets a completion claim go out under a standing approval.
- Look at every affected place, including old pages and old versions. If you verified part of it, the receipt and
  the message both say which part.
- Sent, read back, replied to, acted on, and artefact verified are different states. `wm action` tracks the first
  two; the rest are receipts or item states.
