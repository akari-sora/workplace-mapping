"""Validate outbound rules and check drafts against them."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from typing import Any

from .catalog import DEFAULT_MENTION, Entry, load_registry, public_entry
from .config import Config, Problem, problem, result
from .evidence import attached_receipts
from .markdown import Section, section, sections, visible_lines
from .process import shell
from .secrets import redact_output


REGEX_FIELDS = ("when", "require", "forbid")
CHECK_TIMEOUT = 10


@dataclass(frozen=True)
class Rule:
    id: str
    source: str
    message: str
    kinds: list[str] | None
    channels: list[str] | None
    recipients: list[str] | None
    regex: dict[str, re.Pattern[str]]
    require_receipt: bool | str
    require_mention: bool
    max_chars: int | None
    check: str | None
    where: str

    def applies(self, draft: str, recipient: str, channel: str | None, kind: str) -> bool:
        return (
            (self.kinds is None or kind in self.kinds)
            and (self.channels is None or channel in self.channels)
            and (self.recipients is None or recipient.casefold() in self.recipients)
            and ("when" not in self.regex or self.regex["when"].search(draft) is not None)
        )


def valid_rule_fields(raw: Any) -> bool:
    if (not isinstance(raw, dict) or not isinstance(raw.get("id"), str)
            or re.fullmatch(r"[A-Za-z0-9._-]+", raw["id"]) is None):
        return False
    if any(not isinstance(raw[key], str) for key in (*REGEX_FIELDS, "message", "check") if key in raw):
        return False
    for key in ("kinds", "channels", "to"):
        if key in raw and (not isinstance(raw[key], list) or not all(isinstance(item, str) for item in raw[key])):
            return False
    if "kinds" in raw and any(kind not in ("chat", "command", "other") for kind in raw["kinds"]):
        return False
    receipt = raw.get("require_receipt", False)
    if type(receipt) is not bool and receipt not in ("claimed", "checked"):
        return False
    if "require_mention" in raw and type(raw["require_mention"]) is not bool:
        return False
    if "check" in raw and (not raw["check"].strip() or "\0" in raw["check"]):
        return False
    return "max_chars" not in raw or (type(raw["max_chars"]) is int and raw["max_chars"] >= 0)


def compile_rule(raw: dict[str, Any], where: str) -> tuple[Rule, list[Problem]]:
    problems = []
    source = raw.get("src")
    if not isinstance(source, str) or not source.strip():
        problems.append(problem("rule_without_src", "fail", where, "rule needs a source"))
        source = ""
    regex = {}
    for key in REGEX_FIELDS:
        if key in raw:
            try:
                regex[key] = re.compile(raw[key])
            except (re.error, OverflowError, RecursionError):
                problems.append(problem("bad_regex", "fail", where, f"invalid {key} pattern"))
    if not (raw.get("require_receipt") or raw.get("require_mention") or raw.get("check")
            or any(key in raw for key in ("require", "forbid", "max_chars"))):
        problems.append(problem("rule_without_check", "fail", where, "rule needs at least one check"))
    recipients = [recipient.casefold() for recipient in raw["to"]] if "to" in raw else None
    return Rule(raw["id"], source, raw.get("message", f"Draft broke rule {raw['id']}."),
                raw.get("kinds"), raw.get("channels"), recipients, regex, raw.get("require_receipt", False),
                raw.get("require_mention", False), raw.get("max_chars"), raw.get("check"), where), problems


def read_rules(config: Config) -> tuple[dict[str, Any], str, list[Problem]]:
    path = config.path("rules")
    where = config.where(path)
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except FileNotFoundError:
        if not path.is_symlink():
            return {}, "", []
        return {}, "", [problem("unreadable", "unverified", where, "rules file cannot be read")]
    except (OSError, ValueError):
        return {}, "", [problem("unreadable", "unverified", where, "rules file cannot be read or parsed")]
    return data, text, []


def load_rules(config: Config, data: dict[str, Any], text: str) -> tuple[list[Rule], list[Problem]]:
    path = config.path("rules")
    where = config.where(path)
    raw_rules = data.get("outbound", [])
    if not isinstance(raw_rules, list):
        return [], [problem("bad_rule", "fail", where, "outbound rules must be an array of tables")]

    rules, problems = [], []
    seen: set[str] = set()
    lines = [number for number, line in enumerate(text.splitlines(), 1)
             if re.match(r"\s*\[\[\s*outbound\s*\]\]", line)]
    for index, raw in enumerate(raw_rules):
        location = config.where(path, lines[index]) if index < len(lines) else where
        if not valid_rule_fields(raw):
            problems.append(problem("bad_rule", "fail", location, "rule has an invalid id or field type"))
            continue
        rule, issues = compile_rule(raw, location)
        if rule.id in seen:
            issues.append(problem("duplicate_rule", "fail", location, "duplicate rule id"))
        seen.add(rule.id)
        problems.extend(issues)
        if not issues:
            rules.append(rule)
    return rules, problems


def aliases(person: Section) -> list[str]:
    for _, line in visible_lines(person.body.splitlines()):
        match = re.match(r"^\s*(?:Aliases:|別名：)\s*(.*)$", line, re.IGNORECASE)
        if match:
            return [name.strip().casefold() for name in re.split(r"[,、，]", match[1]) if name.strip()]
    return []


def person_card(config: Config, recipient: str) -> dict[str, str | None]:
    card: dict[str, str | None] = {"before_drafting": None, "person": None}
    path = config.people_cards()
    if path is None:
        return card
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return card
    before = section(text, "Before drafting", "擬稿前")
    if before is not None:
        card["before_drafting"] = before.body
    name = recipient.casefold()
    if name:
        people = [person for person in sections(text) if person.level == 2]
        person = next((part for part in people if part.title.casefold() == name), None)
        if person is None:
            person = next((part for part in people if name in aliases(part)), None)
        if person is not None:
            card["person"] = person.full
    return card


def show_card(config: Config, recipient: str) -> tuple[int, dict[str, Any]]:
    card = person_card(config, recipient)
    issues = []
    if card["person"] is None:
        path = config.people_cards() or config.path("skills")
        issues.append(problem("no_card", "fail", config.where(path), "no people card for recipient"))
    return result(issues, card=card)


def failed_checks(rule: Rule, draft: str, has_receipt: bool, mention: re.Pattern[str]) -> list[str]:
    checks = []
    if rule.require_receipt and not has_receipt:
        checks.append("require_receipt")
    if "require" in rule.regex and not rule.regex["require"].search(draft):
        checks.append("require")
    if rule.require_mention and not mention.search(draft):
        checks.append("require_mention")
    if "forbid" in rule.regex and rule.regex["forbid"].search(draft):
        checks.append("forbid")
    if rule.max_chars is not None and len(draft) > rule.max_chars:
        checks.append("max_chars")
    return checks


def command_check(config: Config, rule: Rule, command: str, draft: str, recipient: str,
                  channel: str | None) -> tuple[str | None, Problem | None]:
    env = dict(os.environ, WM_TO=recipient, WM_CHANNEL=channel or "")
    try:
        execution = shell(config.root, command, CHECK_TIMEOUT, env=env, capture=True,
                          payload=draft.encode("utf-8", "surrogateescape"))
    except (OSError, ValueError):
        execution = None
    could_not_start = (execution is not None and execution.exit in (126, 127)
                       and re.search(rb"not found|No such file or directory|Permission denied|cannot execute|Exec format error",
                                     execution.stderr))
    if execution is None or execution.exit is None or could_not_start:
        return None, problem("check_unverified", "unverified", rule.where,
                             f"rule {rule.id} check timed out or could not run")
    if execution.exit == 0:
        return None, None
    first, _, _ = execution.stdout.decode("utf-8", "replace").partition("\n")
    # Validators can accidentally echo the draft or their own command line.
    detail = redact_output(config, first.rstrip("\r"), private=draft.splitlines())
    return detail, None


def check_outbound(config: Config, draft: str, recipient: str, channel: str | None,
                   receipts: list[str], *, channels: dict[str, Entry] | None = None,
                   rules_file: tuple[dict[str, Any], str, list[Problem]] | None = None,
                   verified: str | None = None, kind: str = "chat") -> tuple[int, dict[str, Any]]:
    problems = []
    if channels is None:
        registry, issues = load_registry(config)
        channels = registry["channel"]
        problems.extend(issues)
    data, text, issues = rules_file if rules_file is not None else read_rules(config)
    rules, rule_problems = load_rules(config, data, text)
    problems.extend(issues + rule_problems)
    registered = channels.get(channel) if kind == "chat" and channel is not None else None
    mention = registered["mention"] if registered else re.compile(DEFAULT_MENTION)
    matching = [rule for rule in rules if rule.applies(draft, recipient, channel, kind)]
    needs_proof = any(rule.require_receipt for rule in matching)
    attached = attached_receipts(config, receipts) if needs_proof else []
    has_checked = any(receipt.get("how") == "checked" for receipt in attached)
    fields = {}
    if needs_proof:
        fields.update(receipt_required=True, receipt_checked=has_checked)
    if any(rule.require_receipt == "checked" for rule in matching):
        fields["receipt_level"] = "checked"
    violations = []
    for rule in matching:
        has_receipt = has_checked if rule.require_receipt == "checked" else bool(attached) or verified is not None
        base = {"message": rule.message, "src": rule.source}
        violations.extend({"rule": rule.id, "check": check, **base}
                          for check in failed_checks(rule, draft, has_receipt, mention))
        if rule.check is not None:
            detail, issue = command_check(config, rule, rule.check, draft, recipient, channel)
            if issue:
                problems.append(issue)
            if detail is not None:
                violations.append({"rule": rule.id, "check": "check", **base, "detail": detail})
    if registered:
        public = public_entry("channel", registered)
        del public["send"]
        fields["channel"] = public
        if registered["max_chars"] and len(draft) > registered["max_chars"]:
            violations.append({"rule": f"channel:{registered['id']}", "check": "max_chars",
                               "message": "Draft exceeds the channel's character limit.", "src": registered["src"]})
    elif kind == "chat" and channels and channel is not None:
        problems.append(problem("unknown_channel", "warn", "workplace.toml", "channel is not registered"))
    card = person_card(config, recipient)
    if recipient and card["person"] is None:
        path = config.people_cards() or config.path("skills")
        problems.append(problem("no_card", "warn", config.where(path), "no people card for recipient"))
    if violations:
        problems.append(problem("outbound_violation", "fail", config.where(config.path("rules")),
                                f"{len(violations)} outbound checks failed"))
    return result(problems, violations=violations, card=card, **fields)
