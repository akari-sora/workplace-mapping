# Turning company systems into commands

The faster path at any company is the same: every system the agent touches becomes a registered command, so the
agent stops clicking through screens, stops re-deriving how to reach things, and reads a few lines where a page
would be thousands.

## Register, do not rebuild

Each command is one entry in `workplace.toml`:

```toml
[[command]]
id = "tickets.mine"
what = "my open tickets"
run = "tools/tickets mine"     # anything: a one-line curl, a script, a call through the channel into a remote desktop
writes = false                 # true: only through wm action
system = "tracker"             # optional: where it lives; wm run refuses on a machine not in that system's hosts
max_output = 8000              # characters printed before the rest is saved to a file
readback = ""                  # for writes: prints the result id when the change is visible
```

- `wm catalog` is the index; `wm run <id> [-- args]` runs a read. If someone already built a tool, register it
  as it is.
- **Build the most-used reads first.** What you look at several times a day (my tickets, new messages in a chat,
  build status) saves the most. The second time you do something by hand, register it.
- **Make reads print little.** One line per item, only the fields needed to decide. A command that prints a whole
  page costs that page every time it runs.
- **Read-only by default.** A command that writes or sends is `writes = true` and runs only through `wm action`:
  preview, the user's approval or a standing approval, one run, read-back. If the outcome is unclear, read back
  before anything else; never resend blind.

## What a command must do

- Exit non-zero when it could not read. An empty result and a failed read must look different.
- A send command takes the payload on stdin exactly as approved, sends once, prints the platform's own message id
  on stdout, and never retries inside.
- Take credentials from a password manager or an existing session at run time. Nothing secret goes in
  `workplace.toml`, in arguments, in output or in the conversation. Do not retry a refused login.
- Whatever the company audits, follow it inside the command: commit identity, whether a file may leave the company
  environment. Keep a `status` command that confirms those checks are still on.
- Have offline tests against fake services. On real systems run read-only smoke tests; try writes in a test
  environment or against a target that is your own.

## Waiting

Wait for an outside result with `wm watch --name <what> --probe "<command>" --done <regex>`: one line per change,
WARN when the probe keeps failing, TIMEOUT at the deadline, DONE at the end state. No output does not mean nothing
happened. Do not write an ad-hoc polling loop.

## Other things worth a command

- A credential index: lists every credential's name, where it is stored and which fields it has, never the values.
- A login command per system that has only a web login, so a session can be restored in one step.
- For a service that runs all the time: one machine runs it; before starting it on another, prove the old one has
  stopped (unreachable is not stopped).
