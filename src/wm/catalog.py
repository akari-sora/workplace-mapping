"""Validate registrations and expose their public metadata."""

from __future__ import annotations

import math
import re
import shlex
import sys
from typing import Any

from .config import Config, Problem, UsageError, hostname, print_json, problem, result
from .files import write_bytes
from .process import shell

DEFAULT_MENTION = r"@\S+"
DEFAULTS = {
    "system": dict(what="", reach="", hosts=[], login="", probe="", may_leave="", src=""),
    "channel": dict(kind="custom", what="", mention=DEFAULT_MENTION, threads=False,
                    max_chars=0, send="", system="", src="", readback=""),
    "command": dict(what="", run="", writes=False, system="", timeout=60, max_output=8000, readback=""),
}
PUBLIC_FIELDS = {
    "system": ("id", "what", "reach", "hosts", "login", "may_leave", "probe"),
    "channel": ("id", "kind", "what", "threads", "max_chars", "send"),
    "command": ("id", "what", "writes", "system"),
}
PRIVATE_FIELDS = {"probe", "send", "run", "readback"}
Entry = dict[str, Any]
Registry = dict[str, dict[str, Entry]]


def fields_error(kind: str, entry: Entry) -> str | None:
    for key, default in DEFAULTS[kind].items():
        value = entry[key]
        if key == "timeout":
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                return "timeout must be a positive number"
        elif type(value) is not type(default):
            return f"{key} has the wrong type"
        elif key == "hosts" and any(not isinstance(host, str) or not host.strip() for host in value):
            return "hosts must be an array of hostnames"
        elif key in ("max_chars", "max_output") and value < 0:
            return f"{key} must be non-negative"
        elif key in PRIVATE_FIELDS and "\0" in value:
            return f"{key} contains an invalid character"
    if kind == "command" and not entry["run"].strip():
        return "command needs run"
    if kind == "channel":
        try:
            entry["mention"] = re.compile(entry["mention"] or DEFAULT_MENTION)
        except (re.error, OverflowError, RecursionError):
            return "invalid mention regex"
    return None


def load_registry(config: Config) -> tuple[Registry, list[Problem]]:
    registry: Registry = {kind: {} for kind in DEFAULTS}
    problems = []
    for kind, defaults in DEFAULTS.items():
        raw = config.registrations[kind]
        if not isinstance(raw, list):
            problems.append(problem("bad_config", "fail", "workplace.toml", f"{kind} must be an array of tables"))
            continue
        seen = set()
        for index, item in enumerate(raw, 1):
            where = f"workplace.toml:{kind}[{index}]"
            identity = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", identity):
                problems.append(problem("bad_config", "fail", where, "missing or invalid id"))
                continue
            if identity in seen:
                problems.append(problem("bad_config", "fail", where, "duplicate id"))
                continue
            seen.add(identity)
            entry = defaults | item
            issue = fields_error(kind, entry)
            if not issue and kind != "system" and entry["system"] and entry["system"] not in registry["system"]:
                issue = "system is not registered"
            if issue:
                problems.append(problem("bad_config", "fail", where, issue))
            else:
                registry[kind][identity] = entry
    return registry, problems


def public_entry(kind: str, entry: Entry) -> Entry:
    return {key: bool(entry[key].strip()) if key in PRIVATE_FIELDS else entry[key] for key in PUBLIC_FIELDS[kind]}


def catalog(config: Config, kind: str | None) -> tuple[int, dict[str, Any]]:
    registry, problems = load_registry(config)
    return result(problems, **{
        group + "s": [public_entry(group, entry) for entry in entries.values()]
        for group, entries in registry.items() if kind is None or kind == group + "s"
    })


def on_host(hosts: list[str]) -> bool:
    def short(name: str) -> str:
        return name.partition(".")[0].casefold()
    local = short(hostname())
    return not hosts or any(short(host) == local for host in hosts)


def host_problem(registry: Registry, entry: Entry) -> Problem | None:
    system = registry["system"].get(entry.get("system", ""))
    if system and not on_host(system["hosts"]):
        return problem("wrong_host", "fail", "workplace.toml",
                       f"available hosts: {', '.join(system['hosts'])}")
    return None


def read_command(config: Config, identity: str) -> tuple[Entry, list[Problem]]:
    registry, problems = load_registry(config)
    entry = registry["command"].get(identity, {})
    if not problems:
        if not entry:
            raise UsageError("unknown command id")
        if entry["writes"]:
            problems.append(problem("use_action", "fail", "workplace.toml", "use wm action for writes"))
        elif issue := host_problem(registry, entry):
            problems.append(issue)
    return entry, problems


def command_line(entry: Entry, args: list[str]) -> str:
    return entry["run"] + (" " + shlex.join(args) if args else "")


def run_registered(config: Config, identity: str, args: list[str], pretty: bool, full: bool = False) -> int:
    entry, problems = read_command(config, identity)
    if problems:
        code, output = result(problems)
        print_json(output, pretty)
        return code

    execution = shell(config.root, command_line(entry, args), entry["timeout"], capture=True)
    stdout = execution.stdout.decode("utf-8", "surrogateescape")
    limit = 0 if full else entry["max_output"]
    truncated = limit and len(stdout) > limit
    if truncated:
        path = config.relative(str(config.path("state") / "last" / f"{identity}.txt"))
        write_bytes(path, execution.stdout, permissions=0o600)
    shown = stdout
    stderr = execution.stderr.decode("utf-8", "surrogateescape")
    if truncated:
        shown = shown[:limit]
        if shown and not shown.endswith("\n"):
            shown += "\n"
        shown += f"... truncated: {len(stdout)} characters in total, full output in {config.where(path)}\n"
    sys.stdout.buffer.write(shown.encode("utf-8", "surrogateescape"))
    sys.stderr.buffer.write(stderr.encode("utf-8", "surrogateescape"))
    if execution.exit is None:
        _, output = result([problem("timeout", "fail", "workplace.toml", "command timed out")])
        print_json(output, pretty, file=sys.stderr)
        return 3
    return execution.exit if execution.exit >= 0 else 128 - execution.exit
