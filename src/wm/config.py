"""Root discovery, configuration, and the shared result format."""

from __future__ import annotations

import json
import math
import os
import re
import socket
import tomllib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Level = Literal["warn", "fail", "unverified"]


class Problem(TypedDict):
    code: str
    level: Level
    where: str
    detail: str


EXIT = {"warn": 1, "fail": 2, "unverified": 4}
STATUS = {0: "ok", 1: "warn", 2: "fail", 4: "unverified", 64: "fail"}

DEFAULT_PATHS = {
    "skills": ".agents/skills",
    "state_machines": [],
    "people_cards": "",
    "commitments": "worklog/commitments.md",
    "notes": "worklog/inbox.md",
    "worklog": "worklog",
    "evidence": "data/evidence.json",
    "rules": "rules.toml",
    "state": ".wm",
    "actions": ".wm/actions.jsonl",
}


class UsageError(ValueError):
    pass


class BadValue(ValueError):
    """A value destined for a project file is not safe as one field."""


def validate_written_value(value: str, field: str, *, nonempty: bool = False) -> str:
    if ((nonempty and not value.strip()) or (value and value.splitlines() != [value])
            or any(unicodedata.category(character) == "Cc" for character in value)):
        qualifier = "nonempty and " if nonempty else ""
        raise BadValue(f"{field} must be {qualifier}one line without control characters")
    return value


def problem(code: str, level: Level, where: str, detail: str) -> Problem:
    return {"code": code, "level": level, "where": where, "detail": detail}


def result(problems: list[Problem] | None = None, **fields: Any) -> tuple[int, dict[str, Any]]:
    problems = problems if problems is not None else []
    code = max((EXIT[item["level"]] for item in problems), default=0)
    return code, {"ok": code == 0, "status": STATUS[code], "problems": problems, **fields}


def usage_result(message: str) -> dict[str, Any]:
    return {"ok": False, "status": "fail", "problems": [problem("usage", "fail", "wm", message)]}


def print_json(data: dict[str, Any], pretty: bool = False, *, file: Any = None) -> None:
    serialized = json.dumps(data, ensure_ascii=False, indent=2 if pretty else None, default=str)
    print(serialized.encode("utf-8", "backslashreplace").decode("utf-8"), file=file)


def hostname() -> str:
    return os.environ.get("WM_HOSTNAME") or socket.gethostname()


def reject_json_constant(_: str) -> NoReturn:
    raise ValueError("non-finite number is not valid JSON")


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9-]+", name))


def root_path(root: Path, value: str) -> Path:
    path = root / value
    try:
        resolved = path.resolve()
    except RuntimeError as exc:
        raise UsageError("project path cannot be resolved") from exc
    if not resolved.is_relative_to(root):
        raise UsageError("project paths must stay under the project root")
    return path


def configured_path(root: Path, value: str) -> Path:
    if not value or Path(value).is_absolute():
        raise UsageError("configured project paths must be nonempty and relative")
    return root_path(root, value)


def find_root(explicit: str | None) -> Path:
    selected = explicit if explicit is not None else os.environ.get("WM_ROOT")
    if explicit == "":
        raise UsageError("--root must not be empty")
    if selected:
        return Path(selected).expanduser().resolve()
    cwd = Path.cwd()
    for directory in (cwd, *cwd.parents):
        if (directory / "workplace.toml").is_file():
            return directory.resolve()
    raise UsageError("no project root with workplace.toml")


def read_table(document: dict[str, Any], name: str) -> dict[str, Any]:
    table = document.get(name, {})
    if not isinstance(table, dict):
        raise UsageError(f"[{name}] must be a table")
    return table


