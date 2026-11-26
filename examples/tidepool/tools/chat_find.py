#!/usr/bin/env python3
"""Stand-in read-back for the example: prints the id of a message if it is in the local outbox.

A real one asks the platform for the message that was just sent (by the id in WM_RESULT) and prints that id only
when the message is really visible in the channel.
"""
import json
import os
import sys
from pathlib import Path


def main() -> int:
    wanted = os.environ.get("WM_RESULT", "").strip()
    outbox = Path(".wm") / "outbox.jsonl"
    if not wanted or not outbox.exists():
        return 2
    for line in outbox.read_text("utf-8").splitlines():
        record = json.loads(line)
        if record["id"] == wanted and (len(sys.argv) < 2 or record["channel"] == sys.argv[1]):
            print(record["id"])
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
