# `wm` command reference

`wm` is the command-line half of workplace-mapping. The skill tells an agent what to record and when; `wm` checks
that it was recorded, tells the agent what to do next, and blocks the two mistakes that cost the most: saying
"done" without proof, and sending a message that breaks a rule someone already taught you.

- Python 3.11 or later, standard library only. No network access in any command except the probe you give `wm watch`.
- Every command reads files under one project root. Nothing here is specific to any company.
- `wm` has no connector of its own. It reaches chat tools, ticket systems and remote desktops only by running the
  commands you register, and it runs the ones that write once, after an approval.
- Every command prints a few lines of plain text; `--json` prints the full result object instead (see "Output:
  short text by default"). The exceptions are `wm run` (the command's own output), `wm watch` (one line per event)
  and the `--hook` modes (the JSON a Claude Code hook expects).

## Commands at a glance

| To | Run |
| --- | --- |
| See where things stand | `wm brief`, `wm next` |
| Read one process row, or one person's card | `wm sm [ID]`, `wm card NAME` |
| See and use what is registered | `wm catalog`, `wm run ID`, `wm access check` |
| Record something learned | `wm note add TEXT --to DEST`, `wm rule add`, `wm standing add`, `wm card add` |
| Track work, promises and proof | `wm item set`, `wm commitments add` / `done`, `wm receipt add`, `wm log` |
| Send a message or change something | `wm action send`, then `wm action approve ID --by TEXT --run` if it asks |
| End the day | `wm wrapup write --start TEXT` |
| Check the project, wait for something | `wm lint`, `wm doctor`, `wm watch` |

## Project root and configuration

The root is the first of: `--root PATH`, the `WM_ROOT` environment variable, the nearest parent of the current
directory that holds `workplace.toml`. With none of them, every command exits 64.

`workplace.toml` (all keys optional except `workplace.name`; defaults shown):

```toml
[workplace]
name = "tidepool"            # required; lowercase letters, digits and hyphens
timezone = ""                # IANA name such as "Asia/Tokyo"; empty means the machine's local time
us = ["us", "me", "我方"]     # how "our side" is written in tables and ledgers (case-insensitive)

[paths]                      # all relative to the root
skills = ".agents/skills"
state_machines = []          # list of files; empty means every state-machines.md found under paths.skills
people_cards = ""            # empty means the first people-cards.md found under paths.skills
commitments = "worklog/commitments.md"
notes = "worklog/inbox.md"
worklog = "worklog"          # one file per day, named YYYY-MM-DD.md
evidence = "data/evidence.json"
rules = "rules.toml"
state = ".wm"                # runtime markers; keep it out of git

[handoff]
start_heading = "Start here"         # also accepted: "明天從哪開始"
unpushed_heading = "Unpushed commits" # also accepted: "未推提交"
lookback_days = 7

[next]
stale_hours = 72             # evidence older than this is reported as unverified

[lint]
global_skills = []           # folders of skills shared across companies (may start with ~)
private_terms = ""           # file with one term per line that must not appear in global_skills
```

`--now ISO8601` replaces the clock, and works only when the environment variable `WM_TEST_CLOCK=1` is set (the
tests and the example walkthrough set it); without that variable `--now` is a usage error. Expiry and staleness
are safety checks, so the clock they use is not something a caller can move by passing a flag. A timestamp later
than now is never fresh: an item whose `at` is in the future is `unverified`, and an action whose draft time is
in the future cannot be approved or run (`bad_clock`, exit 2). A value without an
offset is read in the configured timezone. "Today" is always the date in the configured timezone.

## Systems, channels and commands

Companies differ in where things live (your own machine, behind VPNs, inside a remote desktop, behind a ticket
queue) and in which chat tool they use. `wm` does not know any of them. You describe them once in `workplace.toml`,
and from then on the agent uses the same four verbs everywhere: `wm catalog`, `wm run`, `wm access check`,
`wm action`.

```toml
[workplace]
resident_host = ""             # hostname of the machine that holds logins and runs long-lived services; empty = any

[paths]
actions = ".wm/actions.jsonl"  # append-only log of previews, approvals and sends; keep it out of git

[[system]]
id = "tracker"                 # required; letters, digits, dot, hyphen; unique among systems
what = "ticket tracker"
reach = "vpn:office"           # free text: direct | vpn:<name> | desktop:<name> | via:<host> | person:<who>
hosts = []                     # hostnames that can reach it; empty = any machine
login = "single sign-on plus a one-time code"
probe = "curl -fsS -m 5 -o /dev/null https://tracker.example/health"   # read-only; exit 0 means reachable
may_leave = "notes"            # free text shown to the agent: nothing | notes | code | anything
src = ""                       # where this came from

[[channel]]
id = "release"                 # required; unique among channels
kind = "custom"                # informational: slack | teams | telegram | dingtalk | lark | wecom | custom
what = "what went live and what is about to"
mention = "@\\S+"              # regex that counts as a mention in a draft; this is also the default
threads = true
max_chars = 0                  # 0 = no limit
send = ""                      # command that delivers a message; the payload arrives on stdin
readback = ""                  # optional: command that prints the sent message's id once it is visible
system = ""                    # optional: the [[system]] this channel depends on
src = ""

[[command]]
id = "tickets.mine"            # required; unique among commands
what = "my open tickets"
run = "tools/tickets mine"     # shell command, run with the root as working directory
writes = false                 # true: can only be run through wm action
system = "tracker"             # optional
timeout = 60                   # seconds
max_output = 8000              # characters wm run prints before saving the rest to a file; 0 = no limit
readback = ""                  # optional, for writes: prints the result id once the change is visible
```

`wm lint` gains a `config` sub-check: `bad_config` (fail) for a missing or malformed id, a duplicate id, an invalid
`mention` regex, a `system` that names no `[[system]]`, or a `[[command]]` without `run`. The `secrets` sub-check
also scans `workplace.toml`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok |
| 1 | warn: usable, with items to look at |
| 2 | fail: a check did not pass, or an outbound draft broke a rule |
| 3 | timeout (`wm watch` only) |
| 4 | unverified: a file that should hold evidence is missing, unreadable or stale. Never read this as "nothing to do" |
| 64 | usage error: bad arguments, no root, invalid `workplace.toml` |

When several apply, the highest of 4, 2, 1 wins in that order (4 over 2 over 1).

With `--json`, every result has `ok` (true only for exit 0) and `status` (`ok`, `warn`, `fail`, `unverified`).
Problems are listed in `problems`, each `{"code": "...", "level": "warn|fail|unverified", "where": "file:line",
"detail": "..."}`. `--pretty` is `--json` with indentation. The JSON shapes shown under each command below are what
`--json` prints.

## Output: short text by default

An agent pays for every character a command prints, so `wm` prints short text and says nothing it does not need to.

- Every command prints compact text by default. `--json` prints the full result object on one line instead, and
  `--pretty` means `--json` with indentation. `wm run`, `wm watch` and the `--hook` modes are unchanged.
- The text is a rendering of the same result object, so nothing is lost: `--json` always has every field.
- Line 1 is the status: `ok`, `warn`, `FAIL` or `UNVERIFIED`, optionally followed by a few words
  (`ok PASS`, `ok sent A-20260312-01 #5000 verified`). A check that passes with nothing to report prints just `ok`.
- Then one line per problem: `<level> <code> <where> <detail>`, leaving out empty parts. `where` and the word
  `unverified` are always kept.
- Then the body: one line per item, only the fields needed to act on it, no key names where the position is obvious,
  no blank lines, no decoration.
- Any result that is not `ok` ends with `next: <the exact command to run, or the one thing to do>`. For `needs_user`
  that line is `next: show the payload to the user; after their yes: wm action approve <id> --by "<their words>" --run`.
- Empty categories are left out (no `notes open: 0`).

Shapes to follow (exact spacing is free; golden tests pin what you implement, run against a copy of
`examples/tidepool` with `--now 2026-03-12T18:30:00+00:00`):

```text
$ wm next
ok
do    REQ-15 Moon phase strip on the day view | F1 Flag exists, default off | verify: Settings page read back: flag exists and is off in all three environments | pitfall: Created in staging only, 11 March 2026 (N-20260311-02)
ask   REQ-9 CSV export of saved locations | F2 On for internal accounts | verify: Viewed once with an internal and once with a normal account; behaviour differs
wait  REQ-12 Tide alerts: push a notice one hour before low tide | F3 5% rollout | on: Okafor picks the window and makes the change

$ wm outbound check --to Ilse --channel release --file drafts/blocked.txt
FAIL
violation claim-needs-receipt require_receipt A completion claim needs a verified receipt that covers every affected place. (N-20260312-02)
violation release-mentions-requester require A release notice must @ the person who asked for the change. (N-20260312-01)
drafting:
- Answer the question that was asked, first.
...
card Ilse:
- Role: product owner; her agreement in the request thread is what starts a rollout
...
next: fix the draft; for a completion claim add --receipt <id> or --verified "<what you checked and where>"

$ wm sm F1
F1 Flag exists, default off
who: us ✅   next: F2
verify: Settings page read back: flag exists and is off in all three environments
pitfalls: Created in staging only, 11 March 2026 (N-20260311-02)
source: tested 11 March 2026
```

`pitfall:` is left out when the cell is empty, a dash, `none`, `none yet` or `無`. `verify:` and `pitfall:` appear
only on `do` and `ask` lines. Size budgets for the example, checked by tests: `wm lint` and a passing
`wm outbound check` print exactly `ok`; `wm next` at most 750 characters; `wm brief` at most 800; `wm catalog` at
most 700.

**Cards.** The drafting checklist and the recipient's card are printed whenever an outbound check has violations
and whenever an action stops with `needs_user`. They are not printed on a pass. There is no "already shown"
memory: a session may have been compacted since.

## Record lines

Notes and commitments are single Markdown list lines so people can read them and tools can parse them:

```text
- [ ] [note id=N-20260312-01 kind=correction who=Mara src=#4411] Reply in the thread, not at the channel root.
- [x] [note id=N-20260312-02 kind=pitfall who=us src="build log 2026-03-12" filed=.agents/skills/tidepool-dev-release/SKILL.md] Version is set in two files.
- [ ] [commitment id=C-20260312-01 due=2026-03-14 to=Mara ball=us src=#4412] Send the migration checklist.
- [x] [commitment id=C-20260310-02 due=none to=Jun ball=us src=#4390 proof=#4433] Share the staging link.
```

- Shape: `- [ ]` or `- [x]`, then `[<type> key=value ...]`, then free text. A value with spaces is wrapped in double
  quotes. Keys may appear in any order. Lines that are not list items are ignored; a list item that starts with
  `[note` or `[commitment` but does not parse is a `fail` problem with code `bad_record`.
- Ids: `N-YYYYMMDD-NN` for notes, `C-YYYYMMDD-NN` for commitments, unique within the file.
- `src` is required on both. It says where the fact came from: a message id, a meeting, `file:line`, or
  `tested YYYY-MM-DD`.

## Reading

### `wm brief [--hook]`

Start-of-session brief: hostname, current directory, git branch and count of changed files, the newest worklog
within `handoff.lookback_days` with the text under its start heading, commitments due within two days, undated
commitments, and unfiled notes. Anything it cannot read is listed under `unverified` instead of being dropped.

`--hook` prints `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<the brief as
text>"}}` and always exits 0. On any internal error it prints the same shape with a one-line error as the context.

### `wm next [--all]`

Says where each tracked item sits and what can be done now. It reads `paths.evidence`:

```json
{
  "items": [
    {"id": "REQ-12", "title": "Pricing footer", "state": "R3",
     "evidence": "review approved, read back from the tracker", "at": "2026-03-12T10:00:00+09:00"}
  ],
  "receipts": [
    {"id": "RCPT-7", "what": "new footer visible on /pricing and on two legacy pages",
     "at": "2026-03-12T16:20:00+09:00", "verified": true}
  ]
}
```

An item's `state` is the step to do next: the row says who does it and how it is verified. For each item it
finds the state row and reports `flow`, `state`, `state_name`, `trigger`, `next`, `verify`,
`pitfalls`, `source`, and `status`:

| status | When |
| --- | --- |
| `actionable` | the Trigger cell names our side and carries `✅` |
| `needs_user` | our side with `🙋` |
| `read_only` | our side with `📖`, or our side with no mark |
| `waiting` | the Trigger cell does not name our side; `waiting_on` holds the cell text |
| `done` | the item's `state` is a terminal word; hidden unless `--all` |
| `unverified` | unknown state id, missing or unparseable `at`, or `at` older than `next.stale_hours` |

`now` lists, in order: open commitments that are ours and overdue or due today, `actionable` items, `needs_user`
items. Exit 4 when the evidence file is missing or unreadable or any item is `unverified`; the rest is still printed.

### `wm sm [FLOW|STATE]`

Reads the state machines without opening the file. No argument: one line per flow (`F Rolling out a feature behind
a flag  F0-F5`, the title taken from the nearest heading above the table). A flow id: one line per state
(`F1 Flag exists, default off | us ✅ | -> F2`). A state id: the full row as in the shape above. Unknown id: exit 2.

### `wm card NAME` and `wm card add NAME [--role TEXT] [--aliases A,B]`

`wm card NAME` prints the drafting checklist and that person's card. `add` appends a new section
(`## NAME`, an `Aliases:` line when given, a `- Role:` line when given) to the people-cards file, before a final
`Open questions:` / `待確認：` line if the file ends with one. An existing card or alias with that name: exit 2,
`card_exists`.

### `wm catalog [--kind commands|channels|systems]`

Lists what is registered: for commands `id`, `what`, `writes`, `system`; for channels `id`, `kind`, `what`,
`threads`, `max_chars`, whether `send` is set; for systems `id`, `what`, `reach`, `hosts`, `login`, `may_leave`,
whether `probe` is set. Command lines themselves are not printed.

### `wm run ID [-- ARGS...]`

Runs a registered read-only command with the root as working directory. `ARGS` are appended, shell-quoted. The
command's stdout and stderr pass through untouched and its exit code becomes `wm`'s, so `wm run` adds no JSON of its
own on success. Before running:

- unknown id: exit 64.
- `writes = true`: exit 2 with a JSON problem `use_action`. Nothing runs.
- the command names a system whose `hosts` is non-empty and does not include this machine's hostname: exit 2,
  `wrong_host`, listing the hosts that can. Nothing runs.
- the command exceeds its `timeout`: it is killed, exit 3, JSON problem `timeout` on stderr.

### `wm access list` and `wm access check [ID...] [--timeout SEC]`

`list` prints the systems. `check` runs each selected system's `probe` (default: all; default timeout 10 seconds
each) and reports one of:

