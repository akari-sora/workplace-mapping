"""Read runtime evidence and identify what can be done now."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from .actions import listed_actions
from .config import Config, Problem, problem, result
from .evidence import load_evidence
from .records import filtered_commitments, read_ledger
from .tables import TERMINALS, State, load_tables


def item_status(config: Config, raw: dict[str, Any], row: State | None) -> tuple[str, str | None]:
    state = raw.get("state")
    if isinstance(state, str) and state.strip().casefold() in TERMINALS:
        # A finished item needs no fresh evidence, but a timestamp later than now is never trusted.
        stamp = raw.get("at")
        if isinstance(stamp, str):
            try:
                if config.parse_time(stamp).astimezone(timezone.utc) > config.now().astimezone(timezone.utc):
                    return "unverified", "evidence timestamp is in the future"
            except (ValueError, OverflowError):
                return "unverified", "evidence timestamp is missing or invalid"
        return "done", None
    if row is None:
        return "unverified", "unknown state"
    value = raw.get("at")
    try:
        if not isinstance(value, str):
            raise ValueError
        at = config.parse_time(value)
    except (ValueError, OverflowError):
        return "unverified", "evidence timestamp is missing or invalid"
    age = config.now().astimezone(timezone.utc) - at.astimezone(timezone.utc)
    if age.total_seconds() < 0:
        return "unverified", "evidence timestamp is in the future"
    if age.total_seconds() / 3600 > config.stale_hours:
        return "unverified", "evidence is stale"
    return row.action(config), None


def read_items(config: Config) -> tuple[list[Any], list[Problem]]:
    where = config.where(config.path("evidence"))
    try:
        items = load_evidence(config).get("items", [])
        if not isinstance(items, list):
            raise ValueError
    except (OSError, ValueError):
        return [], [problem("evidence_unverified", "unverified", where, "evidence is missing, unreadable or invalid")]
    return items, []


def next_items(config: Config, show_all: bool = False) -> tuple[int, dict[str, Any]]:
    items, problems = read_items(config)
    ledger = read_ledger(config, "commitment")
    problems.extend(ledger.problems)
    due, _ = filtered_commitments(ledger.records, cutoff=config.today())
    actions, action_problems = listed_actions(config, open_only=True)
    problems.extend(action_problems)
    now = [{**action, "status": "unknown"} for action in actions if action["state"] == "unknown"]
    now.extend(record.output() for record in due if config.ours(record.fields.get("ball", "")))
    rows, _ = load_tables(config) if items else ([], [])
    states: dict[str, State] = {}
    for row in rows:
        if row.flow:
            states.setdefault(row.state, row)
    output = []
    where = config.where(config.path("evidence"))
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, dict):
            problems.append(problem("item_unverified", "unverified", where, f"item {index} must be an object"))
            continue
        state = raw.get("state")
        row = states.get(state) if isinstance(state, str) else None
        status, issue = item_status(config, raw, row)
        if issue:
            label = raw.get("id")
            label = label if isinstance(label, str) and label else index
            problems.append(problem("item_unverified", "unverified", where, f"item {label}: {issue}"))
        if status == "done" and not show_all:
            continue
        item = {key: raw[key] for key in ("id", "title", "state", "evidence", "at") if key in raw}
        if row is not None:
            item.update(row.output())
        if status == "waiting" and row is not None:
            item["waiting_on"] = row.trigger
        item["status"] = status
        output.append(item)

    for status in ("actionable", "needs_user"):
        now.extend(item for item in output if item["status"] == status)
    return result(problems, items=output, now=now)
