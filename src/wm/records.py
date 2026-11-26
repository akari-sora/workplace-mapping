"""Parse, update, and lint the Markdown ledgers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from .config import Config, Problem, problem, result, validate_written_value


Kind = Literal["note", "commitment"]
KINDS = ("correction", "instruction", "pitfall", "decision", "person", "process")
PREFIX = {"note": "N", "commitment": "C"}
LEDGER = {"note": "notes", "commitment": "commitments"}
CANDIDATE = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+(?:\[[^\]]*\][ \t]+)?\[(note|commitment)\b")
RECORD = re.compile(r"^[ \t]*- \[([ x])\] \[(note|commitment)(?=\s|\])")
FIELD = re.compile(r'\s+([A-Za-z_][\w-]*)=("(?:\\.|[^"\\])*"|[^\s\]"]+)(?=\s|\])')
ID = re.compile(r"([NC])-(\d{4})(\d{2})(\d{2})-([0-9]{2})", re.ASCII)
DUE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Record:
    kind: Kind
    checked: bool
    fields: dict[str, str]
    text: str
    day: date
    line: int

    @property
    def id(self) -> str:
        return self.fields["id"]

    def output(self) -> dict[str, Any]:
        return {**self.fields, "type": self.kind, "checked": self.checked, "text": self.text}

    def due(self) -> date | None:
        value = self.fields.get("due", "")
        return parse_due(value) if value != "none" else None


def parse_due(value: str) -> date:
    if not DUE.fullmatch(value):
        raise ValueError("expected YYYY-MM-DD")
    return date.fromisoformat(value)


def parse_record(line: str, line_number: int) -> Record | None:
    if not CANDIDATE.match(line):
        return None
    match = RECORD.match(line)
    if not match:
        raise ValueError("expected a Markdown checkbox followed by a record")
    checked, kind = match.groups()
    fields: dict[str, str] = {}
    index = match.end()
    while field := FIELD.match(line, index):
        key, value = field.groups()
        if key in fields:
            raise ValueError("record repeats a key")
        fields[key] = json.loads(value) if value.startswith('"') else value
        index = field.end()
    tail = re.fullmatch(r"[ \t]*\][ \t]+(.*\S)[ \t]*", line[index:])
    if not tail:
        raise ValueError("expected key=value fields and record text")
    identity = ID.fullmatch(fields.get("id", ""))
    if not identity or identity[1] != PREFIX[kind]:
        raise ValueError("invalid record id")
    day = date(int(identity[2]), int(identity[3]), int(identity[4]))
    return Record(kind, checked == "x", fields, tail[1].strip(), day, line_number)


@dataclass
class Ledger:
    path: Path
    lines: list[str]
    records: list[Record]
    problems: list[Problem]


def read_ledger(config: Config, kind: Kind) -> Ledger:
    path = config.path(LEDGER[kind])
    ledger = Ledger(path, [], [], [])
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            ledger.lines = handle.readlines()
    except FileNotFoundError:
        if path.is_symlink():
            ledger.problems.append(problem("unreadable", "unverified", config.where(path), "ledger cannot be read"))
    except (OSError, UnicodeError):
        ledger.problems.append(problem("unreadable", "unverified", config.where(path), "ledger cannot be read"))

    for number, line in enumerate(ledger.lines, 1):
        try:
            record = parse_record(line.rstrip("\r\n"), number)
            if record and record.kind == kind:
                ledger.records.append(record)
        except ValueError:
            ledger.problems.append(problem("bad_record", "fail", config.where(path, number), "invalid record line"))
    return ledger


def filing_problem(config: Config, identity: str, target: str, where: str) -> Problem | None:
    try:
        if not target or Path(target).is_absolute():
            raise ValueError
        contents = config.relative(target).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return problem("filed_target_missing", "fail", where, "filed target is missing or unreadable")
    if not re.search(rf"(?<![\w-]){re.escape(identity)}(?![\w-])", contents):
        return problem("filed_id_not_found", "fail", where, f"filed target does not cite {identity}")
    return None


def lint_records(config: Config) -> list[Problem]:
    problems: list[Problem] = []
    today = config.today()
    for kind in ("note", "commitment"):
        ledger = read_ledger(config, kind)
        problems.extend(ledger.problems)
        seen: set[str] = set()
        for record in ledger.records:
            where = config.where(ledger.path, record.line)
            fields = record.fields
            if record.id in seen:
                problems.append(problem("duplicate_id", "fail", where, f"duplicate id {record.id}"))
            seen.add(record.id)
            if not fields.get("src", "").strip():
                problems.append(problem("missing_src", "fail", where, "record needs a source"))
            if kind == "note":
                if record.checked:
                    issue = filing_problem(config, record.id, fields.get("filed", ""), where)
                    if issue:
                        problems.append(issue)
                else:
                    level = "fail" if record.day < today else "warn"
                    problems.append(problem("note_unfiled", level, where, f"{record.id} is unfiled"))
            else:
                problems.extend(lint_commitment(config, record, where, today))
    return problems


def lint_commitment(config: Config, record: Record, where: str, today: date) -> list[Problem]:
    problems: list[Problem] = []
    try:
        due = record.due()
    except ValueError:
        due = None
        problems.append(problem("bad_due", "fail", where, "due must be YYYY-MM-DD or none"))
    if record.checked and not record.fields.get("proof", "").strip():
        problems.append(problem("done_without_proof", "fail", where, "completed commitment needs proof"))
    if not record.checked and due is not None and due < today:
        level = "fail" if config.ours(record.fields.get("ball", "")) else "warn"
        problems.append(problem("overdue", level, where, f"{record.id} was due {due}"))
    return problems


def filtered_commitments(records: list[Record], *, open_only: bool = False,
                         cutoff: date | None = None) -> tuple[list[Record], list[Record]]:
    dated, undated = [], []
    for record in records:
        if (open_only or cutoff is not None) and record.checked:
            continue
        if record.fields.get("due") == "none":
            undated.append(record)
        elif cutoff is None:
            dated.append(record)
        else:
            try:
                due = record.due()
            except ValueError:
                continue
            if due is not None and due <= cutoff:
                dated.append(record)
    return dated, undated


def encode_value(value: str) -> str:
    if not value or re.search(r'\s|[\]"]', value):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_line(kind: Kind, fields: dict[str, str], text: str, checked: bool = False) -> str:
    values = " ".join(f"{key}={encode_value(value)}" for key, value in fields.items())
    return f"- [{'x' if checked else ' '}] [{kind} {values}] {text}"


def validate_record_text(value: str, field: str) -> str:
    return validate_written_value(value, field, nonempty=True)