| status | When |
| --- | --- |
| `reachable` | the probe exited 0 |
| `unreachable` | the probe exited non-zero or timed out; `exit` holds the code or `"timeout"` |
| `no_probe` | the system has no probe |
| `wrong_host` | `hosts` is non-empty and does not include this machine; the probe is not run |

Exit 4 when any system is `unreachable` (not reaching a system is never "nothing there"). Exit 1 when nothing was
probed. Exit 0 otherwise. Hostnames are compared case-insensitively on the part before the first dot.

### `wm note list [--open] [--kind KIND]`

Lists notes. `--open` keeps only unfiled ones.

### `wm commitments list [--open] [--due-within Nd]`

`--due-within 2d` keeps open commitments due today through two days from now, plus overdue ones. Commitments with
`due=none` are returned separately under `undated`.

## Writers: one call, no file opened

The agent does not open a ledger, card or table to record something. Each kind of record has one command that
writes one line.

All writers that touch a Markdown or TOML file follow the same rules:

- **Append-only**: they add text and never rewrite or reorder what is there. The single exception is a state row's
  Pitfalls cell, where text is added inside the cell.
- **Atomic**: the new content is written to a temporary file in the same folder, parsed again (the Markdown heading
  or table row is still found and, for a table, the same state ids exist in the same order; TOML loads and passes the
  rules lint), and only then moved over the original with `os.replace`. If the re-parse fails nothing changes.
