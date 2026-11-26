---
name: {{name}}-daily
description: The daily routine at {{name}}: working hours, recurring meetings, status reports and their deadlines, leave and absence notices, and the start-of-day and end-of-day steps. Read at the start and end of each working day and before any recurring report is due.
---

# {{name}}: daily routine

## Start of day

1. `wm brief`, then pick up from the last handoff's "Start here".
2. `wm next` for what can be done now.

## Recurring

| What | When | Where | How it is verified |
| --- | --- | --- | --- |

## End of day

`wm wrapup write --start "<first thing to do tomorrow>"`. It writes the handoff into `worklog/YYYY-MM-DD.md` and checks it; report after PASS.

Open questions:
