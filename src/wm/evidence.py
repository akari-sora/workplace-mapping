"""Read and atomically update tracked items and receipts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .catalog import command_line, read_command
from .config import Config, UsageError, problem, reject_json_constant, result, validate_written_value
from .files import ledger_lock, write_text
from .process import shell
from .secrets import has_secret_like, redact_output
from .tables import TERMINALS, load_tables


def load_evidence(config: Config) -> dict[str, Any]:
    with config.path("evidence").open(encoding="utf-8") as handle:
        evidence = json.load(handle, parse_constant=reject_json_constant)
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    return evidence


def attached_receipts(config: Config, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    try:
        receipts = load_evidence(config).get("receipts", [])
    except (OSError, ValueError):
        return []
    if not isinstance(receipts, list):
        return []
    return [receipt for receipt in receipts if isinstance(receipt, dict)
            and receipt.get("verified") is True and receipt.get("id") in ids]


def evidence_entries(config: Config, key: str, *, create: bool = False) -> tuple[dict, list, list]:
    path = config.path("evidence")
    try:
        try:
            data = load_evidence(config)
        except FileNotFoundError:
            if not create or path.is_symlink():
                raise
            data = {}
        entries = data.get(key, [])
        if not isinstance(entries, list):
            raise ValueError
        return data, entries, []
    except (OSError, ValueError):
        return {}, [], [problem("evidence_unverified", "unverified", config.where(path),
                                 "evidence is missing, unreadable or invalid")]


def save_evidence(config: Config, data: dict, key: str, entries: list) -> None:
    data[key] = entries
    write_text(config.path("evidence"), json.dumps(data, ensure_ascii=True, indent=2) + "\n")


def list_items(config: Config) -> tuple[int, dict]:
    _, items, problems = evidence_entries(config, "items")
    return result(problems, items=items)


def set_item(config: Config, identity: str, state: str | None, title: str | None,
             evidence: str | None, force: bool) -> tuple[int, dict]:
    validate_written_value(identity, "item id", nonempty=True)
    for field, value in (("state", state), ("title", title), ("evidence", evidence)):
        if value is not None:
            validate_written_value(value, field, nonempty=True)
            if has_secret_like(value):
                return result([problem("secret_like", "fail", config.where(config.path("evidence")),
                                       f"{field} contains a possible credential")])
    path = config.path("evidence")
    where = config.where(path)
    if state is not None and not force and state.strip().casefold() not in TERMINALS:
        rows, _ = load_tables(config)
        if not any(row.flow and row.state == state for row in rows):
            return result([problem("unknown_state", "fail", where, f"{state} is not in the state machines")])
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    with ledger_lock(path):
        data, items, problems = evidence_entries(config, "items", create=True)
        if problems:
            return result(problems)
        matches = [item for item in items if isinstance(item, dict) and item.get("id") == identity]
        if len(matches) > 1:
            return result([problem("duplicate_id", "fail", where, f"multiple items with id {identity}")])
        if not matches:
            if state is None:
                return result([problem("missing_state", "fail", where, "a new item needs --state")])
            item = {"id": identity}
            items.append(item)
        else:
            item = matches[0]
        item.update((key, value) for key, value in (("title", title), ("state", state), ("evidence", evidence))
                    if value is not None)
        if state is not None or evidence is not None:
            item["at"] = config.now().isoformat()
        save_evidence(config, data, "items", items)
    return result(item=item)


def add_receipt(config: Config, what: str, item: str | None = None, by_cmd: str | None = None,
                expect: re.Pattern[str] | None = None, args: list[str] | None = None) -> tuple[int, dict]:
    path = config.path("evidence")
    if not what.strip():
        raise UsageError("receipt needs nonempty text")
    validate_written_value(what, "receipt", nonempty=True)
    if has_secret_like(what):
        return result([problem("secret_like", "fail", config.where(path),
                               "receipt contains a possible credential")])
    if item is not None:
        validate_written_value(item, "item id", nonempty=True)
    if by_cmd is not None:
        validate_written_value(by_cmd, "command id", nonempty=True)
    for argument in args or []:
        validate_written_value(argument, "command argument")
    details = {}
    if by_cmd is not None:
        if expect is None:
            raise UsageError("--by-cmd requires --expect")
        try:
            entry, issues = read_command(config, by_cmd)
            execution = None if issues else shell(config.root, command_line(entry, args or []), entry["timeout"],
                                                   capture=True, payload=b"")
        except (OSError, UsageError):
            execution = None
        match = (expect.search(execution.stdout.decode("utf-8", "replace"))
                 if execution is not None and execution.exit == 0 else None)
        if match is None or not match[0]:
            return result([problem("check_failed", "fail", config.where(path),
                                   "read command did not succeed and match --expect")])
        matched = match[0]
        match_sha256 = hashlib.sha256(matched.encode("utf-8")).hexdigest()
        stdout_sha256 = hashlib.sha256(execution.stdout).hexdigest()
        # Redact first, cut afterwards: cutting first can split a secret so that the part kept no longer looks like
        # one. A match that lies inside a secret found in the whole output is not kept at all.
        whole = execution.stdout.decode("utf-8", "replace")
        kept = redact_output(config, matched)
        if not isinstance(kept, str) or matched not in redact_output(config, whole):
            kept = "[redacted]"
        details = {"command": by_cmd, "args": [redact_output(config, value) for value in args or []],
                   "match": kept[:200],
                   "match_sha256": match_sha256, "stdout_sha256": stdout_sha256,
                   "sha256": stdout_sha256}

    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    with ledger_lock(path):
        data, receipts, problems = evidence_entries(config, "receipts", create=True)
        if problems:
            return result(problems)
        prefix = config.today().strftime("R-%Y%m%d-")
        ids = {receipt["id"] for receipt in receipts if isinstance(receipt, dict)
               and isinstance(receipt.get("id"), str)}
        identity = next((f"{prefix}{number:02d}" for number in range(1, 100)
                         if f"{prefix}{number:02d}" not in ids), None)
        if identity is None:
            return result([problem("id_exhausted", "fail", config.where(path), "no free receipt id for today")])
        receipt = {"id": identity, "what": what, "at": config.now().isoformat(),
                   "verified": True, "how": "checked" if by_cmd is not None else "claimed", **details}
        if item is not None:
            receipt["item"] = item
        receipts.append(receipt)
        save_evidence(config, data, "receipts", receipts)
    return result(receipt=receipt)
