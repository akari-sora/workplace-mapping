# State machines: one table per process

To read a process, do not open the file: `wm sm` lists the processes, `wm sm F` lists one, `wm sm F2` prints one
row. This page is for writing them.

Six fixed columns, in this order:

| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |

- **State**: starts with an id, capital letters then digits (`F0`, `F1`). The letters name the process. Use the id
  afterwards in conversation, handoffs and other files.
- **Trigger / who**: what moves it forward and who does that. Mark our own actions: `✅` the agent may do it;
  `🙋` show the user first and act only on their yes (every outbound message, write and submission is 🙋 unless a
  standing approval covers it); `📖` read and judge only.
- **Verified by**: name the thing to look at: a read-back, a receipt, a result someone else can see. "The command
  exited 0" and "the page said success" are not enough. If it cannot be verified, write "unverified".
- **Pitfalls**: only ones that actually happened, with a date. Add one with
  `wm note add "<what went wrong>" --kind pitfall --to pitfall:<state>`.
- **Source**: who, which day, which message, meeting or file and line; or "tested (date)". Mark anything only
  described aloud and never run by you as "verbal".
- After the table, one line `Open questions: ...` for the steps you do not know yet. Do not fill the table by
  guessing.
- A piece of work "at F2" means F2 is the step to do next. The row says who does it and how you will know it is
  done; once verified, move the work on with `wm item set <id> --state <next> --evidence "<what you saw>"`.

Example (invented; rolling out a feature flag):

| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |
| F0 Proposal | Product owner agrees in the request thread / us 📖 | F1 | Link to the agreeing message saved in the project file | Reading "worth a look" as a yes | message link (date) |
| F1 Flag exists, default off | us ✅ | F2 | Settings page read back: flag exists, off in every environment | Created in staging only | tested (date) |
| F2 On for internal accounts | us 🙋 | F3 | Viewed once with an internal and once with a normal account; behaviour differs | Internal list still holds accounts of people who left | owner, verbal (date) |
| F3 5% rollout | On-call engineer | F4 | Error rate for the flag on the dashboard, 30 minutes after the change | Rolled out late on a Friday with nobody watching | weekly meeting (date) |
| F4 100% rollout | On-call engineer / us 📖 | F5 | Dashboard reads 100%; no related report in the support channel for 24 hours | Treating "nobody complained" as "it works" without opening the dashboard | verbal |
| F5 Flag and old branch removed | us 🙋 | done | Code search finds no use of the flag name; flag deleted in settings | One old branch still reads the flag | tested (date) |

Open questions: who picks the rollout window for F3; how long after F4 before F5 is allowed.

Then run `wm lint state-machines`. It rejects a missing verification, a missing source, a next state that does not
exist, and duplicate ids. Other tables and free text in the same file are left alone, so a process can carry
whatever extra notes it needs.