def get_string(table: dict[str, Any], key: str, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise UsageError(f"{key} must be a string")
    return value


def get_strings(table: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = table.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise UsageError(f"{key} must be an array of strings")
    return value


def number(table: dict[str, Any], key: str, default: int, integers_only: bool = False) -> int | float:
    value = table.get(key, default)
    types = (int,) if integers_only else (int, float)
    if type(value) not in types or value < 0 or (isinstance(value, float) and not math.isfinite(value)):
        raise UsageError(f"{key} must be a non-negative {'integer' if integers_only else 'number'}")
    return value


@dataclass(frozen=True)
class Config:
    root: Path
    name: str
    zone: ZoneInfo | None
    us: frozenset[str]
    paths: dict[str, Path | None]
    machine_paths: tuple[Path, ...]
    start_heading: str
    unpushed_heading: str
    lookback_days: int
    stale_hours: float
    global_skills: tuple[Path, ...]
    private_terms: Path | None
    clock_override: datetime | None
    resident_host: str
    registrations: dict[str, Any]

    def path(self, key: str) -> Path:
        path = self.paths.get(key)
        if path is None:
            raise ValueError(f"no path configured for {key}")
        return path

    def relative(self, value: str) -> Path:
        return root_path(self.root, value)

    def where(self, path: Path, line: int | None = None) -> str:
        name = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)
        return f"{name}:{line}" if line is not None else name

    def local_time(self, value: datetime) -> datetime:
        if value.tzinfo is None and self.zone is not None:
            return value.replace(tzinfo=self.zone)
        return value.astimezone(self.zone)

    def parse_time(self, value: str) -> datetime:
        return self.local_time(datetime.fromisoformat(value))

    def now(self) -> datetime:
        return self.clock_override or datetime.now(self.zone).astimezone(self.zone)

    def today(self) -> date:
        return self.now().date()

    def ours(self, value: str) -> bool:
        return value.strip().casefold() in self.us

    def names_our_side(self, value: str) -> bool:
        value = value.casefold()
        return any(
            re.search(rf"(?<![a-z0-9_]){re.escape(side)}(?![a-z0-9_])", value) if side.isascii() else side in value
            for side in self.us if side
        )

    def find_skills_file(self, filename: str) -> list[Path]:
        return [
            path for path in sorted(self.path("skills").rglob(filename))
            if path.resolve().is_relative_to(self.root)
        ]

    def state_machines(self) -> list[Path]:
        return list(self.machine_paths) if self.machine_paths else self.find_skills_file("state-machines.md")

    def people_cards(self) -> Path | None:
        path = self.paths["people_cards"]
        if path is not None:
            return path
        return next(iter(self.find_skills_file("people-cards.md")), None)


def load_config(explicit_root: str | None, now: str | None) -> Config:
    try:
        return _load_config(explicit_root, now)
    except (OSError, ValueError, RuntimeError, OverflowError) as exc:
        if isinstance(exc, UsageError):
            raise
        raise UsageError("cannot read or parse workplace.toml, or invalid --now clock") from exc


def _load_config(explicit_root: str | None, now: str | None) -> Config:
    if now is not None and os.environ.get("WM_TEST_CLOCK") != "1":
        raise UsageError("--now is available only when WM_TEST_CLOCK=1")
    root = find_root(explicit_root)
    with root_path(root, "workplace.toml").open("rb") as handle:
        document = tomllib.load(handle)
    workplace = read_table(document, "workplace")
    name = get_string(workplace, "name")
    if not valid_name(name):
        raise UsageError("workplace.name must use lowercase letters, digits and hyphens")
    timezone = get_string(workplace, "timezone")
    try:
        zone = ZoneInfo(timezone) if timezone else None
    except ZoneInfoNotFoundError as exc:
        raise UsageError("unknown workplace timezone") from exc

    paths = read_table(document, "paths")
    project_paths = {}
    for key, default in DEFAULT_PATHS.items():
        if key != "state_machines":
            value = get_string(paths, key, default)
            project_paths[key] = None if key == "people_cards" and not value else configured_path(root, value)
    machines = tuple(configured_path(root, value) for value in get_strings(paths, "state_machines", []))

    handoff = read_table(document, "handoff")
    lint = read_table(document, "lint")
    private_terms = get_string(lint, "private_terms")
    global_skills = tuple(root / Path(value).expanduser() for value in get_strings(lint, "global_skills", []))
    override = datetime.fromisoformat(now) if now is not None else None
    if override is not None:
        override = override.replace(tzinfo=zone) if override.tzinfo is None and zone else override.astimezone(zone)

    return Config(
        root, name, zone, frozenset(side.casefold() for side in get_strings(workplace, "us", ["us", "me", "我方"])),
        project_paths, machines, get_string(handoff, "start_heading", "Start here"),
        get_string(handoff, "unpushed_heading", "Unpushed commits"),
        int(number(handoff, "lookback_days", 7, integers_only=True)),
        number(read_table(document, "next"), "stale_hours", 72),
        global_skills, configured_path(root, private_terms) if private_terms else None, override,
        get_string(workplace, "resident_host"),
        {kind: document.get(kind, []) for kind in ("system", "channel", "command")},
    )
