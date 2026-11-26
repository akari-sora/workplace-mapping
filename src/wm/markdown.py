"""Small Markdown readers shared by state tables, cards, and handoffs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")


def visible_lines(lines: list[str]) -> Iterator[tuple[int, str]]:
    fence = ""
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = ""
        elif match and (match[1][0] == "~" or "`" not in match[2]):
            fence = match[1]
        else:
            yield index, line


def heading(line: str) -> tuple[int, str] | None:
    match = HEADING.match(line)
    if match:
        title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
        return len(match[1]), title
    return None


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    body: str
    full: str
    start: int
    end: int


def sections(text: str) -> Iterator[Section]:
    lines = text.splitlines()
    headings = [(index, info) for index, line in visible_lines(lines) if (info := heading(line))]
    for i, (start, (level, title)) in enumerate(headings):
        end = next((index for index, (depth, _) in headings[i + 1:] if depth <= level), len(lines))
        yield Section(level, title, "\n".join(lines[start + 1:end]).strip(),
                      "\n".join(lines[start:end]).strip(), start, end)


def section(text: str, *titles: str) -> Section | None:
    names = {title.casefold() for title in titles}
    return next((part for part in sections(text) if part.title.casefold() in names), None)
