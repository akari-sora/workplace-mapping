"""Discover and validate state-machine tables."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .config import Config, Problem, problem, result
from .markdown import heading, visible_lines


HEADERS = (
    ("State", "Trigger / who", "Next", "Verified by", "Pitfalls", "Source"),
    ("狀態", "觸發／誰", "下一個狀態", "怎麼驗證做到了", "常見坑", "來源"),
)
TERMINALS = {"done", "完成", "end", "—", "-"}
MARKS = {"✅": "actionable", "🙋": "needs_user", "📖": "read_only"}
STATE_ID = re.compile(r"([A-Z]+)[0-9]+")
SEPARATOR = re.compile(r":?-+:?")
PIPE = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class State:
    state: str
    state_name: str
    trigger: str
    next: str
    verify: str
    pitfalls: str
    source: str
    where: str
    flow_title: str

    @property
    def flow(self) -> str:
        match = STATE_ID.fullmatch(self.state)
        return match[1] if match else ""

    def action(self, config: Config) -> str:
        if not config.names_our_side(self.trigger):
            return "waiting"
        return next((action for mark, action in MARKS.items() if mark in self.trigger), "read_only")

    def output(self) -> dict[str, str]:
        return {
            "flow": self.flow, "state": self.state, "state_name": self.state_name,
            "trigger": self.trigger, "next": self.next, "verify": self.verify,
            "pitfalls": self.pitfalls, "source": self.source,
        }


def cells(line: str) -> tuple[str, ...]:
    parts = PIPE.split(line.strip())
    if len(parts) == 1:
        return ()
    if not parts[0]:
        parts.pop(0)
    if parts and not parts[-1]:
        parts.pop()
    return tuple(part.strip().replace(r"\|", "|") for part in parts)


def table_start(lines: dict[int, str], index: int) -> tuple[str, ...]:
    titles = cells(lines.get(index, ""))
    divider = cells(lines.get(index + 1, ""))
    return titles if titles and len(titles) == len(divider) and all(map(SEPARATOR.fullmatch, divider)) else ()


def parse_tables(text: str, where: str) -> tuple[list[State], list[Problem]]:
    lines = dict(visible_lines(text.splitlines()))
    rows, problems = [], []
    title = ""
    for index, line in lines.items():
        if info := heading(line):
            title = info[1]
        if table_start(lines, index) not in HEADERS:
            continue
        cursor = index + 2
        while cells(lines.get(cursor, "")) and not heading(lines[cursor]) and not table_start(lines, cursor):
            columns = cells(lines[cursor])
            if len(columns) != 6:
                problems.append(problem("bad_row", "fail", f"{where}:{cursor + 1}",
                                        "state-machine row must have exactly six cells"))
                cursor += 1
                continue
            label, *values = columns
            tokens = label.split(maxsplit=1)
            state = tokens[0].strip("`") if tokens else ""
            name = tokens[1] if len(tokens) > 1 else ""
            rows.append(State(state, name, *values, f"{where}:{cursor + 1}", title))
            cursor += 1
        if not has_open_questions(lines, cursor, max(lines, default=-1) + 1):
            problems.append(problem("no_open_questions", "warn", f"{where}:{index + 1}",
                                    "table needs an Open questions line"))
    return rows, problems


def state_machine(config: Config, identity: str | None) -> tuple[int, dict[str, Any]]:
    rows, problems = load_tables(config)
    if identity:
        selected = [row for row in rows if identity in (row.state, row.flow)]
        if not selected:
            problems.append(problem("state_missing", "fail", config.where(config.path("skills")),
                                    f"no state or flow with id {identity}"))
        elif len(selected) > 1 and any(row.state == identity for row in selected):
            problems.append(problem("state_ambiguous", "fail", selected[0].where, f"multiple rows for {identity}"))
        return result(problems, states=[row.output() for row in selected], selection=identity)

    flows = {}
    for row in rows:
        if row.flow:
            title = re.sub(rf"^{row.flow}(?:[.:\s-]+|$)", "", row.flow_title)
            flow = flows.setdefault(row.flow, {"id": row.flow, "title": title, "first": row.state})
            flow["last"] = row.state
    return result(problems, flows=list(flows.values()))


def has_open_questions(lines: dict[int, str], start: int, end: int) -> bool:
    for index in range(start, end):
        line = lines.get(index, "")
        if heading(line) or table_start(lines, index):
            break
        if line.lstrip().startswith(("Open questions:", "待確認：")):
            return True
    return False


def load_tables(config: Config) -> tuple[list[State], list[Problem]]:
    rows, problems = [], []
    found = False
    for path in config.state_machines():
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError):
            problems.append(problem("unreadable", "unverified", config.where(path), "state-machine file cannot be read"))
            continue
        found = True
        states, issues = parse_tables(text, config.where(path))
        rows.extend(states)
        problems.extend(issues)
    if not found and not problems:
        problems.append(problem("no_state_machines", "unverified", config.where(config.path("skills")),
                                "no state-machine file found"))
    return rows, problems


def lint_tables(config: Config) -> list[Problem]:
    rows, problems = load_tables(config)
    counts = Counter(row.state for row in rows if row.flow)
    seen: set[str] = set()
    for row in rows:
        if not row.flow:
            problems.append(problem("bad_state_id", "fail", row.where, "state id must be capital letters and digits"))
        elif row.state in seen:
            problems.append(problem("duplicate_state", "fail", row.where, f"duplicate state {row.state}"))
        seen.add(row.state)
        for code, value, detail in (
            ("missing_verify", row.verify, "state needs a verification method"),
            ("missing_source", row.source, "state needs a source"),
        ):
            if value in {"", "-", "—"}:
                problems.append(problem(code, "fail", row.where, detail))
        for target in re.split(r"[/,]", row.next):
            target = target.strip().strip("`")
            if target and target.casefold() not in TERMINALS and target not in counts:
                problems.append(problem("unknown_next", "fail", row.where, "next state is not in the state tables"))
        if config.names_our_side(row.trigger) and not any(mark in row.trigger for mark in MARKS):
            problems.append(problem("missing_auth", "warn", row.where, "our trigger needs an authorization mark"))
    return problems
