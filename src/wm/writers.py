"""Edit Markdown and TOML by staging and validating exact byte-preserving changes."""

from __future__ import annotations

import io
import json
import re
import shlex
import tomllib
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from .config import Config, Problem, UsageError, problem, result, validate_written_value
from .files import MultiWriteError, ledger_lock, write_text, write_texts
from .markdown import Section, sections, visible_lines
from .outbound import aliases, load_rules
from .records import (FIELD, LEDGER, PREFIX, RECORD, Kind, Record, encode_value, filing_problem,
                      parse_record, read_ledger, render_line, validate_record_text)
from .standing import load_standing
from .tables import PIPE, cells, parse_tables

EMPTY_PITFALLS = {"", "-", "—", "–", "none", "none yet", "無"}
DRAFTING_TITLES = {"before drafting", "擬稿前"}
QUESTIONS = ("Open questions:", "待確認：")


def add_record(config: Config, kind: Kind, fields: dict[str, str], text: str) -> tuple[int, dict[str, Any]]:
    text = validate_record_text(text, "text").strip()
    for key, value in fields.items():
        validate_record_text(value, key)
    path = config.path(LEDGER[kind])
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    try:
        with ledger_lock(path):
            return append_record(config, kind, fields, text)
    except Refusal as exc:
        return result([refusal(config, path, exc)])


def append_record(config: Config, kind: Kind, fields: dict[str, str], text: str) -> tuple[int, dict[str, Any]]:
    ledger = read_ledger(config, kind)
    if ledger.problems:
        return result(ledger.problems)
    today = config.today()
    start = f"{PREFIX[kind]}-{today.year:04}{today.month:02}{today.day:02}-"
    existing = {record.id for record in ledger.records}
    identity = next((f"{start}{index:02}" for index in range(1, 100) if f"{start}{index:02}" not in existing), None)
    if identity is None:
        return result([problem("id_exhausted", "fail", config.where(ledger.path), "no free id for today")])
    fields = {"id": identity, **fields}
    contents = "".join(ledger.lines)
    updated = block_at(contents, len(contents), [render_line(kind, fields, text)])
    record = Record(kind, False, fields, text, today, len(ledger.lines) + 1)
    stage(ledger.path, updated, lambda value: record_matches(value, record))
    return result(record=record.output())


def mark_record(config: Config, kind: Kind, identity: str, value: str) -> tuple[int, dict[str, Any]]:
    key = "proof" if kind == "commitment" else "filed"
    validate_record_text(value, key)
    path = config.path(LEDGER[kind])
    if not path.resolve().parent.is_dir():
        return result(read_ledger(config, kind).problems or
                      [problem("unknown_id", "fail", config.where(path), f"no record with id {identity}")])
    try:
        with ledger_lock(path):
            return update_record(config, kind, identity, key, value)
    except Refusal as exc:
        return result([refusal(config, path, exc)])


def update_record(config: Config, kind: Kind, identity: str, key: str,
                  value: str) -> tuple[int, dict[str, Any]]:
    ledger = read_ledger(config, kind)
    if ledger.problems:
        return result(ledger.problems)
    matches = [record for record in ledger.records if record.id == identity]
    where = config.where(ledger.path)
    if not matches:
        return result([problem("unknown_id", "fail", where, f"no record with id {identity}")])
    record = matches[0]
    where = config.where(ledger.path, record.line)
    if len(matches) > 1:
        return result([problem("duplicate_id", "fail", where, f"duplicate id {identity}")])
    # A recorded proof or filing is never replaced, whether or not the line's box was ticked by hand.
    if record.checked or record.fields.get(key):
        code = "already_filed" if kind == "note" else "already_done"
        detail = f"{identity} is already {'filed' if kind == 'note' else 'done'}"
        return result([problem(code, "fail", where, detail)], record=record.output())
    if kind == "note":
        issue = filing_problem(config, identity, value, where)
        if issue:
            return result([issue])

    updated_text, checked = record_update_candidate(ledger, record, key, value)
    previous = "".join(ledger.lines)
    if updated_text != previous:
        stage(ledger.path, updated_text, lambda text: record_matches(text, checked))
    return result(record=checked.output())


