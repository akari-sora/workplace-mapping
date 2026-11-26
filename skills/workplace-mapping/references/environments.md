# Environments and chat tools

Every company differs in where things live and which chat it uses. Work out which kind you are in on day one and
write it into `workplace.toml`. Do not carry assumptions over from the last company.

## Four kinds of environment

They can coexist: one system may be direct and another behind a VPN.

| Kind | What it looks like | How the agent works | What to record |
| --- | --- | --- | --- |
| Direct | Code is cloned on your own machine; systems open with a company login | Work in the repository | Which data may not stay on the machine |
| Network-gated | Systems sit behind one or several VPNs or a jump host | Record the path for each system; probe that it is reachable before starting | Which VPN serves which system, whether two can be up at once, how long a login lasts, any limits on exit region |
| Enclosed desktop | Code and tools live inside a cloud or remote desktop and nothing may be taken out | The agent runs inside the desktop, or works from outside through a command channel the company allows | What may cross the boundary (if not stated, assume nothing), who allowed the channel, how to reconnect after a drop |
| Through other people | You cannot touch production; changes go in a ticket that someone else carries out | Produce the ticket and the script; verify from their receipt plus what you can see yourself | Ticket format, who executes, what a receipt looks like, how long before silence counts as a timeout |

One `[[system]]` entry per system:

```toml
[[system]]
id = "tracker"
what = "ticket tracker"
reach = "vpn:office"           # direct | vpn:<name> | desktop:<name> | via:<host> | person:<who>
hosts = []                     # which of your machines can reach it; empty = any
login = "single sign-on plus a one-time code"    # how, never the credentials
probe = "curl -fsS -m 5 -o /dev/null https://tracker.example/health"
may_leave = "notes"            # nothing | notes | code | anything
src = "who told you, when"
```

- `wm access check` runs the probes. A system that cannot be reached is `unreachable` and the result is
  `UNVERIFIED`: not reaching a system is never "no data".
- With several machines of your own, set `resident_host` to the one that holds the logged-in sessions and runs
  long-lived services. Other machines work through it and do not keep logins of their own.
- Blocked by a login page, a lock screen or an expired session: handle it automatically once. If that fails, hand
  it to the user; do not retry.
- In an enclosed-desktop project, what is kept outside records structure and conclusions only, never code or
  internal data. Follow each system's `may_leave`.
- Through other people: the write is an action of kind `other` (`wm action send --kind other --target "<queue or
  person>" < ticket`). It always goes to the user for approval. If a command files the ticket, give it after `--` so
  the user sees it with the text; if a person files it, settle the action afterwards with `wm action resolve <id>
  --state sent --proof <their receipt>`. Put a `wm watch` on whatever shows the result.

## Chat tools

The chat may be Slack, Teams, Telegram, DingTalk, Lark, WeCom, or something the company built. The method is tied to
none of them. One `[[channel]]` entry per chat:

```toml
[[channel]]
id = "release"
kind = "slack"                 # informational: slack | teams | telegram | dingtalk | lark | wecom | custom
what = "what went live and what is about to"
mention = "@\\S+"              # what counts as a mention in a draft
threads = true
max_chars = 4000
send = "tools/chat send --channel release"      # the message arrives on stdin
readback = "tools/chat find --channel release"  # must print the sent message's id for the send to count as verified
src = "who told you, when"
```

- Drafts are plain text with `@name`. The send command turns that into the platform's own markup, so rules and
  drafts stay the same if the company changes tools.
- `wm action send --kind chat --target <channel id>` checks the draft against the rules and this entry, then follows the
  approval path. `wm` has no connector of its own; it only runs `send`.
- For an in-house tool with no public interface, build read-only capture first, since reading is where most of the
  time goes. Sending is still preview, approval, once, read back; if a read-back is not possible, the send
  command may place the approved text in the input box and stop, and the user presses send.
- Before automating a personal account, confirm that both the company and the platform allow it, and record the
  answer under the team skill's hard rules.

Platform details (what each official API offers, mention markup, length limits) are in `docs/adapters.md` of the
workplace-mapping repository.
