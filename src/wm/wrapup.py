"""Build and verify project handoffs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .actions import listed_actions
from .config import Config, Problem, problem, result
from .files import write_text
from .git import GitError, branch, is_repository, unpushed, unpushed_subjects
from .markdown import Section, section
from .next import next_items
from .records import lint_records, read_ledger, render_line, validate_record_text
from .render import action_line, item_line, record_line
from .writers import write_handoff


def day_path(config: Config, day: str) -> Path:
    return config.relative(str(config.path("worklog") / f"{day}.md"))


def start_section(config: Config, text: str) -> Section | None:
    return section(text, config.start_heading, "明天從哪開始")


def handoff_sections(config: Config, path: Path) -> tuple[str, list[Problem]]:
    where = config.where(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not path.is_symlink():
            return "", [problem("no_worklog_today", "fail", where, "today's worklog is missing")]
        return "", [problem("unreadable", "unverified", where, "today's worklog cannot be read")]
    except (OSError, UnicodeError):
        return "", [problem("unreadable", "unverified", where, "today's worklog cannot be read")]

    problems = []
    start = start_section(config, text)
    if start is None or not start.body:
        problems.append(problem("no_start_section", "fail", where, "start heading needs a handoff entry"))
    commits = section(text, config.unpushed_heading, "未推提交")
    if commits is None:
        problems.append(problem("no_unpushed_section", "fail", where, "unpushed-commits heading is missing"))
    return commits.body if commits else "", problems


def pending_path(config: Config) -> Path:
    return config.relative(str(config.path("state") / "wrapup-pending"))


def wrapup_begin(config: Config) -> tuple[int, dict[str, Any]]:
    path = pending_path(config)
    write_text(path, "")
    return result(pending=config.where(path))


def wrapup_check(config: Config) -> tuple[int, dict[str, Any]]:
    path = day_path(config, config.today().isoformat())
    where = config.where(path)
    listed, problems = handoff_sections(config, path)
    try:
        if is_repository(config.root):
            for identity in unpushed(config.root):
                if not re.search(rf"(?<![0-9a-f]){identity}", listed, re.IGNORECASE):
                    problems.append(problem("unpushed_not_listed", "fail", where, f"list unpushed commit {identity}"))
    except GitError as exc:
        problems.append(problem("unpushed_unverified", "unverified", where, str(exc)))
    checks, _, _ = preflight(config)
    problems.extend(checks)
    code, output = outcome(problems)
    if code == 0:
        passed = config.relative(str(config.path("state") / "wrapup-pass"))
        write_text(passed, f"{config.now().isoformat()}\n")
        pending_path(config).unlink(missing_ok=True)
    return code, output


def preflight(config: Config) -> tuple[list[Problem], list[dict], list[dict]]:
    problems = lint_records(config)
    actions, action_problems = listed_actions(config, open_only=True)
    problems.extend(action_problems)
    action_log = config.where(config.path("actions"))
    for action in actions:
        identity = action["id"]
        if action["state"] == "unknown":
            problems.append(problem("action_unknown", "fail", action_log, f"{identity} needs resolution"))
        elif action["state"] == "sent":
            problems.append(problem("action_unverified", "warn", action_log, f"{identity} needs read-back"))
    _, upcoming = next_items(config)
    problems.extend(issue for issue in upcoming["problems"] if issue["code"] == "item_unverified")
    evidence = config.path("evidence")
    if evidence.exists() or evidence.is_symlink():
        problems.extend(issue for issue in upcoming["problems"] if issue["code"] == "evidence_unverified")
    return problems, upcoming["items"], actions


def outcome(problems: list[Problem], **fields: Any) -> tuple[int, dict[str, Any]]:
    code, output = result(problems, **fields)
    output["result"] = {0: "PASS", 1: "WARN", 2: "FAIL", 4: "UNVERIFIED"}[code]
    return code, output


def wrapup_write(config: Config, start: str, notes: list[str], replace: bool) -> tuple[int, dict[str, Any]]:
    start = validate_record_text(start, "start").strip()
    notes = [validate_record_text(note, "note").strip() for note in notes]
    wrapup_begin(config)
    path = day_path(config, config.today().isoformat())
    where = config.where(path)
    problems, items, actions = preflight(config)
    if any(issue["level"] != "warn" for issue in problems):
        return outcome(problems, path=where)
    commits = []
    try:
        if is_repository(config.root):
            commits = unpushed_subjects(config.root)
    except GitError as exc:
        return outcome([*problems, problem("unpushed_unverified", "unverified", where, str(exc))], path=where)

    lines = [f"## {config.start_heading}", "", *(f"- {entry}" for entry in (start, *notes))]
    commitments = [record.output() for record in read_ledger(config, "commitment").records if not record.checked]
    groups = (("Open items", items, lambda item: item_line(item, proof=False)),
              ("Open commitments", commitments, record_line), ("Open actions", actions, action_line))
    for heading, entries, render_entry in groups:
        if entries:
            lines.extend(["", f"### {heading}", "", *(f"- {render_entry(entry)}" for entry in entries)])
    lines.extend(["", f"## {config.unpushed_heading}", ""])
    lines.extend([f"- {commit}" for commit in commits] or ["none"])
    issues = write_handoff(config, path, lines, replace)
    if issues:
        return outcome([*problems, *issues], path=where)
    code, output = wrapup_check(config)
    return code, {**output, "path": where}


def draft_git(config: Config) -> tuple[str | None, list[str], list[Problem]]:
    current = None
    try:
        if is_repository(config.root):
            current = branch(config.root)
            return current, unpushed(config.root), []
    except GitError as exc:
        return current, [], [problem("unpushed_unverified", "unverified", ".", str(exc))]
    return current, [], []


def wrapup_draft(config: Config) -> tuple[int, dict[str, Any]]:
    current, hashes, problems = draft_git(config)
    _, upcoming = next_items(config)
    notes = read_ledger(config, "note")
    commitments = read_ledger(config, "commitment")
    problems.extend(upcoming["problems"])
    problems.extend(issue for issue in commitments.problems if issue not in problems)
    problems.extend(notes.problems)
    markdown = [f"## {config.start_heading}", "", "### Open commitments"]
    markdown.extend(render_line(record.kind, record.fields, record.text)
                    for record in commitments.records if not record.checked)
    markdown.extend(["", "### Next"])
    for item in upcoming["now"]:
        label = item.get("title") or item.get("text") or item.get("state") or "Untitled"
        markdown.append(f"- {item.get('id', '')} {label}".rstrip())
    markdown.extend(["", "### Unfiled notes"])
    markdown.extend(render_line(record.kind, record.fields, record.text)
                    for record in notes.records if not record.checked)
    markdown.extend(["", f"## {config.unpushed_heading}", ""])
    if current:
        markdown.append(f"Branch: {current}")
    if hashes:
        markdown.extend(f"- {identity}" for identity in hashes)
    else:
        markdown.append("unverified" if any(issue["code"] == "unpushed_unverified" for issue in problems) else "none")
    return result(problems, markdown="\n".join(markdown) + "\n")