def record_update_candidate(ledger: Any, record: Record, key: str, value: str) -> tuple[str, Record]:
    fields = {**record.fields, key: value}
    previous = ledger.lines[record.line - 1]
    tag = RECORD.match(previous)
    offset = tag.end()
    updated = previous[:tag.start(1)] + "x" + previous[tag.end(1):]
    field_value = encode_value(value)
    if record.fields.get(key) != value:
        while field := FIELD.match(updated, offset):
            offset = field.end()
            if field[1] == key:
                updated = updated[:field.start(2)] + field_value + updated[field.end(2):]
                break
        else:
            updated = updated[:offset] + f" {key}={field_value}" + updated[offset:]
    checked = Record(record.kind, True, fields, record.text, record.day, record.line)
    lines = list(ledger.lines)
    lines[record.line - 1] = updated
    return "".join(lines), checked


def record_matches(text: str, record: Record) -> bool:
    # Match the ledger reader's newline handling, including a final unterminated line.
    lines = io.StringIO(text, newline="").readlines()
    try:
        return parse_record(lines[record.line - 1].rstrip("\r\n"), record.line) == record
    except (IndexError, ValueError):
        return False


class Refusal(Exception):
    def __init__(self, code: str, detail: str, path: Path | None = None):
        self.code = code
        self.detail = detail
        self.path = path


def refusal(config: Config, path: Path, exc: Refusal) -> Problem:
    return problem(exc.code, "fail", config.where(exc.path or path), exc.detail)


def line_break(text: str) -> str:
    match = re.search(r"\r\n|\n|\r", text)
    return match[0] if match else "\n"


def line_end(text: str, index: int) -> int:
    return sum(len(line) for line in text.splitlines(keepends=True)[:index])


def block_at(text: str, offset: int, lines: list[str]) -> str:
    newline = line_break(text)
    content = newline.join(lines) + newline
    if offset and not text[:offset].endswith(("\r", "\n")):
        content = newline + content
    return text[:offset] + content + text[offset:]


def read_destination(path: Path, *, create: bool = False) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        if create and not path.is_symlink():
            return ""
        raise Refusal("dest_missing", "destination file is missing") from None
    except (OSError, UnicodeError):
        raise Refusal("dest_unreadable", "destination file cannot be read") from None


def stage(path: Path, text: str, validate: Callable[[str], bool]) -> None:
    def verify(temporary: Path) -> None:
        if not validate(temporary.read_bytes().decode("utf-8")):
            raise Refusal("dest_invalid", "updated destination does not parse as expected")
    try:
        write_text(path, text, validate=verify)
    except (OSError, UnicodeError):
        raise Refusal("dest_unreadable", "destination file cannot be written") from None


def one_section(text: str, title: str) -> Section:
    matching = [part for part in sections(text) if part.title.casefold() == title.casefold()]
    return unique_section(matching, "heading_missing")


def unique_section(matching: list[Section], missing: str) -> Section:
    if not matching:
        raise Refusal(missing, "destination section was not found")
    if len(matching) > 1:
        raise Refusal("heading_ambiguous", "multiple sections match the destination")
    return matching[0]


def people(text: str, name: str) -> list[Section]:
    name = name.casefold()
    return [part for part in sections(text) if part.level == 2
            and (part.title.casefold() == name or name in aliases(part))]


def last_nonblank(lines: list[str], end: int) -> int:
    while end and not lines[end - 1].strip():
        end -= 1
    return end


def cites(text: str, identity: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(identity)}(?![\w-])", text) is not None


def note_line(record: Record) -> str:
    who = record.fields.get("who", "user")
    return f"- {record.text} ({who}, {record.day}, {record.fields.get('src', '')}; {record.id})"


def escape_cell(text: str) -> str:
    return " ".join(text.splitlines()).replace("|", r"\|")


def add_pitfall(text: str, path: Path, record: Record, identity: str) -> tuple[str, Callable[[str], bool]]:
    rows, _ = parse_tables(text, str(path))
    matches = [row for row in rows if row.state == identity]
    if not matches:
        raise Refusal("state_missing", "state row was not found")
    if len(matches) > 1:
        raise Refusal("state_ambiguous", "multiple rows have that state id")
    row = matches[0]
    if cites(row.pitfalls, record.id):
        return text, lambda _: True
    index = int(row.where.rsplit(":", 1)[1]) - 1
    start = line_end(text, index)
    raw = text.splitlines()[index]
    separators = list(PIPE.finditer(raw))
    left = 4 if raw.lstrip().startswith("|") else 3
    if len(cells(raw)) != 6 or len(separators) <= left + 1:
        raise Refusal("dest_invalid", "state row has no unambiguous Pitfalls cell")
    first, last = separators[left].end(), separators[left + 1].start()
    cell = raw[first:last]
    value = escape_cell(f"{record.text}, {record.day} ({record.id})")
    if row.pitfalls.casefold() in EMPTY_PITFALLS:
        begin = first + len(cell) - len(cell.lstrip())
        end = first + len(cell.rstrip())
        if begin > end:  # An all-whitespace cell retains all of its padding.
            begin = end = first + (1 if cell else 0)
    else:
        begin = end = first + len(cell.rstrip())
        value = "; " + value
    candidate = text[:start + begin] + value + text[start + end:]

    def verify(updated: str) -> bool:
        parsed, _ = parse_tables(updated, str(path))
        return ([entry.state for entry in parsed] == [entry.state for entry in rows]
                and any(entry.state == identity and cites(entry.pitfalls, record.id) for entry in parsed))
    return candidate, verify


