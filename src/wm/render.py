"""Render command result objects as concise terminal text."""

from __future__ import annotations

import shlex
from typing import Any

from .writers import EMPTY_PITFALLS

STATUS = {"ok": "ok", "warn": "warn", "fail": "FAIL", "unverified": "UNVERIFIED", "needs_user": "warn"}
ITEM_LABELS = {
    "actionable": "do", "needs_user": "ask", "waiting": "wait", "read_only": "read",
    "unverified": "unverified", "done": "done",
}


def compact(*parts: Any) -> str:
    return " ".join(" ".join(str(value).splitlines()).strip() for value in parts if value not in ("", None))


def problem_line(issue: dict) -> str:
    return compact(*(issue.get(key) for key in ("level", "code", "where", "detail")))


def record_line(record: dict) -> str:
    return compact(record.get("id"), record.get("due"), record.get("text"),
                   "filed " + record["filed"] if record.get("filed") else
                   "done" if record.get("checked") else "")


def action_line(action: dict) -> str:
    return compact(action.get("id"), action.get("state"), action.get("target"))


def item_line(item: dict, *, proof: bool = True) -> str:
    status = item.get("status")
    line = compact(ITEM_LABELS.get(status), item.get("id"), item.get("title"))
    state = compact(item.get("state"), item.get("state_name"))
    if state:
        line += " | " + state
    if status == "waiting":
        line += " | on: " + compact(item.get("waiting_on"))
    if proof and status in ("actionable", "needs_user"):
        if item.get("verify"):
            line += " | verify: " + compact(item["verify"])
        if item.get("pitfalls", "").casefold() not in EMPTY_PITFALLS:
            line += " | pitfall: " + compact(item["pitfalls"])
    return line


def brief(data: dict) -> tuple[str, list[str], str]:
    lines = []
    if worklog := data.get("worklog"):
        lines.extend([f"Start here {worklog['date']}", *worklog["start"].splitlines()] if worklog.get("start") else [])
    for key in ("due", "undated", "unfiled_notes"):
        lines.extend(record_line(record) for record in data.get(key, []))
    lines.extend(action_line(action) for action in data.get("actions", []))
    context = compact(data.get("hostname"), data.get("branch"),
                      f"{data['changed_files']} changed" if data.get("changed_files") is not None else "")
    return context, [line for line in lines if line.strip()], "restore the inputs listed above, then run wm brief"


def next_items(data: dict) -> tuple[str, list[str], str]:
    priority = data.get("now", [])
    lines = []
    for entry in priority:
        if entry.get("status") == "unknown":
            lines.append(action_line(entry))
        elif entry.get("type") == "commitment":
            lines.append(record_line(entry))
        else:
            lines.append(item_line(entry))
    lines.extend(item_line(item) for item in data.get("items", [])
                 if item.get("status") not in ("actionable", "needs_user"))
    return "", lines, "restore or update the evidence listed above, then run wm next"


def lint(data: dict) -> tuple[str, list[str], str]:
    return "", [], "fix the problems listed above, then run wm lint"


def catalog(data: dict) -> tuple[str, list[str], str]:
    qualifiers = {"systems": lambda e: e.get("reach"), "channels": lambda e: e.get("kind"),
                  "commands": lambda e: "write" if e.get("writes") else "read"}
    lines = [compact(key[:-1], entry.get("id"), qualifiers[key](entry), "|", entry.get("what"))
             .removesuffix(" |") for key in qualifiers for entry in data.get(key, [])]
    return "", lines, "fix the registrations listed above, then run wm lint config"


def card_lines(data: dict) -> list[str]:
    lines = []
    if drafting := data.get("before_drafting"):
        lines += ["drafting:", *(line for line in drafting.splitlines() if line.strip())]
    if person := data.get("person"):
        heading, *body = person.splitlines()
        lines += [f"card {heading.lstrip('#').strip()}:", *(line for line in body if line.strip())]
    return lines