- **Byte-exact**: every byte outside the inserted text is unchanged, including line endings and a missing final
  newline elsewhere in the file.
- **Refuse rather than guess**: a missing file is `dest_missing`; a heading that is not found is
  `heading_missing`; a heading text that occurs more than once is `heading_ambiguous`; likewise `state_missing`,
  `state_ambiguous`, `no_card`. All are exit 2 and change nothing.
- In a table cell `|` is written as `\|` and line breaks become spaces.
- **One line means one line.** A value that would be written into a record, a card, a heading or a TOML string and
  contains any line separator (including U+0085, U+2028 and U+2029) or another control character is refused with
  `bad_value`, exit 2. Alias lists are split on `,`, `、` and `，` everywhere, before any duplicate check.
- **Two files or none.** `wm rule add --from` changes the rules file and the note's line: it stages and validates
  both results first; if the second replacement fails, the first file is put back and the error names the file that
  failed. (`wm note add --to` is different on purpose: a refused destination still leaves the note recorded, open,
  so nothing the agent learned is lost.)

### `wm note add TEXT [--kind KIND] [--who WHO] [--src SRC] [--to DEST]`

`--kind` defaults to `correction`, `--who` to `user`, `--src` to `user, in session`. With `--to` the note is
written to its destination and recorded as filed in the same call. If the destination is refused, the note is still
recorded (open), the exit code is 2, and the `next:` line gives the `wm note file` command to finish it.