def select_destination(config: Config, dest: str) -> tuple[Path, str, str | None]:
    if not dest or dest.splitlines() != [dest] or any(ord(ch) < 32 or ord(ch) == 127 for ch in dest):
        raise Refusal("dest_invalid", "destination must be nonempty and fit on one line")
    if dest == "drafting" or dest.startswith("card:"):
        return config.people_cards() or config.path("skills"), dest, None
    if dest.startswith("pitfall:"):
        identity = dest.partition(":")[2]
        paths = []
        for path in config.state_machines():
            if not path.is_file():
                continue
            rows, _ = parse_tables(read_destination(path), str(path))
            paths.extend(path for row in rows if row.state == identity)
        if not paths:
            raise Refusal("state_missing", "state row was not found")
        if len(paths) > 1:
            raise Refusal("state_ambiguous", "multiple rows have that state id")
        return paths[0], "pitfall", identity
    name, separator, title = dest.partition("#")
    asked = Path(name) if Path(name).is_absolute() else config.root / name
    if not name:
        raise Refusal("dest_invalid", "destination must be a path under the project root", asked)
    try:
        path = config.relative(name)
    except UsageError:
        raise Refusal("dest_invalid", "destination must be a path under the project root", asked) from None
    resolved = path.resolve()
    forbidden = {
        config.path(key).resolve() for key in ("notes", "commitments", "evidence", "actions", "rules")
    } | {config.relative("workplace.toml").resolve()}
    if (path.suffix.casefold() != ".md" or resolved in forbidden
            or resolved.is_relative_to(config.path("state").resolve())):
        raise Refusal("dest_invalid", "destination path is not an allowed Markdown file", path)
    return path, "heading" if separator else "file", title


def edit_destination(config: Config, path: Path, text: str, mode: str, selector: str | None,
                     record: Record) -> None:
    if mode == "pitfall":
        updated, verify = add_pitfall(text, path, record, selector)
    else:
        def locate(value: str) -> Section | None:
            if mode == "heading":
                return one_section(value, selector)
            if mode == "drafting":
                return unique_section([part for part in sections(value) if part.title.casefold() in DRAFTING_TITLES],
                                      "heading_missing")
            if mode.startswith("card:"):
                return unique_section(people(value, mode.partition(":")[2]), "no_card")
            return None

        part = locate(text)
        if cites(part.body if part else text, record.id):
            return
        offset = line_end(text, last_nonblank(text.splitlines(), part.end)) if part else len(text)
        bullet = note_line(record)
        updated = block_at(text, offset, [bullet])

        def verify(value: str) -> bool:
            try:
                section = locate(value)
            except Refusal:
                return False
            lines = value.splitlines()
            return any(line == bullet and (section is None or section.start < index < section.end)
                       for index, line in visible_lines(lines))

    if updated != text:
        def validate(value: str) -> bool:
            if path.suffix.casefold() == ".toml":
                try:
                    parsed = tomllib.loads(value)
                except ValueError:
                    return False
                if load_rules(config, parsed, value)[1] or load_standing(config, parsed, value)[1]:
                    return False
            return verify(value)

        stage(path, updated, validate)


def find_record(config: Config, identity: str) -> tuple[Record | None, list[Problem]]:
    ledger = read_ledger(config, "note")
    if ledger.problems:
        return None, ledger.problems
    matching = [record for record in ledger.records if record.id == identity]
    if not matching:
        return None, [problem("unknown_id", "fail", config.where(ledger.path), f"no record with id {identity}")]
    if len(matching) != 1:
        return None, [problem("duplicate_id", "fail", config.where(ledger.path), f"duplicate id {identity}")]
    return matching[0], []


