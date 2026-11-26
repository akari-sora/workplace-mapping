"""Collect project context for the start of a session."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .actions import listed_actions
from .config import Config, Problem, hostname, problem, result
from .git import GitError, branch, changed_files, is_repository
from .records import Ledger, filtered_commitments, parse_due, read_ledger
from .wrapup import start_section


def recent_worklog(config: Config, today: date, problems: list[Problem]) -> dict[str, str | None] | None:
    worklog = config.path("worklog")
    try:
        paths = sorted(worklog.iterdir(), reverse=True)
    except OSError:
        problems.append(problem("unreadable", "unverified", config.where(worklog), "worklog folder cannot be read"))
        return None
    for path in paths:
        if path.suffix != ".md":
            continue
        try:
            day = parse_due(path.stem)
        except ValueError:
            continue
        if not 0 <= (today - day).days <= config.lookback_days:
            continue
        where = config.where(path)
        try:
            text = config.relative(str(path)).read_text(encoding="utf-8")
        except (OSError, ValueError):
            problems.append(problem("unreadable", "unverified", where, "worklog cannot be read"))
            continue
        start = start_section(config, text)
        if start is None or not start.body:
            problems.append(problem("no_start_section", "unverified", where, "worklog has no usable start section"))
        return {"date": day.isoformat(), "path": where, "start": start.body if start else None}
    return None


def brief_ledger_problems(config: Config, ledger: Ledger) -> list[Problem]:
    if not ledger.problems and not ledger.path.exists():
        return [problem("unreadable", "unverified", config.where(ledger.path), "ledger is missing")]
    return [problem(item["code"], "unverified", item["where"], item["detail"]) for item in ledger.problems]


def brief(config: Config) -> tuple[int, dict[str, Any]]:
    today = config.today()
    problems: list[Problem] = []
    current, changes = None, None
    try:
        if is_repository(config.root):
            current = branch(config.root)
            changes = changed_files(config.root)
    except GitError as exc:
        problems.append(problem("git_unverified", "unverified", ".", str(exc)))

    worklog = recent_worklog(config, today, problems)
    notes, commitments = read_ledger(config, "note"), read_ledger(config, "commitment")
    for ledger in (notes, commitments):
        problems.extend(brief_ledger_problems(config, ledger))
    for record in commitments.records:
        try:
            record.due()
        except ValueError:
            problems.append(problem("bad_due", "unverified", config.where(commitments.path, record.line),
                                    "due date cannot be read"))
    cutoff = today + timedelta(days=min(2, (date.max - today).days))
    due, undated = filtered_commitments(commitments.records, cutoff=cutoff)
    actions, action_problems = listed_actions(config, open_only=True)
    problems.extend(action_problems)
    return result(
        problems, hostname=hostname(), cwd=str(Path.cwd()), branch=current, changed_files=changes,
        worklog=worklog, due=[record.output() for record in due], undated=[record.output() for record in undated],
        unfiled_notes=[record.output() for record in notes.records if not record.checked], actions=actions,
        unverified=problems,
    )


def as_text(data: dict[str, Any]) -> str:
    lines = [f"Hostname: {data['hostname']}", f"Directory: {data['cwd']}"]
    if data["branch"]:
        lines.append(f"Git branch: {data['branch']}")
    if data["changed_files"] is not None:
        lines.append(f"Changed files: {data['changed_files']}")
    worklog = data["worklog"]
    if worklog and worklog["start"]:
        lines.extend([f"Start here ({worklog['date']}):", worklog["start"]])
    for label, key in (("Commitments due within two days", "due"), ("Undated commitments", "undated"),
                       ("Unfiled notes", "unfiled_notes")):
        if data[key]:
            lines.append(f"{label}:")
            lines.extend(f"- {record['id']} {record['text']}" for record in data[key])
    if data["actions"]:
        lines.append("Open actions:")
        lines.extend(f"- {action['id']} {action['state']} ({action['target']})" for action in data["actions"])
    if data["unverified"]:
        lines.append("Unverified:")
        lines.extend(f"- {item['code']} {item['where']}: {item['detail']}" for item in data["unverified"])
    return "\n".join(lines)
