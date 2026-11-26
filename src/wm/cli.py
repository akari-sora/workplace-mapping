"""Command-line arguments, command dispatch, and Claude Code hook adapters."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from contextlib import suppress
from datetime import timedelta
from typing import Any

from . import actions
from .access import check_access
from .brief import as_text, brief
from .catalog import catalog, run_registered
from .config import (BadValue, Config, UsageError, load_config, print_json, problem, reject_json_constant,
                     result, usage_result, validate_written_value)
from .doctor import doctor
from .evidence import add_receipt, list_items, set_item
from .init import init
from .lint import lint
from .next import next_items
from .outbound import check_outbound, show_card
from .records import KINDS, filtered_commitments, parse_due, read_ledger
from .render import render
from .secrets import redact_output
from .standing import KINDS as ACTION_KINDS, STANDING_KINDS
from .tables import state_machine
from .watch import watch
from .wrapup import pending_path, wrapup_begin, wrapup_check, wrapup_draft, wrapup_write
from .writers import add_card, add_note, add_record, add_rule, file_note, log_entry, mark_record


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:"):
            message = "unrecognized arguments"
        elif ": invalid choice:" in message:
            message = message.split(": invalid choice:", 1)[0] + ": invalid choice"
        raise UsageError(message)


class HookError(ValueError):
    pass


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("expected a positive number")
    return seconds


def positive_int(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        count = 0
    if count <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return count


def days(value: str) -> int:
    if not re.fullmatch(r"[0-9]+d", value):
        raise argparse.ArgumentTypeError("expected a duration such as 2d")
    try:
        return timedelta(days=int(value[:-1])).days
    except OverflowError as exc:
        raise argparse.ArgumentTypeError("duration is too large") from exc


def regex(value: str) -> re.Pattern[str]:
    try:
        return re.compile(value)
    except (re.error, OverflowError, RecursionError) as exc:
        raise argparse.ArgumentTypeError("invalid regex") from exc


def csv(value: str) -> list[str]:
    validate_written_value(value, "list value")
    return [part.strip() for part in re.split(r"[,、，]", value) if part.strip()]


def rule_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]+", value) is None:
        raise argparse.ArgumentTypeError("expected letters, digits, dot, hyphen or underscore")
    return value


def configure_notes(note: Parser, common: Parser) -> None:
    notes = note.add_subparsers(dest="action", required=True)
    new_note = notes.add_parser("add", parents=[common], allow_abbrev=False)
    new_note.add_argument("--kind", default="correction", choices=KINDS)
    new_note.add_argument("--src", default="user, in session")
    new_note.add_argument("--who", default="user")
    new_note.add_argument("--to")
    new_note.add_argument("text")
    list_notes = notes.add_parser("list", parents=[common], allow_abbrev=False)
    list_notes.add_argument("--open", action="store_true")
    list_notes.add_argument("--kind", choices=KINDS)
    file_note = notes.add_parser("file", parents=[common], allow_abbrev=False)
    file_note.add_argument("id")
    file_note.add_argument("--to", required=True)


def configure_commitments(commitments: Parser, common: Parser) -> None:
    commits = commitments.add_subparsers(dest="action", required=True)
    new_commitment = commits.add_parser("add", parents=[common], allow_abbrev=False)
    new_commitment.add_argument("--to", required=True)
    new_commitment.add_argument("--src", required=True)
    new_commitment.add_argument("--due")
    new_commitment.add_argument("--ball", choices=("us", "them"), default="us")
    new_commitment.add_argument("text")
    done = commits.add_parser("done", parents=[common], allow_abbrev=False)
    done.add_argument("id")
    done.add_argument("--proof", required=True)
    list_commits = commits.add_parser("list", parents=[common], allow_abbrev=False)
    list_commits.add_argument("--open", action="store_true")
    list_commits.add_argument("--due-within", type=days)


def configure_outbound(outbound: Parser, common: Parser) -> None:
    outbound_actions = outbound.add_subparsers(dest="action", required=True)
    check = outbound_actions.add_parser("check", parents=[common], allow_abbrev=False)
    check.add_argument("--to", required=True)
    check.add_argument("--channel")
    check.add_argument("--receipt", action="append", default=[])
    check.add_argument("--verified")
    check.add_argument("--file")
    hook = outbound_actions.add_parser("hook", parents=[common], allow_abbrev=False)
    hook.add_argument("--text-field", required=True)
    hook.add_argument("--to-field")
    hook.add_argument("--channel-field")
    hook.add_argument("--receipt-field")


def configure_actions(parser: Parser, common: Parser) -> None:
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("preview", "send", "approve", "run", "readback", "resolve", "cancel", "show", "list"):
        sub = subparsers.add_parser(name, parents=[common], allow_abbrev=False)
        if name in ("preview", "send"):
            sub.add_argument("--kind", required=True, choices=ACTION_KINDS)
            sub.add_argument("--target", required=True)
            sub.add_argument("--to", default="")
            sub.add_argument("--receipt", action="append", default=[])
            sub.add_argument("--verified")
            sub.add_argument("--file")
            sub.add_argument("--ttl", type=positive_seconds, default=30, metavar="MIN")
        elif name == "list":
            sub.add_argument("--open", action="store_true")
        elif name == "approve":
            sub.add_argument("ids", nargs="+")
            sub.add_argument("--by", required=True)
            sub.add_argument("--run", action="store_true")
        else:
            sub.add_argument("id")
        if name in ("run", "send"):
            sub.add_argument("--timeout", type=positive_seconds)
        if name in ("readback", "resolve"):
            sub.add_argument("--proof", required=True)
        if name == "resolve":
            sub.add_argument("--state", required=True, choices=("sent", "failed"))


def configure_writers(commands: Any, common: Parser) -> None:
    for name in ("rule", "standing"):
        group = commands.add_parser(name, parents=[common], allow_abbrev=False)
        subs = group.add_subparsers(dest="action", required=True)
        add = subs.add_parser("add", parents=[common], allow_abbrev=False)
        add.add_argument("--id", required=True, type=rule_id)
        add.add_argument("--to", type=csv)
        add.add_argument("--max-chars", type=int)
        if name == "rule":
            source = add.add_mutually_exclusive_group(required=True)
            source.add_argument("--src")
            source.add_argument("--from", dest="from_note")
            add.add_argument("--message", required=True)
            for key in ("when", "require", "forbid", "check"):
                add.add_argument("--" + key)
            add.add_argument("--channels", type=csv)
            add.add_argument("--kinds", type=csv)
            add.add_argument("--require-receipt", nargs="?", const=True, choices=("claimed", "checked"))
            add.add_argument("--require-mention", action="store_true", default=None)
        else:
            add.add_argument("--src", required=True)
            add.add_argument("--kind", choices=STANDING_KINDS, required=True)
            add.add_argument("--targets", type=csv, required=True)
            add.add_argument("--match")
            add.add_argument("--expires")

    items = commands.add_parser("item", parents=[common], allow_abbrev=False)
    subs = items.add_subparsers(dest="action", required=True)
    for name in ("set", "done", "list"):
        sub = subs.add_parser(name, parents=[common], allow_abbrev=False)
        if name != "list":
            sub.add_argument("id")
            sub.add_argument("--evidence")
        if name == "set":
            sub.add_argument("--state")
            sub.add_argument("--title")
            sub.add_argument("--force", action="store_true")

    receipt = commands.add_parser("receipt", parents=[common], allow_abbrev=False)
    add = receipt.add_subparsers(dest="action", required=True).add_parser("add", parents=[common], allow_abbrev=False)
    add.add_argument("what")
    add.add_argument("--item")
    add.add_argument("--by-cmd")
    add.add_argument("--expect", type=regex)
    log = commands.add_parser("log", parents=[common], allow_abbrev=False)
    log.add_argument("text")


def make_parser() -> Parser:
    common = Parser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, metavar="PATH")
    common.add_argument("--now", default=argparse.SUPPRESS, metavar="ISO8601")
    common.add_argument("--pretty", default=argparse.SUPPRESS, action="store_true")
    common.add_argument("--json", default=argparse.SUPPRESS, action="store_true")
    parser = Parser(prog="wm", parents=[common], allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("init", parents=[common], allow_abbrev=False)
    setup.add_argument("--name", required=True)
    setup.add_argument("--lang", choices=("en", "zh-TW"), default="en")
    setup.add_argument("--dir", default=".")

    configure_notes(commands.add_parser("note", parents=[common], allow_abbrev=False), common)
    configure_commitments(commands.add_parser("commitments", parents=[common], allow_abbrev=False), common)
    configure_outbound(commands.add_parser("outbound", parents=[common], allow_abbrev=False), common)
    configure_actions(commands.add_parser("action", parents=[common], allow_abbrev=False), common)
    configure_writers(commands, common)
    card = commands.add_parser("card", parents=[common], allow_abbrev=False)
    card.add_argument("name_or_add", metavar="NAME|add")
    card.add_argument("name", nargs="?")
    card.add_argument("--role")
    card.add_argument("--aliases", type=csv)
    sm = commands.add_parser("sm", parents=[common], allow_abbrev=False)
    sm.add_argument("id", nargs="?")
    catalogue = commands.add_parser("catalog", parents=[common], allow_abbrev=False)
    catalogue.add_argument("--kind", choices=("commands", "channels", "systems"))
    registered = commands.add_parser("run", parents=[common], allow_abbrev=False)
    registered.add_argument("id")
    registered.add_argument("--full", action="store_true")
    access = commands.add_parser("access", parents=[common], allow_abbrev=False)
    access_actions = access.add_subparsers(dest="action", required=True)
    access_actions.add_parser("list", parents=[common], allow_abbrev=False)
    probe = access_actions.add_parser("check", parents=[common], allow_abbrev=False)
    probe.add_argument("ids", nargs="*")
    probe.add_argument("--timeout", type=positive_seconds, default=10)
    checks = commands.add_parser("lint", parents=[common], allow_abbrev=False)
    checks.add_argument("check", nargs="?", choices=("records", "state-machines", "rules", "secrets", "global", "config"))
    next_cmd = commands.add_parser("next", parents=[common], allow_abbrev=False)
    next_cmd.add_argument("--all", action="store_true")

    handoff = commands.add_parser("wrapup", parents=[common], allow_abbrev=False)
    handoffs = handoff.add_subparsers(dest="action", required=True)
    for action in ("begin", "check", "draft"):
        handoffs.add_parser(action, parents=[common], allow_abbrev=False)
    write = handoffs.add_parser("write", parents=[common], allow_abbrev=False)
    write.add_argument("--start", required=True)
    write.add_argument("--note", action="append", default=[])
    write.add_argument("--replace", action="store_true")
    brief_cmd = commands.add_parser("brief", parents=[common], allow_abbrev=False)
    brief_cmd.add_argument("--hook", action="store_true")
    commands.add_parser("stop-gate", parents=[common], allow_abbrev=False)

    watch_cmd = commands.add_parser("watch", parents=[common], allow_abbrev=False)
    watch_cmd.add_argument("--name", required=True)
    watch_cmd.add_argument("--probe", required=True)
    watch_cmd.add_argument("--every", type=positive_seconds, default=60)
    watch_cmd.add_argument("--timeout", type=positive_seconds, default=3600)
    watch_cmd.add_argument("--done", type=regex)
    watch_cmd.add_argument("--warn-after", type=positive_int, default=3)
    watch_cmd.add_argument("--state")

    commands.add_parser("doctor", parents=[common], allow_abbrev=False)
    return parser


def list_notes(config: Config, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    ledger = read_ledger(config, "note")
    selected = [
        record.output() for record in ledger.records
        if (not args.open or not record.checked) and (not args.kind or record.fields.get("kind") == args.kind)
    ]
    return result(ledger.problems, notes=selected)


def notes_command(config: Config, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.action == "add":
        fields = {"kind": args.kind, "src": args.src, "who": args.who}
        return add_note(config, fields, args.text, args.to)
    if args.action == "file":
        return file_note(config, args.id, args.to)
    return list_notes(config, args)


def commitments_command(config: Config, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.action == "add":
        if args.due is not None:
            try:
                parse_due(args.due)
            except ValueError as exc:
                raise UsageError("--due must be a valid YYYY-MM-DD date") from exc
        fields = {"due": args.due or "none", "to": args.to, "ball": args.ball, "src": args.src}
        return add_record(config, "commitment", fields, args.text)
    if args.action == "done":
        return mark_record(config, "commitment", args.id, args.proof)
    ledger = read_ledger(config, "commitment")
    try:
        cutoff = None if args.due_within is None else config.today() + timedelta(days=args.due_within)
    except OverflowError as exc:
        raise UsageError("due-within date is out of range") from exc
    dated, undated = filtered_commitments(ledger.records, open_only=args.open, cutoff=cutoff)
    return result(ledger.problems, commitments=[record.output() for record in dated],
                  undated=[record.output() for record in undated])


def read_draft(config: Config, filename: str | None) -> str:
    if filename is not None:
        data = config.relative(filename).read_bytes()
    else:
        stream = getattr(sys.stdin, "buffer", None)
        data = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
    return data.decode("utf-8")


def action_command(config: Config, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    name = args.action
    try:
        if name in ("preview", "send"):
            code, output = actions.preview(config, args.kind, args.target, args.to, args.receipt,
                                           read_draft(config, args.file), args.ttl,
                                           args.verified, args.extra)
            if name == "preview" or "action" not in output:
                return code, output
            action = output["action"]
            if action["state"] != "approved":
                return 1, {**output, "ok": False, "status": "needs_user"}
            code, sent = actions.run_action(config, action["id"], args.timeout)
            if "action" not in sent:
                _, latest = actions.show(config, action["id"])
                sent["action"] = latest.get("action", action)
            context = ("card", "channel", "proof_receipts", "receipt_required", "receipt_checked")
            return code, {**{key: output[key] for key in context if key in output}, **sent}
        if name == "approve":
            if args.run:
                return actions.approve_and_run(config, args.ids, args.by)
            return actions.approve(config, args.ids, args.by)
        if name == "run":
            return actions.run_action(config, args.id, args.timeout)
        if name == "readback":
            return actions.transition(config, args.id, {"sent"}, "verified", proof=args.proof)
        if name == "resolve":
            return actions.resolve(config, args.id, args.state, args.proof)
        if name == "cancel":
            return actions.transition(config, args.id, {"draft", "approved"}, "cancelled")
        if name == "show":
            return actions.show(config, args.id)
        entries, problems = actions.listed_actions(config, args.open)
        return result(problems, actions=entries)
    except actions.JournalError:
        return result([actions.log_problem(config)])


def run_json_command(config: Config, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "note":
        return notes_command(config, args)
    if args.command == "commitments":
        return commitments_command(config, args)
    if args.command == "lint":
        return lint(config, args.check)
    if args.command == "next":
        return next_items(config, args.all)
    if args.command == "catalog":
        return catalog(config, args.kind)
    if args.command == "card":
        if args.name_or_add == "add" and args.name is not None:
            return add_card(config, args.name, args.role, args.aliases)
        if args.name is not None or args.role is not None or args.aliases is not None:
            raise UsageError("use wm card NAME or wm card add NAME")
        return show_card(config, args.name_or_add)
    if args.command == "sm":
        return state_machine(config, args.id)
    if args.command in ("rule", "standing"):
        keys = (("id", "message", "src", "when", "kinds", "channels", "to", "require", "forbid", "require_receipt",
                 "require_mention", "max_chars", "check") if args.command == "rule" else
                ("id", "kind", "targets", "src", "to", "match", "max_chars", "expires"))
        fields = {key: getattr(args, key) for key in keys if getattr(args, key) is not None}
        source = getattr(args, "from_note", None)
        if source is not None:
            fields["src"] = source
        return add_rule(config, "outbound" if args.command == "rule" else "standing", fields, source)
    if args.command == "item":
        if args.action == "list":
            return list_items(config)
        if args.action == "done":
            return set_item(config, args.id, "done", None, args.evidence, False)
        if args.state is None and args.title is None and args.evidence is None:
            raise UsageError("wm item set needs --state, --title or --evidence")
        return set_item(config, args.id, args.state, args.title, args.evidence, args.force)
    if args.command == "receipt":
        if args.by_cmd is None and (args.expect is not None or args.extra):
            raise UsageError("--expect and command arguments require --by-cmd")
        if args.expect is not None and not args.expect.pattern:
            raise UsageError("--expect must be nonempty and match at least one character")
        return add_receipt(config, args.what, args.item, args.by_cmd, args.expect, args.extra)
    if args.command == "log":
        return log_entry(config, args.text)
    if args.command == "access":
        return catalog(config, "systems") if args.action == "list" else check_access(config, args.ids, args.timeout)
    if args.command == "action":
        return action_command(config, args)
    if args.command == "outbound":
        draft = read_draft(config, args.file)
        if args.verified is not None and not args.verified.strip():
            raise UsageError("--verified needs nonempty proof")
        return check_outbound(config, draft, args.to, args.channel, args.receipt, verified=args.verified)
    if args.command == "wrapup":
        if args.action == "write":
            return wrapup_write(config, args.start, args.note, args.replace)
        action = {"begin": wrapup_begin, "check": wrapup_check, "draft": wrapup_draft}
        return action[args.action](config)
    if args.command == "brief":
        return brief(config)
    return doctor(config)


def command_tokens(argv: list[str]) -> list[str]:
    tokens = []
    values = iter(argv)
    for token in values:
        if token == "--":
            break
        if token in ("--root", "--now"):
            next(values, None)
        elif token not in ("--pretty", "--json") and not token.startswith(("--root=", "--now=")):
            tokens.append(token)
    return tokens


def hook_mode(argv: list[str]) -> str | None:
    tokens = command_tokens(argv)
    if not tokens:
        return None
    if tokens[0] == "stop-gate":
        return "stop"
    if tokens[0] == "brief" and "--hook" in tokens[1:]:
        return "brief"
    if tokens[:2] == ["outbound", "hook"]:
        return "outbound"
    return None


def hook_input(optional: bool = False) -> dict[str, Any]:
    if optional and sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if optional and not raw.strip():
        return {}
    data = json.loads(raw, parse_constant=reject_json_constant)
    if not isinstance(data, dict):
        raise HookError("hook input must be a JSON object")
    return data


def hook_string(data: dict[str, Any], key: str | None, default: str | None = None) -> str | None:
    value = data.get(key, default) if key else default
    if value is not None and not isinstance(value, str):
        raise HookError("hook field must contain a string")
    return value


def outbound_hook(config: Config, data: dict[str, Any], args: argparse.Namespace) -> int:
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        raise HookError("tool_input must be a JSON object")
    draft = hook_string(tool_input, args.text_field)
    if draft is None:
        raise HookError("tool_input is missing the draft text field")
    recipient = hook_string(tool_input, args.to_field, "") or ""
    channel = hook_string(tool_input, args.channel_field)
    receipts = tool_input.get(args.receipt_field, []) if args.receipt_field else []
    if isinstance(receipts, str):
        receipts = [receipts]
    if not isinstance(receipts, list) or any(not isinstance(item, str) for item in receipts):
        raise HookError("receipt field must contain a string or an array of strings")
    _, output = check_outbound(config, draft, recipient, channel, receipts)
    if output["violations"]:
        violations = redact_output(config, output["violations"], private=actions.logged_payloads(config))
        print(json.dumps(violations, ensure_ascii=False), file=sys.stderr)
        return 2
    for issue in output["problems"]:
        if issue["level"] != "warn" and issue["code"] != "outbound_violation":
            raise HookError(f"{issue['code']} {issue['where']}: {issue['detail']}")
    return 0


def brief_output(text: str, pretty: bool = False) -> None:
    print_json({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}, pretty)


def stop_hook(config: Config, pretty: bool) -> int:
    if pending_path(config).exists():
        code, output = wrapup_check(config)
        if code in (2, 4):
            reason = "\n".join(f"{issue['code']} {issue['where']}: {issue['detail']}"
                               for issue in output["problems"])
            reason = redact_output(config, reason, private=actions.logged_payloads(config))
            print_json({"decision": "block", "reason": reason}, pretty)
    return 0


def run_hook(mode: str, argv: list[str]) -> int:
    try:
        args = make_parser().parse_args(argv)
        data = hook_input(optional=mode == "brief")
        if mode == "stop" and data.get("stop_hook_active") is True:
            return 0
        config = load_config(getattr(args, "root", None), getattr(args, "now", None))
        pretty = getattr(args, "pretty", False)
        if mode == "brief":
            _, output = brief(config)
            brief_output(redact_output(config, as_text(output), private=actions.logged_payloads(config)), pretty)
            return 0
        if mode == "stop":
            return stop_hook(config, pretty)
        return outbound_hook(config, data, args)
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code == 0:
            return 0
        message = " ".join(str(exc).splitlines()) or type(exc).__name__
        error = f"wm {mode} hook: {message}"
        with suppress(BaseException):
            print(error, file=sys.stderr)
        if mode == "brief":
            with suppress(BaseException):
                brief_output(error, "--pretty" in argv)
        return 0


def run(args: argparse.Namespace) -> int:
    pretty = getattr(args, "pretty", False)
    if args.command == "init":
        code, output = init(args.name, args.lang, args.dir)
    else:
        config = load_config(getattr(args, "root", None), getattr(args, "now", None))
        if args.command == "run":
            return run_registered(config, args.id, args.extra, pretty, args.full)
        if args.command == "watch":
            validate_written_value(args.name, "--name", nonempty=True)
            if (args.state is not None
                    and (re.fullmatch(r"[A-Za-z0-9._-]+", args.state) is None or args.state in {".", ".."})):
                raise UsageError("--state must use letters, digits, dot, hyphen or underscore")
            state = (config.path("state") / "watch" / args.state) if args.state is not None else None
            if state is not None and (state.is_symlink()
                                      or state.parent.resolve() != config.path("state").resolve() / "watch"):
                # a link planted in the state folder must not turn the watch into a writer of some other file
                raise UsageError("--state must name a plain file inside the state folder's watch directory")
            return watch(config, args.name, args.probe, args.every, args.timeout, args.done, args.warn_after, state)
        code, output = run_json_command(config, args)
        output = redact_result(config, output)
    print_result(output, args.command, getattr(args, "action", ""), getattr(args, "json", False), pretty)
    return code


def redact_result(config: Config, data: dict) -> dict:
    own = data.get("action", {}).get("payload")
    return redact_output(config, data, private=actions.logged_payloads(config, own))


def print_result(output: dict, command: str, operation: str, as_json: bool, pretty: bool) -> None:
    if as_json or pretty or command in ("run", "watch"):
        print_json(output, pretty)
    else:
        rendered = render(command, operation, output).encode("utf-8", "backslashreplace").decode("utf-8")
        print(rendered)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = hook_mode(argv)
    if mode is not None:
        return run_hook(mode, argv)
    try:
        tokens = command_tokens(argv)
        command = tokens[0] if tokens else ""
        extra = []
        if "--" in argv and (command == "run" or tokens[:2] in (
            ["action", "preview"], ["action", "send"], ["receipt", "add"],
        )):
            split = argv.index("--")
            extra = argv[split + 1:]
            argv = argv[:split]
        args = make_parser().parse_args(argv)
        args.extra = extra
        return run(args)
    except BadValue as exc:
        code, output = result([problem("bad_value", "fail", "wm", str(exc))])
        print_result(output, command, "", "--json" in argv, "--pretty" in argv)
        return code
    except UsageError as exc:
        print_result(usage_result(str(exc)), command, "", "--json" in argv, "--pretty" in argv)
        return 64
    except (OSError, UnicodeError):
        code, output = result([problem("unreadable", "unverified", "wm", "project input or output could not be accessed")])
        print_result(output, command, "", "--json" in argv, "--pretty" in argv)
        return code