`DEST` is one of:

| DEST | Where the line goes |
| --- | --- |
| `card:NAME` | end of that person's section in the people-cards file |
| `drafting` | end of the `Before drafting` / `擬稿前` section |
| `pitfall:STATE` | the Pitfalls cell of that state row; an empty cell, a dash, `none`, `none yet` or `無` is replaced, otherwise `; ` and the text are added |
| `PATH#HEADING` | end of the section under that heading in the file (path relative to the root, inside it) |
| `PATH` | end of the file |

`PATH` must be a Markdown file (`.md`) under the root. The state folder, the ledgers (`paths.notes`,
`paths.commitments`, `paths.evidence`, `paths.actions`), `workplace.toml` and the rules file are refused with
`dest_invalid`.

The line written is `- TEXT (WHO, YYYY-MM-DD, SRC; N-id)`; in a Pitfalls cell it is `TEXT, YYYY-MM-DD (N-id)`.
"End of a section" means after its last non-blank line, before the blank lines that precede the next heading of
the same or a higher level.

### `wm note file ID --to DEST`

Same destinations. If the destination already contains the id, the note is only marked filed; otherwise the line
is written first. A note that is already filed is refused with `already_filed`; `filed=` is never replaced.

### `wm rule add --id ID --message TEXT (--src SRC | --from NOTE_ID) [checks and filters]`

Appends an `[[outbound]]` block to the rules file, creating it if needed. Options map to the rule keys: `--when`,
`--channels A,B`, `--to A,B`, `--require`, `--forbid`, `--require-receipt [claimed|checked]`, `--require-mention`,
`--max-chars N`, `--check CMD`. At least one check is required. `--from NOTE_ID` uses the note id as `src` and marks
that note filed to the rules file if it is still open (a note already filed elsewhere is left unchanged). A duplicate id, an invalid regex or a result with a `fail`-level rules-lint problem:
exit 2, nothing written. Warnings about other entries (an expired standing approval, say) do not block. Ids are
letters, digits, dot, hyphen and underscore.

### `wm standing add --id ID --kind KIND --targets A,B --src SRC [--to A,B] [--match RE] [--max-chars N] [--expires DATE]`

Appends a `[[standing]]` block the same way.

### `wm commitments add --to WHO --src SRC [--due YYYY-MM-DD] [--ball us|them] TEXT`

Appends a commitment (`due` defaults to `none`, `ball` to `us`).

### `wm commitments done ID --proof REF`

Marks it `- [x]` and adds `proof=REF`. Unknown id: exit 2, code `unknown_id`. A commitment that is already done, or
whose line already carries a `proof`, is refused with `already_done`; a recorded proof is never replaced.

### `wm item set ID [--state STATE] [--title TEXT] [--evidence TEXT]`, `wm item done ID [--evidence TEXT]`, `wm item list`

Creates or updates an item in the evidence file and stamps `at` with the current time. A new item needs `--state`. `wm item set` needs at least one of `--state`, `--title`, `--evidence`, and stamps
`at` only when `--state` or `--evidence` is given: a title change, or a bare call, does not make old evidence fresh.
A state that is neither in the state machines nor a terminal word is exit 2, `unknown_state`, unless `--force`.
`done` sets the state to `done`. The evidence file is rewritten atomically with two-space indentation, keeping key
order and every other entry.

