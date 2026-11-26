"""Journal approvals and execute each approved action at most once."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, TextIO

from .catalog import host_problem, load_registry
from .config import Config, Problem, UsageError, problem, reject_json_constant, result, validate_written_value
from .evidence import add_receipt, attached_receipts
from .outbound import check_outbound, read_rules
from .process import shell
from .secrets import has_secret_like, redact_output
from .standing import KINDS, load_standing

OPEN = {"draft", "approved", "unknown", "sent"}
TRANSITIONS = {
    "draft": {None},
    "approved": {"draft"},
    "running": {"approved"},
    "sent": {"running", "approved"},
    "failed": {"running"},
    "verified": {"sent"},
    "cancelled": {"draft", "approved"},
}
DRAFT_FIELDS = ("kind", "target", "to", "receipts", "payload", "sha256", "expires", "command",
                "command_sha256")
EVENT_FIELDS = {
    "draft": DRAFT_FIELDS,
    "approved": ("approved_by",),
    "running": ("target", "command_sha256", "command"),
    "sent": ("result", "proof"),
    "failed": ("exit", "stderr", "proof"),
    "verified": ("proof",),
    "cancelled": (),
}
REQUIRED_FIELDS = {
    "draft": DRAFT_FIELDS,
    "approved": ("approved_by",),
    "verified": ("proof",),
}


class JournalError(ValueError):
    pass


def payload_bytes(payload: str) -> bytes:
    return payload.encode("utf-8", "surrogateescape")


def digest(payload: str) -> str:
    return hashlib.sha256(payload_bytes(payload)).hexdigest()


def fold_event(actions: dict[str, dict[str, Any]], event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise JournalError
    identity, state, at = event.get("id"), event.get("event"), event.get("at")
    if (not isinstance(identity, str) or not re.fullmatch(r"A-[0-9]{8}-[0-9]{2,}", identity)
            or not isinstance(state, str) or state not in TRANSITIONS or not isinstance(at, str)
            or datetime.fromisoformat(at).utcoffset() is None):
        raise JournalError
    old = actions.get(identity, {})
    fields = {key: event[key] for key in EVENT_FIELDS[state] if key in event}
    required = REQUIRED_FIELDS.get(state, ())
    if state in ("sent", "failed"):
        required = ("proof",) if "proof" in fields else ("result",) if state == "sent" else ("exit", "stderr")
    elif state == "running":
        required = ("command",) if old.get("kind") == "other" else ("target", "command_sha256")
    manual_sent = (state == "sent" and old.get("state") == "approved"
                   and old.get("kind") == "other" and not old.get("command") and "proof" in fields)
    if (old.get("state") not in TRANSITIONS[state] or any(key not in fields for key in required)
            or (state == "sent" and old.get("state") == "approved" and not manual_sent)):
        raise JournalError
    if any(not isinstance(value, str) for key, value in fields.items() if key not in ("receipts", "exit")):
        raise JournalError
    action = {"id": identity, **old, **fields, "state": state, "at": at}
    if state == "draft":
        if (action["kind"] not in KINDS or action["sha256"] != digest(action["payload"])
                or not isinstance(action["receipts"], list)
                or not all(isinstance(receipt, str) for receipt in action["receipts"])
                or datetime.fromisoformat(action["expires"]).utcoffset() is None
                or (action["kind"] != "other" and (action["command"] != ""
                                                    or re.fullmatch(r"[0-9a-f]{64}", action["command_sha256"]) is None))
                or (action["kind"] == "other" and action["command_sha256"] != "")):
            raise JournalError
        action["created_at"] = at
    if "exit" in fields and type(fields["exit"]) is not int:
        raise JournalError
    return action


def replay(handle: TextIO) -> dict[str, dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for line in handle:
        if not line.endswith("\n"):
            raise JournalError
        action = fold_event(actions, json.loads(line, parse_constant=reject_json_constant))
        actions[action["id"]] = action
    return actions


@dataclass
class Journal:
    handle: TextIO
    actions: dict[str, dict[str, Any]]
    at: datetime

    def append(self, identity: str, state: str, **fields: Any) -> dict[str, Any]:
        event = {"id": identity, "event": state, "at": self.at.isoformat(), **fields}
        action = fold_event(self.actions, event)
        self.handle.write(json.dumps(event) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.actions[identity] = action
        return action


@contextmanager
def journal(config: Config, *, write: bool = False, create: bool = False) -> Iterator[Journal]:
    path = config.path("actions")
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_APPEND if write else os.O_RDONLY
    if create:
        flags |= os.O_CREAT
    with os.fdopen(os.open(path, flags, 0o600), "a+" if write else "r",
                   encoding="utf-8", newline="\n") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        try:
            handle.seek(0)
            actions = replay(handle)
        except (OSError, ValueError, OverflowError) as exc:
            raise JournalError from exc
        yield Journal(handle, actions, config.now())


def log_problem(config: Config) -> Problem:
    return problem("unreadable", "unverified", config.where(config.path("actions")),
                   "action log cannot be read or written")


def snapshot(config: Config) -> tuple[dict[str, dict[str, Any]], list[Problem]]:
    try:
        with journal(config) as log:
            return log.actions, []
    except FileNotFoundError:
        return {}, [log_problem(config)] if config.path("actions").is_symlink() else []
    except (OSError, ValueError, OverflowError):
        return {}, [log_problem(config)]


def action_state(action: dict[str, Any], now: datetime) -> str:
    state = action["state"]
    if state == "running":
        return "unknown"
    if now.astimezone(timezone.utc) < datetime.fromisoformat(action["created_at"]).astimezone(timezone.utc):
        return "bad_clock"
    if state in {"draft", "approved"} and now.astimezone(timezone.utc) >= datetime.fromisoformat(action["expires"]):
        return "expired"
    return state


def public_action(action: dict[str, Any], now: datetime, *, full: bool = False) -> dict[str, Any]:
    fields = action if full else {key: action[key] for key in ("id", "kind", "target", "to", "created_at", "expires")}
    return {**fields, "state": action_state(action, now)}


def listed_actions(config: Config, open_only: bool = False) -> tuple[list[dict[str, Any]], list[Problem]]:
    actions, problems = snapshot(config)
    now = config.now()
    return [public_action(action, now) for action in actions.values()
            if not open_only or action_state(action, now) in OPEN], problems


def logged_payloads(config: Config, own: str | None = None) -> list[str]:
    entries, _ = snapshot(config)
    return [part for entry in entries.values() if entry["payload"] != own
            for part in (entry["payload"], *entry["payload"].splitlines()) if part]


def state_problem(config: Config, identity: str, state: str | None, *, rerun: bool = False) -> Problem:
    where = config.where(config.path("actions"))
    if state is None:
        return problem("unknown_id", "fail", where, "action id is not in the log")
    if state == "expired":
        return problem("expired", "fail", where, f"{identity} has expired")
    if state == "bad_clock":
        return problem("bad_clock", "fail", where, f"{identity} has a draft time in the future")
    if state == "unknown" and rerun:
        return problem("unknown", "unverified", where, f"{identity} needs manual read-back and resolution")
    return problem("bad_state", "fail", where, f"{identity} is {state}")


def show(config: Config, identity: str) -> tuple[int, dict[str, Any]]:
    actions, problems = snapshot(config)
    if problems:
        return result(problems)
    if identity not in actions:
        return result([state_problem(config, identity, None)])
    return result(action=public_action(actions[identity], config.now(), full=True))


def preview(config: Config, kind: str, target: str, recipient: str, receipts: list[str],
            payload: str, ttl: float, verified: str | None = None,
            command_args: list[str] | None = None) -> tuple[int, dict[str, Any]]:
    command_args = command_args or []
    for field, value in (("target", target), ("recipient", recipient), *(('receipt', value) for value in receipts)):
        validate_written_value(value, field, nonempty=field != "recipient")
    if verified is not None and not verified.strip():
        raise UsageError("--verified needs nonempty proof")
    if verified is not None:
        validate_written_value(verified, "verified proof", nonempty=True)
    if kind != "other" and command_args:
        raise UsageError("-- CMD is allowed only for an other action preview or send")
    command = ""
    if command_args:
        command = command_args[0] if len(command_args) == 1 else shlex.join(command_args)
        validate_written_value(command, "command", nonempty=True)
    if has_secret_like(payload) or (command and has_secret_like(command)):
        return result([problem("secret_like", "fail", config.where(config.path("actions")),
                               "action payload or command contains a possible credential")])
    if kind == "command":
        raw_target = next((entry for entry in config.registrations.get("command", [])
                           if isinstance(entry, dict) and entry.get("id") == target), None)
        if (isinstance(raw_target, dict) and raw_target.get("writes") is True
                and (not isinstance(raw_target.get("run"), str) or not raw_target["run"].strip())):
            raise UsageError("command target has no run command")
    registry, problems = load_registry(config)
    if problems:
        return result(problems)
    outbound = {}
    asserted_only = False
    rules_file = read_rules(config)
    registered_command = ""
    if kind == "chat":
        if target not in registry["channel"]:
            raise UsageError("chat target must be a registered channel")
        if not registry["channel"][target]["send"].strip():
            raise UsageError("chat target has no send command")
        registered_command = registry["channel"][target]["send"]
    elif kind == "command" and not registry["command"].get(target, {}).get("writes"):
        raise UsageError("command target must be a registered write command")
    elif kind == "command":
        registered_command = registry["command"][target]["run"]

    code, checked = check_outbound(config, payload, recipient, target, receipts,
                                   channels=registry["channel"], rules_file=rules_file,
                                   verified=verified, kind=kind)
    if code in (2, 4):
        return code, checked
    outbound = {key: checked[key] for key in ("card", "channel") if key in checked}
    asserted_only = checked.get("receipt_required", False) and not checked["receipt_checked"]
    if "receipt_required" in checked:
        outbound.update(receipt_required=True, receipt_checked=checked["receipt_checked"])
    problems.extend(checked["problems"])

    data, text, issues = rules_file
    approvals, lint = load_standing(config, data, text)
    if issues or any(issue["level"] == "fail" for issue in lint):
        return result(problems + issues + [issue for issue in lint if issue["level"] == "fail"])
    if verified is not None:
        code, proof = add_receipt(config, verified)
        if code:
            return code, proof
        receipts = [*receipts, proof["receipt"]["id"]]
    if asserted_only or verified is not None:
        outbound["proof_receipts"] = [
            {key: receipt[key] for key in ("id", "what", "how") if key in receipt}
            for receipt in attached_receipts(config, receipts)
        ]
    with journal(config, write=True, create=True) as log:
        series = f"A-{log.at.date().strftime('%Y%m%d')}-"
        number = max((int(identity[len(series):]) for identity in log.actions if identity.startswith(series)), default=0)
        identity = f"{series}{number + 1:02d}"
        try:
            expires = (log.at.astimezone(timezone.utc) + timedelta(minutes=ttl)).astimezone(log.at.tzinfo)
        except OverflowError as exc:
            raise UsageError("--ttl is too large") from exc
        action = log.append(identity, "draft", kind=kind, target=target, to=recipient, receipts=receipts,
                            payload=payload, sha256=digest(payload), expires=expires.isoformat(), command=command,
                            command_sha256=(hashlib.sha256(registered_command.encode("utf-8")).hexdigest()
                                            if registered_command else ""))
        standing = next((rule for rule in approvals if rule.matches(kind, target, recipient, payload, log.at.date())), None)
        if kind != "other" and standing and not asserted_only:
            action = log.append(identity, "approved", approved_by=f"standing:{standing.id}")
    code, output = result(problems, action=public_action(action, log.at, full=True), **outbound)
    if asserted_only:
        return 1, {**output, "ok": False, "status": "needs_user"}
    return code, output


def approve(config: Config, ids: list[str], by: str) -> tuple[int, dict[str, Any]]:
    if not by.strip():
        raise UsageError("--by needs nonempty approval text")
    validate_written_value(by, "approval", nonempty=True)
    approvals, problems = [], []
    # An empty log needs no file to report missing IDs.
    try:
        with journal(config, write=True) as log:
            for identity in ids:
                action = log.actions.get(identity)
                state = action_state(action, log.at) if action else None
                if state != "draft":
                    issue = state_problem(config, identity, state)
                    problems.append(issue)
                    approvals.append({"id": identity, **result([issue])[1]})
                else:
                    approved = log.append(identity, "approved", approved_by=by)
                    approvals.append({**public_action(approved, log.at), **result()[1]})
    except FileNotFoundError:
        if config.path("actions").is_symlink():
            raise
        problems = [state_problem(config, identity, None) for identity in ids]
        approvals = [{"id": identity, **result([issue])[1]} for identity, issue in zip(ids, problems)]
    return result(problems, actions=approvals)


def transition(config: Config, identity: str, states: set[str], event: str,
               **fields: Any) -> tuple[int, dict[str, Any]]:
    for key, value in fields.items():
        if isinstance(value, str):
            validate_written_value(value, key, nonempty=True)
    try:
        with journal(config, write=True) as log:
            action = log.actions.get(identity)
            state = action_state(action, log.at) if action else None
            if state not in states:
                return result([state_problem(config, identity, state)])
            updated = log.append(identity, event, **fields)
            return result(action=public_action(updated, log.at, full=True))
    except FileNotFoundError:
        if config.path("actions").is_symlink():
            raise
        return result([state_problem(config, identity, None)])


def resolve(config: Config, identity: str, state: str, proof: str) -> tuple[int, dict[str, Any]]:
    validate_written_value(proof, "proof", nonempty=True)
    try:
        with journal(config, write=True) as log:
            action = log.actions.get(identity)
            current = action_state(action, log.at) if action else None
            manual_other = bool(action and action["kind"] == "other" and not action["command"]
                                and current == "approved" and state == "sent")
            if current != "unknown" and not manual_other:
                return result([state_problem(config, identity, current)])
            updated = log.append(identity, state, proof=proof)
            return result(action=public_action(updated, log.at, full=True))
    except FileNotFoundError:
        if config.path("actions").is_symlink():
            raise
        return result([state_problem(config, identity, None)])


def run_action(config: Config, identity: str, timeout: float | None) -> tuple[int, dict[str, Any]]:
    try:
        with journal(config, write=True) as log:
            action = log.actions.get(identity)
            state = action_state(action, log.at) if action else None
            if state != "approved":
                return result([state_problem(config, identity, state, rerun=True)])
            registry, problems = load_registry(config)
            if problems:
                return result(problems)
            kind, target = action["kind"], action["target"]
            entry = (registry.get("channel" if kind == "chat" else "command", {}).get(target, {})
                     if kind != "other" else {})
            if entry and (issue := host_problem(registry, entry)):
                return result([issue])
            command = action["command"] if kind == "other" else entry.get("send" if kind == "chat" else "run", "")
            if not command.strip():
                raise UsageError("no command is available for the action")
            command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()
            if kind != "other" and command_sha256 != action["command_sha256"]:
                return result([problem("command_changed", "fail", "workplace.toml",
                                       "registered action command changed after preview")])
            payload = action["payload"]
            env = dict(os.environ, WM_ACTION_ID=identity, WM_KIND=kind, WM_TARGET=target,
                       WM_TO=action["to"], WM_PAYLOAD_SHA256=action["sha256"])
            running = ({"command": command} if kind == "other" else
                       {"target": target, "command_sha256": command_sha256})
            log.append(identity, "running", **running)
    except FileNotFoundError:
        if config.path("actions").is_symlink():
            raise
        return result([state_problem(config, identity, None)])

    try:
        execution = shell(config.root, command, timeout if timeout is not None else 60,
                          payload=payload_bytes(payload), env=env, capture=True)
    except (KeyboardInterrupt, OSError):
        return result([state_problem(config, identity, "unknown", rerun=True)])
    exit_code = execution.exit
    # A shell can report a child's signal using 128 + signal instead of a negative return code.
    if exit_code is None or exit_code < 0 or exit_code - 128 in signal.valid_signals():
        return result([state_problem(config, identity, "unknown", rerun=True)])
    with journal(config, write=True) as log:
        action = log.actions[identity]
        if action["state"] != "running":
            return result([state_problem(config, identity, action["state"])])
        stream = execution.stderr if exit_code else execution.stdout
        diagnostic = stream.decode("utf-8", "replace")
        for other in log.actions.values():
            if other["payload"] and other["payload"] != payload:
                diagnostic = diagnostic.replace(other["payload"], "[redacted]")
        if kind == "other" and command in diagnostic:
            diagnostic = diagnostic.replace(command, "[redacted]")
        diagnostic = redact_output(config, diagnostic)[:2000]
        if exit_code:
            action = log.append(identity, "failed", exit=exit_code, stderr=diagnostic)
            problems = [problem("action_failed", "fail", config.where(config.path("actions")),
                                f"adapter exited {exit_code}")]
        else:
            action = log.append(identity, "sent", result=diagnostic)
            problems = []
        output = result(problems, action=public_action(action, log.at, full=True))

    readback = entry.get("readback", "")
    if exit_code or not readback.strip():
        return output
    token = diagnostic.strip()  # an id is an id with or without the line break the adapter printed after it
    env = dict(os.environ, WM_RESULT=token, WM_ACTION_ID=identity, WM_TARGET=target)
    try:
        checked = shell(config.root, readback, 30, env=env, capture=True, payload=b"")
    except (OSError, ValueError):
        checked = None
    matched = (checked is not None and checked.exit == 0 and bool(token)
               and diagnostic == execution.stdout.decode("utf-8", "replace")[:2000]
               and token in checked.stdout.decode("utf-8", "replace"))
    return finish_readback(config, identity, matched)


def finish_readback(config: Config, identity: str, matched: bool) -> tuple[int, dict[str, Any]]:
    with journal(config, write=True) as log:
        action = log.actions[identity]
        if action["state"] == "sent" and matched:
            action = log.append(identity, "verified", proof="readback:auto")
        problems = []
        if action["state"] == "sent":
            problems.append(problem("readback_failed", "warn", config.where(config.path("actions")),
                                    f"{identity} needs read-back"))
        return result(problems, action=public_action(action, log.at, full=True))


def approve_and_run(config: Config, ids: list[str], by: str) -> tuple[int, dict[str, Any]]:
    _, approval = approve(config, ids, by)
    problems = list(approval["problems"])
    for entry in approval["actions"]:
        if entry["ok"]:
            try:
                _, run = run_action(config, entry["id"], None)
            except UsageError as exc:
                _, run = result([problem("action_not_run", "fail", config.where(config.path("actions")), str(exc))])
            problems.extend(run["problems"])
            # Keep the approval and report the run separately, including its own status.
            if "action" not in run:
                _, latest = show(config, entry["id"])
                if "action" in latest:
                    run["action"] = latest["action"]
            if "action" in run:
                entry["state"] = run["action"]["state"]
                # Batch reports are summaries, like action list.
                run["action"] = {key: run["action"][key] for key in
                                 ("id", "state", "result", "proof", "exit") if key in run["action"]}
            entry["run"] = run
    return result(problems, actions=approval["actions"])
