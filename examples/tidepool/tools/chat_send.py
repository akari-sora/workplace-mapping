#!/usr/bin/env python3
"""Stand-in chat adapter for the example: appends the message to a local outbox instead of sending it.

A real adapter has the same shape: the message arrives on stdin, the channel is an argument, and it exits 0 only
when the platform accepted the message, printing the platform's message id. It does not retry.
"""
import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: chat_send.py CHANNEL < message", file=sys.stderr)
        return 64
    text = sys.stdin.read().strip()
    if not text:
        print("empty message", file=sys.stderr)
        return 2
    outbox = Path(".wm") / "outbox.jsonl"
    outbox.parent.mkdir(exist_ok=True)
    count = len(outbox.read_text("utf-8").splitlines()) if outbox.exists() else 0
    message_id = f"#{5000 + count}"
    record = {"id": message_id, "channel": sys.argv[1], "to": os.environ.get("WM_TO", ""),
              "action": os.environ.get("WM_ACTION_ID", ""), "text": text}
    with outbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(message_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
