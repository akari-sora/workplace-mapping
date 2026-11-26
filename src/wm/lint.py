"""Run the requested validation checks."""

from __future__ import annotations

from typing import Any

from .catalog import load_registry
from .config import Config, Problem, problem, result
from .files import read_text_file, text_files
from .outbound import load_rules, read_rules
from .records import lint_records
from .secrets import SECRET_PATTERNS
from .standing import load_standing
from .tables import lint_tables


def lint_secrets(config: Config) -> list[Problem]:
    rules = config.path("rules")
    paths = dict.fromkeys([
        config.relative("workplace.toml"),
        *text_files(config.path("skills"), within=config.root),
        *text_files(config.path("worklog"), within=config.root),
        *([rules] if rules.is_file() else []),
    ])
    problems = []
    for path in paths:
        text = read_text_file(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            matches = [match.span() for pattern in SECRET_PATTERNS for match in pattern.finditer(line)]
            end = -1
            for start, stop in sorted(matches):
                if start >= end:
                    problems.append(problem("secret_like", "fail", config.where(path, number),
                                            "possible credential detected"))
                end = max(end, stop)
    return problems


def lint_global(config: Config) -> list[Problem]:
    path = config.private_terms
    if path is None or not config.global_skills:
        return []
    try:
        terms = [(number, line.strip()) for number, line in
                 enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if line.strip()]
    except (OSError, UnicodeError):
        return [problem("unreadable", "unverified", config.where(path), "private terms cannot be read")]

    problems = []
    paths = dict.fromkeys(file for directory in config.global_skills for file in text_files(directory))
    for file in paths:
        text = read_text_file(file)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for index, term in terms:
                if term in line:
                    problems.append(problem("private_term_in_global", "fail", config.where(file, number),
                                            f"private term from line {index} of terms file"))
    return problems


def lint_rules(config: Config) -> list[Problem]:
    data, text, problems = read_rules(config)
    return problems + load_rules(config, data, text)[1] + load_standing(config, data, text)[1]


def lint(config: Config, name: str | None = None) -> tuple[int, dict[str, Any]]:
    checks = {
        "records": lint_records,
        "state-machines": lint_tables,
        "rules": lint_rules,
        "secrets": lint_secrets,
        "global": lint_global,
        "config": lambda settings: load_registry(settings)[1],
    }
    selected = [name] if name is not None else list(checks)
    return result([issue for check in selected for issue in checks[check](config)])