### `wm receipt add WHAT [--item ID] [--by-cmd COMMAND_ID --expect REGEX [-- ARGS...]]`

Appends a receipt with id `R-YYYYMMDD-NN`, `verified: true`, the current time, and `how`:

- `claimed`: the agent states what it verified.
- `checked`: with `--by-cmd`, `wm` runs that registered read-only command itself, and writes the receipt only if
  it exits 0 and its stdout matches `--expect` (searched, not anchored). `--expect` must not be empty and must
  match at least one character. The receipt stores the command id, the arguments
  passed after `--`, the SHA-256 of the matched text and of stdout, and the matched text itself (first 200
  characters) only after the same redaction that applies to printed output; a secret-like match is never written
  to the evidence file. Otherwise exit 2, `check_failed`, nothing written.

A receipt with no `how` (written by hand) counts as `claimed`.

### `wm log TEXT`

Appends `- HH:MM TEXT` to today's worklog, creating the file with a `# YYYY-MM-DD` title. If the file already has
the handoff's start heading, the line goes immediately before that heading.

## Sending and changing things

### `wm outbound check --to NAME [--channel CH] [--receipt ID]... [--file PATH]`

Checks a message draft (from `--file` or stdin) before it is sent. Rules live in `paths.rules`:

```toml
[[outbound]]
id = "claim-needs-receipt"
when = "(?i)\\b(done|shipped|deployed|fixed)\\b|完成|已上線"   # regex on the draft; omitted = always applies
channels = ["release"]       # optional: only these channels
to = ["Mara"]                # optional: only these recipients (case-insensitive)
require_receipt = true       # at least one --receipt must name a receipt in the evidence file with "verified": true
require = "@\\w+"            # optional: regex the draft must match
forbid = "\\bASAP\\b"        # optional: regex the draft must not match
max_chars = 400              # optional
message = "Completion claims need a verified receipt."
src = "N-20260312-03"        # required: the note, message or meeting the rule came from
```

A rule applies when its `kinds`, `channels` and `to` filters match (absent filters match everything) and `when` is
absent or matches. `kinds = ["chat"]` limits a rule to chat messages; without it the rule also covers `command` and
`other` actions. Each failed check is a violation `{"rule", "check", "message", "src"}` where `check` is
`require_receipt`, `require`, `forbid` or `max_chars`.

The result also carries `card`: the checklist under the `Before drafting` (or `擬稿前`) heading of the people-cards
file, and the full section of the person named by `--to`. A person section is a level-2 heading whose text equals
the name, or whose section has an `Aliases:` (or `別名：`) line listing it, compared case-insensitively. No card for
that person: `card.person` is null and the result gains a warn problem `no_card`. In text output the checklist and card are
printed only when there are violations; `wm card NAME` prints them on request.

Exit 0 with no violations (1 if only `no_card`), 2 with violations, 4 when the rules file exists but cannot be
read or parsed.

With channels configured:

- When `--channel` names a configured channel, the result carries `channel`: `id`, `kind`, `what`, `threads`,
  `max_chars`. A draft longer than a non-zero `max_chars` is a violation with rule `channel:<id>`, check
  `max_chars`.
- A rule may set `require_mention = true`: the draft must match the channel's `mention` regex (`@\S+` when the
  channel is unknown or sets none). The violation's check is `require_mention`. It counts as a check for
  `rule_without_check`.
- When channels are configured and `--channel` names none of them: warn problem `unknown_channel`.

### `wm action`: preview, approval, one run, read-back

Anything that writes or sends goes through an action. The log at `paths.actions` is append-only JSON lines; an
action's state is the result of replaying its events. Writers take an exclusive file lock.

States: `draft` → `approved` → `running` → `sent` → `verified`. Side states: `failed`, `cancelled`. Two derived
states: `expired` (a `draft` or `approved` action past its expiry) and `unknown` (`running` with no finish event:
the run was interrupted or timed out, so nobody knows whether it went out).

**`wm action preview --kind chat|command|other --target TARGET [--to NAME] [--receipt ID]... [--file PATH] [--ttl MIN] [-- CMD...]`**

Reads the payload from `--file` or stdin and records a `draft` with id `A-YYYYMMDD-NN`, the payload, its SHA-256 and
an expiry (default 30 minutes).

- `chat`: `TARGET` is a channel id. The outbound check runs first with `--to`, `--receipt` and the channel. With
  violations nothing is recorded: exit 2 and the same output as `wm outbound check`.
- `command`: `TARGET` is a `[[command]]` id with `writes = true`; anything else is exit 64.
- `other`: `TARGET` is free text (a ticket queue, a person). The command that carries it out, if there is one, is
  given here after `--`. It is stored with the draft, shown to the user together with the payload, and is the only
  command that action will ever run.