def card(data: dict) -> tuple[str, list[str], str]:
    return compact(data.get("name"), data.get("path")), card_lines(data.get("card", {})), "run wm card add NAME or use an existing card name"


def sm(data: dict) -> tuple[str, list[str], str]:
    lines = [compact(flow["id"], flow["title"], f"{flow['first']}-{flow['last']}")
             for flow in data.get("flows", [])]
    for row in data.get("states", []):
        label = compact(row["state"], row["state_name"])
        if row["state"] == data["selection"] and len(data["states"]) == 1:
            lines.extend([label, f"who: {row['trigger']}   next: {row['next']}", f"verify: {row['verify']}",
                          f"pitfalls: {row['pitfalls']}", f"source: {row['source']}"])
        else:
            lines.append(f"{label} | {row['trigger']} | -> {row['next']}")
    return "", lines, "run wm sm to find a flow or state id"


def outbound(data: dict) -> tuple[str, list[str], str]:
    violations = data.get("violations", [])
    lines = []
    for violation in violations:
        source = f"({violation['src']})" if violation["src"] else ""
        lines.append(compact("violation", violation["rule"], violation["check"], violation["message"],
                             source, violation.get("detail")))
    if violations:
        lines.extend(card_lines(data.get("card", {})))
    if any(issue["code"] == "check_unverified" for issue in data["problems"]):
        followup = "restore the rule check and rerun wm outbound check"
    elif any(violation["check"] == "require_receipt" for violation in violations):
        if data.get("receipt_level") == "checked":
            followup = "fix the draft and attach a checked receipt with --receipt <id>"
        else:
            followup = 'fix the draft; for a completion claim add --receipt <id> or --verified "<what you checked and where>"'
    elif violations:
        followup = "fix the draft and rerun wm outbound check"
    elif any(issue["code"] == "no_card" for issue in data["problems"]):
        followup = "add the recipient's card with wm card add NAME"
    else:
        followup = "fix the outbound configuration, then run wm lint rules"
    return "", lines, followup


def action(data: dict, operation: str) -> tuple[str, list[str], str]:
    if "violations" in data:
        return outbound(data)
    entry = data.get("action", {})
    identity = entry.get("id", "")
    state = entry.get("state")
    lines = []
    if data.get("status") == "needs_user":
        lines.extend(card_lines(data.get("card", {})))
        lines.append("payload: " + entry["payload"])
        if lines[-1].endswith("\n"):
            lines[-1] = lines[-1][:-1]
        if entry.get("kind") == "other" and entry.get("command"):
            lines.append("command: " + entry["command"])
        lines.extend(compact("proof", proof.get("id"), proof.get("how", "claimed"), proof.get("what"))
                     for proof in data.get("proof_receipts", []))
        followup = f'show the payload to the user; after their yes: wm action approve {identity} --by "<their words>" --run'
        return compact("needs_user", identity), lines, followup
    for item in data.get("actions", []):
        if item.get("state"):
            lines.append(action_line(item))
        else:
            lines.append(compact(item.get("id"), item.get("status")))
    if entry and operation in ("preview", "show"):
        lines.append("payload: " + entry["payload"].removesuffix("\n"))
        if entry.get("kind") == "other" and entry.get("command"):
            lines.append("command: " + entry["command"])
    codes = {issue["code"] for issue in data["problems"]}
    if "readback_failed" in codes:
        followup = f'check the destination and run wm action readback {identity or "<id>"} --proof "<reference>"'
    elif "unknown" in codes:
        followup = "check the destination and resolve the action with wm action resolve"
    elif "expired" in codes or "action_failed" in codes:
        followup = "create a new preview for the failed or expired action"
    else:
        followup = "review the action with wm action show <id>"
    if state in ("sent", "verified"):
        result_lines = (entry.get("result") or "").splitlines()
        first = result_lines[0] if result_lines else ""
        summary = compact("sent", identity, first[:119] + "…" if len(first) > 120 else first,
                          "verified" if state == "verified" else "")
    else:
        summary = compact(state, identity)
    return summary, lines, followup