def lock_paths(stack: ExitStack, *paths: Path) -> None:
    directories = {path.resolve().parent: path for path in paths}
    for directory in sorted(directories):
        if directory.is_dir():
            stack.enter_context(ledger_lock(directories[directory]))


def file_note(config: Config, identity: str, dest: str) -> tuple[int, dict[str, Any]]:
    record, problems = find_record(config, identity)
    if problems:
        return result(problems)
    if record.checked:
        where = config.where(config.path("notes"), record.line)
        return result([problem("already_filed", "fail", where, f"{identity} is already filed")],
                      record=record.output())
    ledger_path = config.path("notes")
    path = config.path("skills")
    try:
        path, mode, selector = select_destination(config, dest)
        if not path.is_file():
            raise Refusal("dest_missing", "destination file is missing")
        if path.resolve() == ledger_path.resolve():
            raise Refusal("dest_invalid", "the note ledger cannot be its own destination")
        with ExitStack() as stack:
            lock_paths(stack, ledger_path, path)
            record, problems = find_record(config, identity)
            if problems:
                return result(problems)
            text = read_destination(path)
            edit_destination(config, path, text, mode, selector, record)
            return update_record(config, "note", identity, "filed", config.where(path))
    except Refusal as exc:
        argument = shlex.quote(dest) if dest and dest.splitlines() == [dest] and "\0" not in dest else "<destination>"
        return result([refusal(config, path, exc)], record=record.output(),
                      next=f"wm note file {shlex.quote(identity)} --to {argument}")


def add_note(config: Config, fields: dict[str, str], text: str, dest: str | None) -> tuple[int, dict[str, Any]]:
    code, output = add_record(config, "note", fields, text)
    if code or dest is None:
        return code, output
    return file_note(config, output["record"]["id"], dest)


def add_card(config: Config, name: str, role: str | None, names: list[str] | None) -> tuple[int, dict[str, Any]]:
    name = validate_record_text(name, "name").strip()
    if role is not None:
        role = validate_record_text(role, "role").strip()
    for alias in names or []:
        validate_record_text(alias, "alias")
    names = [part.strip() for alias in names or [] for part in re.split(r"[,、，]", alias) if part.strip()]
    for alias in names:
        validate_record_text(alias, "alias")
    path = config.people_cards() or config.path("skills")
    try:
        if not path.is_file():
            raise Refusal("dest_missing", "people-cards file is missing")
        with ledger_lock(path):
            text = read_destination(path)
            identifiers = [name, *names]
            if (len({value.casefold() for value in identifiers}) != len(identifiers)
                    or any(people(text, value) for value in identifiers)):
                raise Refusal("card_exists", "a card or alias already uses that name")
            lines = text.splitlines()
            end = last_nonblank(lines, len(lines))
            if end and lines[end - 1].lstrip().startswith(QUESTIONS) and end - 1 in dict(visible_lines(lines)):
                offset = line_end(text, last_nonblank(lines, end - 1))
            else:
                offset = len(text)
            added = ["", f"## {name}", ""]
            if names:
                added.append(f"Aliases: {', '.join(names)}")
            if role:
                added.append(f"- Role: {role}")
            if offset < len(text) and not text[offset:].startswith(("\r", "\n")):
                added.append("")
            updated = block_at(text, offset, added)
            stage(path, updated, lambda value: len(people(value, name)) == 1)
    except Refusal as exc:
        return result([refusal(config, path, exc)])
    return result(name=name, path=config.where(path))


def toml_value(value: Any) -> str:
    if isinstance(value, str):
        # JSON's ordinary escapes are also TOML basic-string escapes.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", r"\u007f")
    if isinstance(value, list):
        return "[" + ", ".join(map(toml_value, value)) + "]"
    return json.dumps(value)


