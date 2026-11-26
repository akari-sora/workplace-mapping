# Terminal demo for the README. Regenerate from the repository root with:  vhs docs/media/src/demo.tape
# It runs against a temporary copy of examples/tidepool (an invented company) with a frozen test clock.
Output docs/media/demo.gif
Output docs/media/demo.mp4

Require python3
Set Shell zsh
Set FontFamily "Menlo"
Set FontSize 14
Set Width 1600
Set Height 600
Set Padding 28
Set TypingSpeed 32ms
Set Theme { "name": "wm", "black": "#0b1a1c", "red": "#f2777a", "green": "#5fd1bf", "yellow": "#f2b25c", "blue": "#8ab4f8", "magenta": "#c8a2e0", "cyan": "#5fd1bf", "white": "#dfe9e3", "brightBlack": "#5f7b7e", "brightRed": "#f2777a", "brightGreen": "#5fd1bf", "brightYellow": "#f2b25c", "brightBlue": "#8ab4f8", "brightMagenta": "#c8a2e0", "brightCyan": "#5fd1bf", "brightWhite": "#ffffff", "background": "#0f2326", "foreground": "#dfe9e3", "selection": "#1d3a3e", "cursor": "#f2b25c" }

Hide
Type "export REPO=REPO_PATH && source docs/media/src/demo-setup.sh"
Enter
Sleep 2s
Show

Type "# What can be done right now, what needs you, what is waiting on whom?"
Enter
Sleep 600ms
Type "wm next"
Enter
Sleep 5s

Hide
Type "clear"
Enter
Sleep 400ms
Show
Type "# A draft that claims something is fixed, with no proof and nobody mentioned:"
Enter
Sleep 600ms
Type "cat drafts/blocked.txt"
Enter
Sleep 1500ms
Type "wm action send --kind chat --target release --to Ilse < drafts/blocked.txt"
Enter
Sleep 8s

Hide
Type "clear"
Enter
Sleep 400ms
Show
Type "# Someone corrects the agent: one call records it on that person's card, with its source."
Enter
Sleep 600ms
Type "wm note add 'Start messages with the flag name' --who Okafor --src '#4420' --to card:Okafor"
Enter
Sleep 3500ms
Type "# A kind of message the user pre-approved: checked, sent once, read back."
Enter
Sleep 600ms
Type "echo 'ack, on it' | wm action send --kind chat --target on-call --to Okafor"
Enter
Sleep 3500ms
Type "# End of day: the handoff is written and checked in one call."
Enter
Sleep 600ms
Type "wm wrapup write --start 'REQ-15: create the flag, default off'"
Enter
Sleep 5s
