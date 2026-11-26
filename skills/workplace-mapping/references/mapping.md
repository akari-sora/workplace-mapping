# Mapping a new company or team

Most of a new company's rules are not written anywhere. They are spread across group chats, direct messages,
meetings, other people's commit history, and the moment someone corrects you. An agent starts every session from
zero, so anything not recorded gets asked again and broken again. Two layers are kept apart: the **method** (this
skill and `wm`) holds no company's data; the **content** lives in that company's project.

## Day one

1. In the work project's root run `wm init --name <company-id> --lang en`. It creates the layout below, blank
   templates and `workplace.toml`. Add `.wm/` to `.gitignore`.
2. Fill in what you already know: who assigns work, which chat it arrives in, how the first task goes from start
   to finished. Put what you do not know under "Open questions".
3. Register the systems, chats and commands you already have in `workplace.toml` (`references/environments.md`,
   `references/commands.md`).
4. Add one paragraph to the project's entry file (`AGENTS.md` or `CLAUDE.md`): "This is a workplace-mapping project.
   Use the `wm` command for everything in it; start with `wm brief`."

## The seven things to find out

| Category | Questions to answer | Record it with |
| --- | --- | --- |
| People and roles | Who owns what, who decides, who grants access; what each person is called, which language they use, what shape of message they want | `wm card add`, `wm note add --to card:<name>` |
| Communication rules | Which topic goes in which chat, length and tone, what needs approval before sending, when to @ whom | `wm note add --to drafting`; machine-checkable ones with `wm rule add` |
| Systems and access | Each system's entry point, how to log in, which machine can reach it, what the company audits | `[[system]]` in `workplace.toml` |
| Processes | Which states a piece of work passes through, who moves it, how you prove it reached the next one | A table in `state-machines.md` |
| Hard rules and soft habits | What causes harm or a reprimand when broken (hard); what just goes smoother (soft) | The "Hard rules" section at the top of each skill |
| Rhythm | Working hours, recurring meetings, report deadlines, release windows, who is away when | The daily skill |
| Open questions | Unknown, inferred but unconfirmed, or two sources that disagree | "Open questions" at the end of each file |

## How the rules grow by themselves

After a correction, change the harness; do not rely on memory. The same matter climbs three levels:

1. **First correction**: a written rule on a card or in a skill, with its source. One call:
   `wm note add "<rule>" --who <who> --src <where> --to card:<name>`.
2. **Corrected again for the same kind of thing**: the written rule did not hold. Promote it so a command does the
   stopping: `wm rule add --id <id> --from <note id> --message "<what to do>" --when <regex> --require <regex>`.
   A rule can require a mention, a receipt, a pattern, a length, or run any check command (`--check`).
3. **The rule blocks wrongly or is out of date**: change it in place in `rules.toml`, with the date and the source
   of the change. Do not work around it.

The same goes for approvals. When the user says a kind of action no longer needs their yes, record it:
`wm standing add --id <id> --kind chat --targets <channel> --match <regex> --src "user, <date>: <their words>"`.

Weekly, or at the end of a day: merge duplicate rules, move stray facts into a file that gets read, add missing
sources, clear open questions that now have answers. `wm lint` shows what is inconsistent.

## Layout of one company's project

```
<project>/
  workplace.toml                  name, timezone, systems, chats, commands
  rules.toml                      checks on outbound messages; standing approvals
  data/evidence.json              where each item stands; verification receipts   (written by wm item, wm receipt)
  worklog/
    YYYY-MM-DD.md                 the day's log and handoff                        (wm log, wm wrapup write)
    commitments.md                promises                                         (wm commitments)
    inbox.md                      every note taken, with where it was filed        (wm note)
  .agents/skills/
    <company>-team/               people and communication
      SKILL.md                    hard rules, what each chat is for, who to ask about what
      references/
        people-cards.md           "Before drafting" list, then one card per person
        lead-notices.md           what the lead asked of us, newest first
        project-<name>.md         one file per project in progress
        state-machines.md         every process as a table
    <company>-dev-release/        repositories and owners, branches, versions, build, release
    <company>-daily/              recurring meetings, reports, leave and absence
```

- People card fields: what to call them, account, role, where they read messages, language, the message shape they
  want, how they usually reply, what not to do, what we said to them before, corrections learned.
- Values specific to the project (accounts, ids, URLs, chat names) stay in the company project and never go into a
  cross-company skill.
- Three places to write, by kind: rules and how to operate go in skills and `rules.toml`; facts of the moment,
  to-dos and promises go in the ledgers; lessons that hold at any company go in your own cross-project memory.
- For a handbook or standard the company issues, make an index skill: what to look up in which chapter, a
  self-check of each red line, and a list of where we do not comply yet (listing is not an exemption).