def add_rule(config: Config, group: str, fields: dict[str, Any],
             from_note: str | None = None) -> tuple[int, dict[str, Any]]:
    identity = fields.get("id")
    if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9._-]+", identity) is None:
        raise UsageError("rule id must use letters, digits, dot, hyphen or underscore")
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for entry in values:
            if isinstance(entry, str):
                validate_written_value(entry, key)
    path = config.path("rules")
    ledger_path = config.path("notes")
    where = config.where(path)
    try:
        path.resolve().parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            lock_paths(stack, *(path, ledger_path) if from_note is not None else (path,))
            record = None
            if from_note is not None:
                record, problems = find_record(config, from_note)
                if problems:
                    return result(problems)
            text = read_destination(path, create=True)
            block = [f"[[{group}]]", *(f"{key} = {toml_value(value)}" for key, value in fields.items())]
            updated = block_at(text, len(text), ([""] if text else []) + block)
            try:
                parsed = tomllib.loads(updated)
            except ValueError:
                return result([problem("bad_rule", "fail", where, "updated rules file is invalid TOML")])
            issues = load_rules(config, parsed, updated)[1] + load_standing(config, parsed, updated)[1]
            failures = [issue for issue in issues if issue["level"] == "fail"]
            if failures:
                return result(failures)
            if not parsed.get(group) or parsed[group][-1] != fields:
                return result([problem("bad_rule", "fail", where, "appended rule did not parse as written")])

            def verify_rule(temporary: Path) -> None:
                value = temporary.read_text(encoding="utf-8")
                loaded = tomllib.loads(value)
                validation = load_rules(config, loaded, value)[1] + load_standing(config, loaded, value)[1]
                if any(issue["level"] == "fail" for issue in validation):
                    raise Refusal("bad_rule", "updated rules file did not validate", path)

            changes = [(path, updated, verify_rule)]
            output = {"rule": {key: value for key, value in fields.items() if key != "check"}, "path": where}
            if record is not None and not record.checked:
                ledger = read_ledger(config, "note")
                ledger_text, filed = record_update_candidate(ledger, record, "filed", where)

                def verify_ledger(temporary: Path) -> None:
                    if not record_matches(temporary.read_text(encoding="utf-8"), filed):
                        raise Refusal("dest_invalid", "updated note ledger did not validate", ledger_path)

                changes.append((ledger_path, ledger_text, verify_ledger))
                output["record"] = filed.output()
            elif record is not None:
                output["record"] = record.output()
            try:
                write_texts(changes)
            except MultiWriteError as exc:
                if isinstance(exc.cause, Refusal):
                    raise exc.cause
                raise Refusal("dest_unreadable", "destination file cannot be written", exc.path) from None
    except Refusal as exc:
        return result([refusal(config, path, exc)])
    return result(**output)


def log_entry(config: Config, text: str) -> tuple[int, dict[str, Any]]:
    text = validate_record_text(text, "text").strip()
    day = config.today().isoformat()
    path = config.relative(str(config.path("worklog") / f"{day}.md"))
    where = config.where(path)
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    entry = f"- {config.now():%H:%M} {text}"
    titles = {config.start_heading.casefold(), "明天從哪開始"}
    try:
        with ledger_lock(path):
            original = read_destination(path, create=True)
            existing = [part for part in sections(original) if part.title.casefold() in titles]
            if len(existing) > 1:
                raise Refusal("heading_ambiguous", "multiple start headings in today's worklog")
            offset = line_end(original, existing[0].start) if existing else len(original)
            lines = [entry] if original else [f"# {day}", "", entry]
            updated = block_at(original, offset, lines)
            entry_index = len(original[:offset].splitlines()) + len(lines) - 1
            stage(path, updated, lambda value: dict(visible_lines(value.splitlines())).get(entry_index) == entry)
    except Refusal as exc:
        return result([refusal(config, path, exc)])
    return result(path=where, entry=entry)


def write_handoff(config: Config, path: Path, lines: list[str], replace: bool) -> list[Problem]:
    titles = {config.start_heading.casefold(), "明天從哪開始"}
    commit_titles = {config.unpushed_heading.casefold(), "未推提交"}
    path.resolve().parent.mkdir(parents=True, exist_ok=True)
    try:
        with ledger_lock(path):
            text = read_destination(path, create=True)
            matches = [part for part in sections(text) if part.title.casefold() in titles]
            if replace and len(matches) > 1:
                raise Refusal("heading_ambiguous", "multiple start headings in today's worklog")
            existing = matches[0] if matches else None
            if existing:
                if not replace:
                    raise Refusal("handoff_exists", "today's worklog already has a start heading")
                prefix = text[:line_end(text, existing.start)]
                added = lines
            else:
                prefix = text
                added = ["", *lines] if text else [f"# {config.today()}", "", *lines]

            def verify(value: str) -> bool:
                headings = {part.title.casefold() for part in sections(value) if part.body}
                return bool(headings & titles and headings & commit_titles)

            stage(path, block_at(prefix, len(prefix), added), verify)
    except Refusal as exc:
        return [refusal(config, path, exc)]
    return []
