# Processes at Tidepool, as state machines

Read a row with `wm sm <state>`. Format: `references/state-machines.md` in the `workplace-mapping` skill. Check with `wm lint state-machines`.
Our actions carry a mark: ✅ the agent may do it; 🙋 show the user first; 📖 read and judge only.

## F. Rolling out a feature behind a flag

| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |
| F0 Proposal | Ilse agrees in the request thread / us 📖 | F1 | Link to the agreeing message saved in the project file | "Worth a look" is not a yes | Mara, #4361, 9 March 2026 |
| F1 Flag exists, default off | us ✅ | F2 | Settings page read back: flag exists and is off in all three environments | Created in staging only, 11 March 2026 (N-20260311-02) | tested 11 March 2026 |
| F2 On for internal accounts | us 🙋 | F3 | Viewed once with an internal and once with a normal account; behaviour differs | none yet | Mara, verbal, 9 March 2026 |
| F3 5% rollout | Okafor picks the window and makes the change | F4 | Error rate for the flag on the dashboard, 30 minutes after the change | none yet | weekly meeting, 12 March 2026 |
| F4 100% rollout | Okafor / us 📖 | F5 | Dashboard reads 100%; no related report in the support chat for 24 hours | none yet | verbal |
| F5 Flag and old branch removed | us 🙋 | done | Code search finds no use of the flag name; flag deleted in settings | none yet | verbal |

Open questions: how long after F4 before F5 is allowed; who stands in when Okafor is away.

## S. A question from support

| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |
| S0 Question arrives | Support posts in app-team and names us / us 📖 | S1 | Message id saved in the day's log | A question addressed to Jun is not ours | Mara, verbal, 9 March 2026 |
| S1 Answer drafted | us 🙋 | S2 | `wm outbound check` passes and the user approved the draft | none yet | tested 10 March 2026 |
| S2 Answer sent | us 🙋 | done | The sent message read back from the thread | Replying at the channel root (N-20260310-01) | Mara, #4379, 10 March 2026 |

Open questions: whether support wants a ticket as well when the answer is "this is a bug".

## Conflicts between files

None found.
