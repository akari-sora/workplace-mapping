"""Load, lint, and match standing approvals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import Config, Problem, problem

KINDS = ("chat", "command", "other")
STANDING_KINDS = ("chat", "command")


@dataclass(frozen=True)
class Standing:
    id: str
    kind: str
    targets: list[str]
    recipients: set[str]
    pattern: re.Pattern[str] | None
    max_chars: int | None
    expires: date | None

    def matches(self, kind: str, target: str, recipient: str, payload: str, today: date) -> bool:
        return (self.kind == kind and target in self.targets
                and (not self.recipients or recipient.casefold() in self.recipients)
                and (self.pattern is None or self.pattern.search(payload) is not None)
                and (self.max_chars is None or len(payload) <= self.max_chars)
                and (self.expires is None or today <= self.expires))


def parse_rule(raw: Any) -> Standing:
    if not isinstance(raw, dict) or any(
        not isinstance(raw.get(key), str) or not raw[key].strip() for key in ("id", "kind", "src")
    ) or re.fullmatch(r"[A-Za-z0-9._-]+", raw["id"]) is None or raw["kind"] not in STANDING_KINDS:
        raise ValueError("standing approval needs an id, a valid kind and a source")

    targets, recipients = raw.get("targets"), raw.get("to", [])
    for key, values in (("targets", targets), ("to", recipients)):
        if (not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values)
                or (key == "targets" and not values)):
            raise ValueError(f"standing approval has invalid {key}")
    maximum = raw.get("max_chars")
    if maximum is not None and (type(maximum) is not int or maximum < 0):
        raise ValueError("standing approval has invalid max_chars")
    pattern = raw.get("match")
    if pattern is not None and not isinstance(pattern, str):
        raise ValueError("standing approval match must be a string")
    expiry = raw.get("expires")
    expires = None
    if expiry is not None:
        if not isinstance(expiry, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry):
            raise ValueError("standing approval expiry must be a YYYY-MM-DD date")
        try:
            expires = date.fromisoformat(expiry)
        except ValueError as exc:
            raise ValueError("standing approval expiry must be a valid date") from exc
    return Standing(raw["id"], raw["kind"], targets, {name.casefold() for name in recipients},
                    re.compile(pattern) if pattern is not None else None, maximum, expires)


def load_standing(config: Config, data: dict[str, Any], text: str) -> tuple[list[Standing], list[Problem]]:
    path = config.path("rules")
    raw_rules = data.get("standing", [])
    if not isinstance(raw_rules, list):
        return [], [problem("bad_rule", "fail", config.where(path), "standing must be an array of tables")]
    lines = [number for number, line in enumerate(text.splitlines(), 1)
             if re.match(r"\s*\[\[\s*standing\s*\]\]", line)]
    today = config.today()
    approvals, problems, seen = [], [], set()
    for index, raw in enumerate(raw_rules):
        where = config.where(path, lines[index] if index < len(lines) else None)
        try:
            rule = parse_rule(raw)
        except (re.error, OverflowError, RecursionError):
            problems.append(problem("bad_regex", "fail", where, "invalid standing match pattern"))
            continue
        except ValueError as exc:
            problems.append(problem("bad_rule", "fail", where, str(exc)))
            continue
        if rule.id in seen:
            problems.append(problem("duplicate_rule", "fail", where, "duplicate standing id"))
            continue
        seen.add(rule.id)
        approvals.append(rule)
        if rule.expires is not None and rule.expires < today:
            problems.append(problem("standing_expired", "warn", where, "standing approval is past its last day"))
    return approvals, problems