def note(data: dict) -> tuple[str, list[str], str]:
    records = [data["record"]] if "record" in data else data.get("notes", [])
    return "", [record_line(record) for record in records], "review the note with wm note list --open"


def commitments(data: dict) -> tuple[str, list[str], str]:
    records = [data["record"]] if "record" in data else data.get("commitments", []) + data.get("undated", [])
    return "", [record_line(record) for record in records], "fix the commitments listed above, then run wm lint records"


def init(data: dict) -> tuple[str, list[str], str]:
    lines = [*data.get("files", []), *(f"kept {path}" for path in data.get("kept", []))]
    return "", lines, "choose an empty directory and run wm init again"


def access(data: dict) -> tuple[str, list[str], str]:
    if any("status" in entry for entry in data.get("systems", [])):
        return "", [compact(entry["id"], entry["status"]) for entry in data["systems"]], "restore access and run wm access check"
    suffix, lines, _ = catalog(data)
    return suffix, lines, "configure a probe and run wm access check"


def doctor(data: dict) -> tuple[str, list[str], str]:
    lines = [compact(entry["key"], entry["path"], "present" if entry["exists"] else "missing")
             for entry in data.get("paths", [])]
    return "", lines, "fix the setup issues listed above and run wm doctor"


def rule(data: dict) -> tuple[str, list[str], str]:
    return compact(data.get("rule", {}).get("id"), data.get("path")), [], "fix the rule and retry the add"


def standing(data: dict) -> tuple[str, list[str], str]:
    return rule(data)


def item(data: dict) -> tuple[str, list[str], str]:
    rows = [data["item"]] if "item" in data else data.get("items", [])
    lines = [compact(row.get("id"), row.get("state"), row.get("title")) for row in rows if isinstance(row, dict)]
    return "", lines, "run wm sm to find a state, then use wm item set"


def receipt(data: dict) -> tuple[str, list[str], str]:
    entry = data.get("receipt", {})
    return compact(entry.get("id"), entry.get("how")), [], "check the evidence or restore the read command, then retry"


def log(data: dict) -> tuple[str, list[str], str]:
    return compact(data.get("path")), [data["entry"]] if "entry" in data else [], "fix today's worklog and run wm log again"


def wrapup(data: dict, operation: str) -> tuple[str, list[str], str]:
    label = data.get("result", "")
    if label in ("FAIL", "UNVERIFIED"):
        label = ""
    if any(issue["code"] == "handoff_exists" for issue in data["problems"]):
        followup = "run wm wrapup check, or repeat wm wrapup write with --replace to replace the handoff"
    elif operation == "write":
        followup = "resolve the problems listed above, then retry wm wrapup write"
    else:
        followup = "fix the problems listed above, then run wm wrapup check"
    return compact(label, data.get("path"), data.get("pending")), [
        line for line in data.get("markdown", "").splitlines() if line.strip()
    ], followup


RENDERERS = {function.__name__: function for function in
             (brief, lint, catalog, card, sm, outbound, note, commitments, init, access, doctor,
              rule, standing, item, receipt, log)}
RENDERERS["next"] = next_items


def render(command: str, operation: str, data: dict) -> str:
    status = data["status"]
    if command == "action":
        label, body, followup = action(data, operation)
    elif command == "wrapup":
        label, body, followup = wrapup(data, operation)
    else:
        formatter = RENDERERS.get(command)
        label, body, followup = formatter(data) if formatter else ("", [], "check the command and try again")
    lines = [compact(STATUS[status], label), *(problem_line(issue) for issue in data["problems"]), *body]
    if status != "ok":
        lines.append("next: " + data.get("next", followup))
    return "\n".join(lines)
