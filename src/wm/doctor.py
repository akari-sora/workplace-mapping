"""Check project setup without changing it."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from .config import Config, Problem, problem, reject_json_constant, result
from .git import GitError, git, is_repository


HOOKS = {
    "stop-gate": "Stop",
    "brief --hook": "SessionStart",
    "outbound hook": "PreToolUse",
}


def commands(data: Any, event: str) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return []
    groups = data["hooks"].get(event)
    if not isinstance(groups, list):
        return []
    return [hook["command"] for group in groups if isinstance(group, dict) and isinstance(group.get("hooks"), list)
            for hook in group["hooks"] if isinstance(hook, dict) and hook.get("type") == "command"
            and isinstance(hook.get("command"), str)]


def registers(command: str, expected: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or Path(tokens[0]).name != "wm":
        return False
    command_tokens = []
    options = iter(tokens[1:])
    for token in options:
        if token in ("--root", "--now"):
            next(options, None)
        elif token not in ("--pretty", "--json") and not token.startswith(("--root=", "--now=")):
            command_tokens.append(token)
    signature = expected.split()
    return command_tokens[:len(signature)] == signature


def doctor(config: Config) -> tuple[int, dict[str, Any]]:
    configured = []
    for key, path in config.paths.items():
        if key == "people_cards":
            path = config.people_cards()
        configured.append({"key": key, "path": config.where(path) if path is not None else None,
                           "exists": path.exists() if path is not None else False})
    for path in config.state_machines():
        configured.append({"key": "state_machines", "path": config.where(path), "exists": path.exists()})
    if not any(entry["key"] == "state_machines" for entry in configured):
        configured.append({"key": "state_machines", "path": None, "exists": False})
    if config.private_terms is not None:
        configured.append({"key": "private_terms", "path": config.where(config.private_terms),
                           "exists": config.private_terms.exists()})
    for path in config.global_skills:
        configured.append({"key": "global_skills", "path": config.where(path), "exists": path.exists()})
    problems: list[Problem] = []
    state = config.where(config.path("state"))
    actions = config.where(config.path("actions"))
    ignored = None
    actions_ignored = None
    try:
        if is_repository(config.root):
            check = git(config.root, "check-ignore", "-q", "--", state.rstrip("/") + "/")
            if check.returncode not in (0, 1):
                raise GitError("state ignore status could not be verified")
            ignored = check.returncode == 0
            if not ignored:
                problems.append(problem("state_not_ignored", "warn", state, "state folder is not git-ignored"))
            check = git(config.root, "check-ignore", "-q", "--", actions)
            if check.returncode not in (0, 1):
                problems.append(problem("git_unverified", "unverified", actions,
                                        "action log ignore status could not be verified"))
            else:
                actions_ignored = check.returncode == 0
                if not actions_ignored:
                    problems.append(problem("actions_not_ignored", "warn", actions,
                                            "action log is not git-ignored"))
    except GitError as exc:
        problems.append(problem("git_unverified", "unverified", state, str(exc)))

    location = ".claude/settings.json"
    try:
        data = json.loads(config.relative(location).read_text(encoding="utf-8"), parse_constant=reject_json_constant)
    except (OSError, ValueError):
        data = {}
    hooks = {}
    for name, event in HOOKS.items():
        registered = any(registers(command, name) for command in commands(data, event))
        hooks[name] = {"registered": registered}
        if not registered:
            problems.append(problem("hook_not_registered", "warn", location, f"wm {name} is not registered"))
    return result(problems, paths=configured, state_ignored=ignored, actions_ignored=actions_ignored,
                  resident_host=config.resident_host, hooks=hooks)