`-- CMD` is a usage error (exit 64) for `chat` and `command`: those always run the registered `send` or `run`.
The outbound rules run first for every kind (for `command` and `other` the rule's channel is the target). With
violations nothing is recorded. A payload that contains a secret-like value (the patterns of `wm lint secrets`) is
refused with `secret_like`, exit 2, and nothing is recorded: what the user is shown is always the exact payload.

Output: the action, the exact payload, and for `chat` the `card` and `channel` blocks.

**`wm action approve ID --by TEXT`**

Records the user's approval; `TEXT` is what they said and must not be empty. Only a `draft` can be approved. `expired`: exit 2, code
`expired`. Any other state: exit 2, code `bad_state`.

**`wm action run ID [--timeout SEC]`**

Runs the action once. It must be `approved` and not expired; otherwise exit 2 with `bad_state` or `expired`, or
exit 4 with `unknown` for an action already in the unknown state. The command is fixed by the action and `wm action run` takes none
of its own: the channel's `send` for `chat`, the command's `run` for `command`, the command stored at preview for
`other`. An `other` action with no stored command cannot be run; a person carries it out and it is then settled with
`wm action resolve ID --state sent --proof REF`, which for such an action is allowed from `approved`. A `chat` or
`command` action whose target has no command configured is exit 64 at preview, before anything is recorded. A `running` event is written and flushed **before**
the command starts, and records what is about to run: the channel or command id and the SHA-256 of its command
line, or for `other` the command text. The command runs through the shell from the root with the payload on stdin and these
environment variables: `WM_ACTION_ID`, `WM_KIND`, `WM_TARGET`, `WM_TO`, `WM_PAYLOAD_SHA256`.

- exit 0: state `sent`; the first 2000 characters of stdout are stored as `result` (for example the platform's
  message id). `wm` exits 0.
- non-zero exit: state `failed`, with the code and the first 2000 characters of stderr. `wm` exits 2.
- timeout (default 60 seconds) or the process being killed: no finish event is written, so the state is
  `unknown`. `wm` exits 4.

An action never runs twice. After `failed`, preview a new one. After `unknown`, read the destination back and
resolve it.

**`wm action readback ID --proof REF`**: `sent` → `verified`, storing where the delivered result was seen.

**`wm action resolve ID --state sent|failed --proof REF`**: settles an `unknown` action after a manual read-back.

**`wm action cancel ID`**: a `draft` or `approved` action → `cancelled`.

**`wm action show ID`**, **`wm action list [--open]`**: `--open` keeps `draft`, `approved`, `unknown`, and `sent`
without read-back.

Effects elsewhere:

- `wm wrapup check`: an `unknown` action is `action_unknown` (fail); a `sent` action without read-back is
  `action_unverified` (warn).
- `wm brief` lists open actions. `wm next` puts `unknown` actions first in `now`.
- `wm doctor` reports whether `paths.actions` is git-ignored.

### Standing approvals: ask once, not every time

Asking the user before every send is slow and wears them out. When the user says a class of action no longer needs
their yes, record it as a standing approval in `paths.rules`:

```toml
[[standing]]
id = "ack-on-call"
kind = "chat"                  # chat | command (an `other` action always needs the user)
targets = ["on-call"]          # channel ids, command ids or free-text targets; required, at least one
to = []                        # optional: only these recipients
match = "(?i)^(ack|received|on it)\\b"   # optional: regex the payload must match
max_chars = 80                 # optional
expires = "2026-12-31"         # optional: last day it applies
src = "user, 2026-03-12: acknowledgements in on-call do not need my OK"   # required: the user's words
```

`wm action preview` applies standing approvals after the outbound check passes: when one matches, the action is
recorded as `approved` at once, with `approved_by` set to `standing:<id>`. A draft with violations is never
auto-approved. `wm lint rules` reports `bad_rule` for a standing approval without `id`, `kind`, `targets` or `src`,
and `standing_expired` (warn) for one past its `expires`.

**`wm action send ...`** takes the same arguments as `preview` (plus `--timeout`) and
does the whole path in one call: preview, then run if a standing approval applied. Without one it stops at `draft`
and exits 1 with `status: "needs_user"`, the action id and the exact payload, so the agent can show the user
everything in one message.

**`wm action approve ID [ID...] --by TEXT`** accepts several ids, so one answer from the user can approve a batch.
It reports each id separately and exits 2 if any could not be approved; the others are still approved.

### Proof, approvals and fewer calls

**Receipts on a send.** `wm action preview` and `wm action send` accept `--verified TEXT`: it writes a `claimed`
receipt and attaches it. `wm outbound check --verified TEXT` treats the text as a claimed receipt without writing
anything. A rule's `require_receipt = true` (or `"claimed"`) is met by any verified receipt; `require_receipt =
"checked"` only by a `checked` one.

**An asserted proof always goes to the user.** When a draft triggered any `require_receipt` rule and none of the
receipts attached is `checked`, standing approvals are not applied: the action stops as a draft with `needs_user`,
and the receipt's text is shown with the payload. Only a `checked` receipt lets a completion claim go out under a
standing approval.

**`wm action approve ID [ID...] --by TEXT [--run]`.** With `--run` each action that was approved is run at once.

**Automatic read-back.** A `[[channel]]` or `[[command]]` may set `readback = "CMD"`. After a run that exits 0,
`wm` runs it from the root (30 second timeout) with `WM_RESULT` (the stored result), `WM_ACTION_ID` and
`WM_TARGET`. The action becomes `verified`, with proof `readback:auto`, only if the read-back exits 0 **and** its
stdout contains the stored result (leading and trailing whitespace ignored), which must be non-empty. Anything else leaves the action `sent`, adds the warn
problem `readback_failed`, and exits 1. A zero exit code alone never verifies.

**A rule can run any check.** An `[[outbound]]` rule may set `check = "CMD"`. It runs through the shell from the
root with the draft on stdin and `WM_TO`, `WM_CHANNEL` in the environment (10 second timeout). Exit 0 passes. Any
other exit is a violation with check name `check`, the rule's message, and the first line of the command's stdout
as `detail`. A timeout or a command that cannot start is the problem `check_unverified` (exit 4); it is never a
silent pass. `check` counts as a check for `rule_without_check`.

**`wm run` keeps noisy commands small.** A `[[command]]` may set `max_output` (characters; default 8000; 0 means no
limit). When stdout is longer, `wm run` prints the first `max_output` characters, then one line
`... truncated: N characters in total, full output in <state>/last/<id>.txt`, and saves the whole stdout there.
`--full` lifts the limit for one call. stderr and the exit code are unaffected. The saved file is the command's raw
output, created readable by the owner only (mode 0600); the state folder must stay out of git, and `wm doctor`
warns (`state_not_ignored`, `actions_not_ignored`) in its text output when it is not.

### `wm outbound hook --text-field FIELD [--to-field FIELD] [--channel-field FIELD] [--receipt-field FIELD]`

Claude Code `PreToolUse` adapter. Reads the hook JSON on stdin, takes the named fields from `tool_input`, runs the
same check, and on violations exits 2 with the violations on stderr, which blocks the tool call and tells the model
why. Violations that were found always block, whatever else went
wrong. Only when no violation was found does an internal error, an invalid configuration or a check that could not
run let the call through (exit 0, with the reason on stderr).

## Ending a day

### `wm wrapup write --start TEXT [--note TEXT]... [--replace]`

The whole end-of-day path in one call.

1. Marks the wrap-up as begun.
2. Runs every check that does not depend on the handoff text: the records lint, plus `action_unknown` (fail),
   `action_unverified` (warn) and `item_unverified` (unverified: any item `wm next` reports as unverified, which
   includes stale ones) and `evidence_unverified` (the evidence file exists but cannot be read or parsed). If any is `fail` or `unverified`, nothing is written and the result says what to fix.
3. Writes the handoff to the end of today's worklog (creating the file): the start heading with `TEXT` and each
   `--note` as a bullet; then generated lists, each left out when empty: open items (one line each, as `wm next`
   prints them without verify and pitfall), open commitments, open actions; then the unpushed heading with one
   line per unpushed commit (short hash and subject) or `none`.
4. Runs the full wrap-up check and reports `PASS`, `WARN`, `FAIL` or `UNVERIFIED` as `wm wrapup check` does.

If today's worklog already has a start heading: exit 2, `handoff_exists`, unless `--replace`, which removes the
text from that heading to the end of the file and writes the new handoff there. `--replace` needs exactly one start
heading: with more than one it refuses with `heading_ambiguous` and changes nothing. `wm wrapup begin`, `check` and
`draft` stay as they are; `wm wrapup check` gains the `item_unverified` problem.

### `wm wrapup begin`

Writes `<state>/wrapup-pending`. Run it when the user says they are wrapping up.

### `wm wrapup check`

The usual path is `wm wrapup write`, which runs this for you. Checks the handoff and, on PASS, removes `wrapup-pending` and writes `<state>/wrapup-pass` (ISO time).

- `no_worklog_today` (fail): no `YYYY-MM-DD.md` for today in `paths.worklog`.
- `no_start_section` (fail): today's file has no heading whose text is the start heading, or nothing under it.
- `no_unpushed_section` (fail): no unpushed-commits heading. `none` or `無` under it is a valid entry.
- `unpushed_not_listed` (fail): the root is a git repository whose branch is ahead of its upstream, and a commit's
  short hash (first 7 characters) is not in that section. No upstream or git unavailable: `unpushed_unverified`
  (unverified). Not a git repository: skipped.
- Every problem from `wm lint records` at its own level.
- Headings inside fenced code blocks do not count.

`result` is `PASS`, `WARN`, `FAIL` or `UNVERIFIED`, matching exit 0, 1, 2, 4.

### `wm wrapup draft`

Prints a handoff skeleton in `markdown`: the start and unpushed headings, open commitments, `wm next`'s `now` list,
unfiled notes, and the git branch with its unpushed hashes. It writes nothing.

### `wm stop-gate`

Claude Code `Stop` hook. Reads the hook JSON on stdin.

- `stop_hook_active` is true: allow (print nothing, exit 0). This stops a block loop.
- No `<state>/wrapup-pending`: allow.
- Otherwise run the wrap-up check. PASS or WARN: allow. FAIL or UNVERIFIED: print
  `{"decision": "block", "reason": "..."}` naming each problem code and its `where`, exit 0.
- Invalid stdin, no root, or any internal error: allow, with the error on stderr.

## Checking, waiting and setup

### `wm lint`

Runs every check below and reports all problems. Sub-checks can be run alone: `wm lint records`, `wm lint
state-machines`, `wm lint rules`, `wm lint secrets`, `wm lint global`.

**records** (notes and commitments)
- `bad_record` (fail), `duplicate_id` (fail), `missing_src` (fail), `bad_due` (fail).
- `done_without_proof` (fail): a checked commitment with no `proof`.
- `overdue` (fail when `ball` is ours, warn otherwise): open and `due` before today.
- `filed_target_missing`, `filed_id_not_found` (fail): a filed note whose target is gone or no longer cites it.
- `note_unfiled` (warn for a note dated today, fail for older): notes are meant to be filed the day they are taken.
- A missing ledger file is not a problem; an unreadable one is `unverified` with code `unreadable`.

**state-machines**

A state-machine table is a Markdown table with exactly these six columns, in this order. English and Traditional
Chinese headers are both accepted:

| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |
| 狀態 | 觸發／誰 | 下一個狀態 | 怎麼驗證做到了 | 常見坑 | 來源 |

- The first token of the State cell is the state id: one or more capital letters followed by digits (`R0`, `QA12`).
  The letters are the flow id. The rest of the cell is the state name.
- `bad_state_id` (fail), `duplicate_state` (fail, within all configured files).
- `bad_row` (fail): a row of a state-machine table that does not have exactly six cells. Extra cells are never
  dropped silently.
- `missing_verify`, `missing_source` (fail): an empty cell or a bare dash.
- `unknown_next` (fail): the Next cell names an id that does not exist. `done`, `完成`, `end`, `—` and `-` are
  terminal. A Next cell may name several ids separated by `/` or `,`.
- `missing_auth` (warn): the Trigger cell names our side but carries none of the three marks. `✅` the agent may do it;
  `🙋` show the user first; `📖` read and judge only.
- `no_open_questions` (warn): a table not followed, before the next heading or table, by a line starting with
  `Open questions:` or `待確認：`.
- No state-machine file found: `unverified`, code `no_state_machines`.

**rules**: see `wm outbound check`. `bad_rule` (fail), `duplicate_rule` (fail), `rule_without_src` (fail),
`bad_regex` (fail), `rule_without_check` (fail). A missing rules file is not a problem.

**secrets**: scans every text file under `paths.skills`, `paths.worklog` and `rules.toml` for private key blocks,
`otpauth://` URIs, common token prefixes (`ghp_`, `github_pat_`, `glpat-`, `sk-`, `xoxb-`, `xoxp-`, `AKIA`), JWTs,
and `password|passwd|secret|token|api_key` followed by `=` or `:` and a literal of eight or more characters. Each
hit is `secret_like` (fail) with the file and line; the value itself is never printed.

**global**: when `lint.global_skills` and `lint.private_terms` are both set, every term found in a global skill is
`private_term_in_global` (fail). The term is printed as its line number in the terms file, not as text.

### `wm watch --name NAME --probe CMD [--every SEC] [--timeout SEC] [--done REGEX] [--warn-after N] [--state NAME]`

Runs `CMD` through the shell every `--every` seconds (default 60) and prints one line per event:

```text
2026-03-12T16:20:05+09:00 deploy EVENT status=running
2026-03-12T16:23:05+09:00 deploy WARN probe failed 3 times in a row (exit 1)
2026-03-12T16:26:05+09:00 deploy DONE status=live
2026-03-12T17:20:05+09:00 deploy TIMEOUT last=status=running
```

- `EVENT` when the probe's stdout (stripped, first 400 characters) differs from the last seen value.
- `WARN` once each time the probe has failed or printed nothing `--warn-after` times in a row (default 3).
  Silence is never treated as "nothing happened".
- `DONE` when stdout matches `--done`; exit 0.
- `TIMEOUT` after `--timeout` seconds (default 3600); exit 3. If no probe ever succeeded, exit 4 instead.
- `--state NAME` stores a SHA-256 digest of the last seen value (never the value) in `<state>/watch/NAME` (a
  symbolic link there is refused), so a restarted watch does not repeat an event
  already reported. `NAME` is letters, digits, dot, hyphen and underscore only; anything else is exit 64.
- Output is flushed after every line.

### `wm doctor`

Read-only check of the setup: `workplace.toml` parses, each configured path exists (missing optional files are
listed, not failed), the state folder is git-ignored when the root is a git repository, and whether
`.claude/settings.json` registers `wm stop-gate`, `wm brief --hook` and `wm outbound hook` (reported as
`registered: true|false`; absence is a warn, since the hooks are optional and Claude Code only).

### `wm init --name NAME [--lang en|zh-TW] [--dir DIR]`

Copies the template tree for the language into `DIR` (default: current directory). `{{name}}` in file contents and
`__name__` in path segments become `NAME`. Refuses (exit 64) when `DIR/workplace.toml` exists or the language has no
templates. Templates live inside the package at `wm/templates/<lang>/`. It never overwrites: a file that already exists is left
as it is and listed as `kept`, and only missing files are created.

## Claude Code hooks

The hooks are optional and only apply to Claude Code. Everything else works from any agent that can run a shell.

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "wm --root \"$CLAUDE_PROJECT_DIR\" brief --hook", "timeout": 10}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "wm --root \"$CLAUDE_PROJECT_DIR\" stop-gate", "timeout": 20}]}
    ],
    "PreToolUse": [
      {"matcher": "mcp__your_chat__send_message",
       "hooks": [{"type": "command",
                  "command": "wm --root \"$CLAUDE_PROJECT_DIR\" outbound hook --text-field text --to-field recipient",
                  "timeout": 10}]}
    ]
  }
}
```

All three fail open: if `wm` itself breaks, the session continues and the error goes to stderr.
