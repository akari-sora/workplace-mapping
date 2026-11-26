"""Run registered reachability probes without relaying their output."""

from __future__ import annotations

from typing import Any

from .catalog import load_registry, on_host
from .config import Config, UsageError, problem, result
from .process import shell


def check_access(config: Config, ids: list[str], timeout: float) -> tuple[int, dict[str, Any]]:
    registry, problems = load_registry(config)
    systems = registry["system"]
    if problems:
        return result(problems, systems=[])
    if any(identity not in systems for identity in ids):
        raise UsageError("unknown system id")

    results = []
    probed = False
    for identity in dict.fromkeys(ids or systems):
        system = systems[identity]
        if not on_host(system["hosts"]):
            results.append({"id": identity, "status": "wrong_host", "hosts": system["hosts"]})
        elif not system["probe"].strip():
            results.append({"id": identity, "status": "no_probe"})
        else:
            probed = True
            try:
                exit_code = shell(config.root, system["probe"], timeout, quiet=True).exit
            except OSError:
                exit_code = "could_not_start"
            if exit_code == 0:
                results.append({"id": identity, "status": "reachable"})
            else:
                results.append({"id": identity, "status": "unreachable",
                                "exit": "timeout" if exit_code is None else exit_code})
                problems.append(problem("unreachable", "unverified", "workplace.toml",
                                        f"system {identity} could not be reached"))
    if not probed:
        problems.append(problem("no_probe", "warn", "workplace.toml", "no system was probed"))
    return result(problems, systems=results)
