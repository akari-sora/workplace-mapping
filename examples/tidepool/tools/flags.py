#!/usr/bin/env python3
"""Stand-in for a feature-flag service: reads and writes data/flags.json so the example runs offline."""
import json
import sys
from pathlib import Path

STORE = Path("data") / "flags.json"
ENVIRONMENTS = ("staging", "beta", "production")


def load() -> dict:
    return json.loads(STORE.read_text("utf-8"))


def status() -> int:
    print(json.dumps(load(), indent=2, sort_keys=True))
    return 0


def set_flag(line: str) -> int:
    parts = line.split()
    if len(parts) != 3:
        print("payload must be: <flag> <on|off|internal|N%> <environment|all>", file=sys.stderr)
        return 2
    flag, value, where = parts
    targets = ENVIRONMENTS if where == "all" else (where,)
    if any(target not in ENVIRONMENTS for target in targets):
        print(f"unknown environment: {where}", file=sys.stderr)
        return 2
    flags = load()
    entry = flags.setdefault(flag, {})
    for target in targets:
        entry[target] = value
    STORE.write_text(json.dumps(flags, indent=2, sort_keys=True) + "\n", "utf-8")
    print(json.dumps({flag: entry}, sort_keys=True))
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "status":
        return status()
    if len(sys.argv) == 2 and sys.argv[1] == "set":
        return set_flag(sys.stdin.read())
    print("usage: flags.py status | flags.py set < payload", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
