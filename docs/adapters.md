# Adapters: plugging in any chat tool and any environment

`wm` does not talk to Slack, Teams, a ticket system or a remote desktop. It runs **commands you register**, and
gates the ones that write. An adapter is one of those commands. This page says what shape an adapter has, then gives
notes per chat platform and per kind of environment.

## The shape of an adapter

**A send adapter** (a channel's `send`, or a `[[command]]` with `writes = true`):

- The payload arrives on stdin, exactly as approved. Do not rewrite it, except to turn mentions into the
  platform's markup.
- Context arrives in environment variables: `WM_ACTION_ID`, `WM_KIND`, `WM_TARGET`, `WM_TO`, `WM_PAYLOAD_SHA256`.
- Exit 0 only when the platform accepted the message, and print the platform's own message id or link on stdout.
  `wm` stores it, and the read-back uses it.
- Any failure is a non-zero exit with the reason on stderr. **Never retry inside the adapter.** `wm` runs an action
  once; if the outcome is unclear, a person or a read-back decides, not a loop.
- Read credentials from your password manager or the platform's own session at run time. Nothing secret goes in
  `workplace.toml`, arguments or output.

**A read adapter** (a `[[command]]` with `writes = false`): prints what it read, as JSON where possible, and exits
non-zero when it could not read. An empty result and a failed read must look different.

**A probe** (a system's `probe`): read-only, fast, exits 0 when the system is reachable from this machine.

Register them once:

```toml
[[channel]]
id = "release"
kind = "slack"
send = "tools/chat send --channel release"

[[command]]
id = "chat.history"
what = "last 50 messages of a channel"
run = "tools/chat history"
```

From then on the agent's side is the same at every company:

```sh
wm run chat.history -- release                      # read
wm action send --kind chat --target release --to Ilse --receipt RCPT-7 < draft   # checked, gated, sent once
wm action readback A-20260312-01 --proof "#5001"    # seen in the channel
```

## Chat platforms

Facts below were read from each platform's developer documentation on 26 November 2026. Platforms change; check the
linked page before relying on a detail. "Not found" means the pages we read did not say, not that it is impossible.

| Platform | Send as yourself through an official API | Post-only webhook | Reply to a parent message | Mention markup |
| --- | --- | --- | --- | --- |
| Slack | Yes: a user token; writes are made as the user | Yes | `thread_ts` of the parent | `<@USERID>` |
| Microsoft Teams | Yes: Microsoft Graph with delegated permission, work or school accounts only | Workflows webhook (the older connector webhooks are retired) | Graph `.../messages/{id}/replies` | `<at id="0">Name</at>` plus a `mentions` entry |
| Telegram | Yes: the client API (MTProto, TDLib) with your own `api_id`. The Bot API is bot-only | No dedicated one; a bot's `sendMessage` serves | `reply_parameters`; `message_thread_id` for topics | `@username`, or a `tg://user?id=` link |
| DingTalk | Not found | Yes: custom robot | Not found | `at.atUserIds`, `at.atMobiles` on the robot message |
| Lark / Feishu | Yes: `user_access_token` with the send-as-user scope | Yes: custom bot | Reply API, `reply_in_thread` | `<at user_id="...">Name</at>` |
| WeCom | Not offered: app and robot sends only | Yes: group robot | Not found | `mentioned_list`; `<@userid>` in markdown |
| Built in-house | Ask whoever maintains it | Ask | Ask | Ask |

Sources: Slack [tokens](https://docs.slack.dev/authentication/tokens),
[chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage),
[formatting](https://docs.slack.dev/messaging/formatting-message-text),
[incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks).
Teams [send chatMessage](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0),
[replies](https://learn.microsoft.com/en-us/graph/api/chatmessage-post-replies?view=graph-rest-1.0),
[webhooks](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook).
Telegram [Bot API](https://core.telegram.org/bots/api), [client API](https://core.telegram.org/api),
[API terms](https://core.telegram.org/api/terms).
DingTalk [custom robot](https://help.dingtalk.io/zh/open/development/custom-robots-send-group-messages.md).
Feishu [send message](https://open.feishu.cn/document/server-docs/im-v1/message/create),
[reply](https://open.feishu.cn/document/server-docs/im-v1/message/reply),
[custom bot](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).
WeCom [group robot](https://developer.work.weixin.qq.com/document/path/91770),
[app messages](https://developer.work.weixin.qq.com/document/path/90236).

Length limits the pages state: Slack about 4,000 characters per message before it may be split; Telegram 4,096
characters; WeCom text 2,048 bytes; Teams about 100 KB per post; Feishu text content 150 KB. Set `max_chars` on the
channel to the smaller of the platform's limit and what your colleagues will actually read.

### Choosing how to send

1. **Does the company allow it?** A user token, a delegated app or a client-API session acts as you. Some
   workspaces require an administrator to approve the app, and some companies forbid personal automation outright.
   Find out before building, and record the answer with its source in the team skill's hard rules.
2. **Is a bot or webhook enough?** For notices to a channel, a post-only webhook is the simplest and least
   privileged. It cannot read, and the message comes from the bot, not from you.
3. **Do colleagues need to see it come from you?** Then use the platform's official send-as-user path where one
   exists (see the table). Where none exists, the adapter cannot send as you: have it prepare the text and let the
   user press send.
4. **No interface at all** (an in-house tool, or a client that only runs inside a remote desktop): build read-only
   capture first, since reading is where most of the time goes. For sending, the adapter may place the approved
   text in the input box and stop; the user sends it. That still gets you the check, the card and the ledger.

Whichever path you take, write the adapter so a draft is plain text with `@name` mentions, and convert to the
platform's markup inside the adapter. Rules and drafts then stay the same if the company changes tools.

## Environments

The four kinds from the skill's `references/environments.md`, and what each means for adapters.

**Direct.** Code is on your machine and systems open with a company login. Adapters are ordinary scripts. Give each
system a `probe` anyway: logins expire, and "the command printed nothing" must not be mistaken for "nothing there".

**Network-gated.** Systems sit behind VPNs or a jump host.

- One `[[system]]` per system, with `reach` naming the path (`vpn:office`, `via:jump-host`).
- Point each command at its system. `wm access check` then tells you which paths are down before you start,
  and `wm run` refuses on a machine listed as unable to reach the system.
- When two VPNs cannot be up together, say so in the system's `what`, and make the probes cheap so the agent can
  check after switching.
- Bringing a VPN up usually needs the user (a one-time code, a prompt). Treat it like a login: try once, then hand
  over.

**Enclosed desktop.** Code and tools live in a cloud or remote desktop and may not leave it.

- Decide first where the agent runs. If the company allows an agent inside the desktop, run `wm` there and keep the
  whole project inside. Nothing crosses the boundary and everything on this page works unchanged.
- If the agent runs outside, every adapter is a call through whatever channel the company permits into the desktop
  (a relay, a remote shell). Register those calls as commands; the agent never types into the desktop directly.
- Set `may_leave` on each system and follow it. With `nothing`, read adapters return conclusions the agent
  writes itself after looking (counts, names of files, pass or fail), not file contents, and the project kept
  outside holds structure and decisions only.
- Record who allowed the channel and when, as the system's `src`.
- Sessions drop. A probe for "the desktop session is up and unlocked" saves a great deal of confusion.

**Through other people.** You cannot touch production; someone else carries out the change.

- The write is a ticket or a message, so it is an action of kind `other` or `chat`: preview, approval, sent once.
- The verification is their receipt plus what you can observe yourself. Write both into the state machine's
  "Verified by" cell, and add a `wm watch` on whatever shows the result.
- Record how long their queue normally takes, so a watch has a real timeout and silence becomes a WARN.

## A minimal send adapter

`examples/tidepool/tools/chat_send.py` is a stand-in that writes to a local file. A real one has the same thirty
lines with the platform call in the middle: read stdin, convert mentions, call the platform once, print the message
id, exit non-zero on any error.
