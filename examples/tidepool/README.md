# Example: Tidepool

Tidepool is an invented company that makes a tide-chart app. Every person, chat, ticket and message id here is made
up. The folder shows what a work project looks like after about two weeks of using workplace-mapping.

The example is frozen at 18:30 on 12 March 2026 (Lisbon time), so the walkthrough sets a test clock to get the same output:

```sh
cd examples/tidepool
export WM_TEST_CLOCK=1           # lets --now replace the clock; never set this in a real project
NOW=2026-03-12T18:30:00+00:00

wm --now $NOW brief              # what a new session sees first
wm --now $NOW next               # REQ-15 can be done now, REQ-9 needs the user, REQ-12 is waiting on on-call
wm --now $NOW lint                        # exit 0
wm --now $NOW wrapup check                # PASS

# A draft that claims completion with no receipt and no @-mention: exit 2, two violations
wm outbound check --to Ilse --channel release --file drafts/blocked.txt

# The same news with a verified receipt and the requester mentioned: exit 0
wm outbound check --to Ilse --channel release --receipt RCPT-7 --file drafts/ok.txt
```

Everything the agent can do at Tidepool is registered in `workplace.toml`, so the same verbs work for reads,
writes and messages. The chat and flag tools here are stand-ins that write to local files, so this runs offline:

```sh
wm catalog                       # 2 commands, 3 channels, 2 systems
wm run flags.status                       # a read: runs at once

# A message of a kind the user pre-approved ("acknowledgements in on-call"): checked, sent, logged in one call
echo "ack, on it" | wm action send --kind chat --target on-call --to Okafor

# A write with no standing approval: stops as a draft and says needs_user, with the exact payload
echo "moon_strip off all" | wm action send --kind command --target flags.set
wm action approve A-<id> --by "yes, create it"
wm action run A-<id>
wm run flags.status                       # read it back: moon_strip is off in all three environments
wm action readback A-<id> --proof "flags.status at 18:40"
```

Where to look:

- `workplace.toml`: the systems, chats and commands, and how each is reached.

- `.agents/skills/tidepool-team/references/state-machines.md`: two processes as tables.
- `.agents/skills/tidepool-team/references/people-cards.md`: the "Before drafting" list and three cards.
- `worklog/inbox.md`: five notes, each filed into the file that now carries its id.
- `rules.toml`: two rules that started as corrections and were promoted after the second time, and one standing approval.
- `tools/`: the two stand-in adapters. A real adapter has the same shape (see `docs/adapters.md`).
