"""Verify the one-call workflows and byte-preserving writers."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_wm import CARDS, COMMIT, CONFIG, EN, NOW, REPO, SHIM, cli
from wm.tables import parse_tables


class ThirdPassTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-third-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = dict(os.environ, WM_HOSTNAME="third-pass", PYTHONDONTWRITEBYTECODE="1",
                        GIT_CEILING_DIRECTORIES=str(self.root.parent), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=str(self.root / "absent-global-config"))
        self.config()
        self.machine = ".agents/skills/work/state-machines.md"
        self.cards = ".agents/skills/work/people-cards.md"
        self.day = self.root / "worklog/2026-03-12.md"
        self.notes = self.root / "worklog/inbox.md"
        self.receipts = self.root / "data/evidence.json"
        self.actions = self.root / ".wm/actions.jsonl"
        self.rules = self.root / "rules.toml"

    def write(self, relative: str | Path, data: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def config(self, registrations="") -> None:
        self.write("workplace.toml", CONFIG + registrations)

    def argv(self, *args, now=NOW):
        return [str(SHIM), "--root", str(self.root), "--now", now, *args]

    def invoke(self, *args, payload="", now=NOW):
        return subprocess.run(self.argv(*args, now=now), cwd=self.root, env=self.env,
                              input=payload, text=True, capture_output=True, timeout=10)

    def command(self, *args, code=0, **kwargs):
        proc = self.invoke("--json", *args, **kwargs)
        self.assertEqual((proc.returncode, proc.stderr), (code, ""), proc.stdout)
        return json.loads(proc.stdout)

    def assert_text_failure(self, proc, code, *details):
        self.assertEqual((proc.returncode, proc.stderr), (code, ""), proc.stdout)
        self.assertTrue(proc.stdout.splitlines()[-1].startswith("next: "), proc.stdout)
        for detail in details:
            self.assertIn(detail, proc.stdout)

    def assert_inserted(self, path, before, offset, inserted):
        after = path.read_bytes()
        self.assertEqual(after[:offset], before[:offset])
        self.assertEqual(after[offset:offset + len(inserted)], inserted)
        self.assertEqual(after[offset + len(inserted):], before[offset:])

    def test_note_destination_insertions_are_byte_exact(self):
        for eol, final in ((b"\r\n", b"\r\n"), (b"\n", b"")):
            for dest in ("card:lead", "drafting", "guide.md#Rules", "whole.md", "pitfall:F1"):
                with self.subTest(newline=eol, final=final, dest=dest):
                    self.notes.unlink(missing_ok=True)
                    expected = b"- Cite the thread. (user, 2026-03-12, user, in session; N-20260312-01)"
                    if dest in ("card:lead", "drafting"):
                        data = eol.join((b"# Cards", b"## Before drafting", b"Read it.", b"", b"## Mara",
                                         b"Aliases: lead", b"Keep replies short.", b"", b"## Jun", b"Wait.")) + final
                        path = self.write(self.cards, data)
                        stop = b"Keep replies short." if dest.startswith("card:") else b"Read it."
                        offset = data.index(stop) + len(stop) + len(eol)
                        added = expected + eol
                    elif dest.endswith("#Rules"):
                        data = eol.join((b"# Guide", b"## Rules", b"Old entry.", b"### Detail", b"Keep it.",
                                         b"", b"## Other", b"Footer")) + final
                        path = self.write("guide.md", data)
                        offset = data.index(b"Keep it.") + len(b"Keep it.") + len(eol)
                        added = expected + eol
                    elif dest.startswith("pitfall:"):
                        data = eol.join((b"# Flow", *EN.encode().splitlines(),
                                         b"| F1 Flag | us | done | check it | Surviving value  | evidence |",
                                         b"Open questions: none")) + final
                        path = self.write(self.machine, data)
                        offset = data.index(b"Surviving value") + len(b"Surviving value")
                        added = b"; Cite the thread., 2026-03-12 (N-20260312-01)"
                    else:
                        data = b"# Guide" + eol + b"Footnote" + final
                        path = self.write("whole.md", data)
                        offset = len(data)
                        added = (b"" if final else eol) + expected + eol

                    note = self.command("note", "add", "Cite the thread.", "--to", dest)["record"]
                    self.assertTrue(note["checked"])
                    self.assertEqual(note["filed"], path.relative_to(self.root).as_posix())
                    self.assert_inserted(path, data, offset, added)
                    filed = path.read_bytes()
                    refused = self.command("note", "file", note["id"], "--to", dest, code=2)
                    self.assertEqual(refused["problems"][0]["code"], "already_filed")
                    self.assertEqual(path.read_bytes(), filed)
                    self.command("lint", "records")

    def test_note_file_preserves_existing_ledger_bytes_and_skips_existing_citation(self):
        before = b'  - [ ] [note   id=N-20260312-01 kind=correction src="old log"]  Keep this spacing.  '
        self.write(self.notes, before)
        target = self.write("rules.md", b"# Source\r\nRelated to [N-20260312-01]")
        self.command("note", "file", "N-20260312-01", "--to", "rules.md")
        self.assertEqual(target.read_bytes(), b"# Source\r\nRelated to [N-20260312-01]")
        self.assertEqual(self.notes.read_bytes(), before.replace(b"[ ]", b"[x]", 1)
                         .replace(b'src="old log"', b'src="old log" filed=rules.md'))
        self.command("note", "add", "One more.")
        expected = b'\n- [ ] [note id=N-20260312-02 kind=correction src="user, in session" who=user] One more.\n'
        self.assert_inserted(self.notes, before.replace(b"[ ]", b"[x]", 1)
                             .replace(b'src="old log"', b'src="old log" filed=rules.md'),
                             len(before) + len(b" filed=rules.md"), expected)

    def test_pitfalls_replace_placeholders_and_escape_cells(self):
        for placeholder in ("", "-", "—", "none", "none yet", "無"):
            with self.subTest(placeholder=placeholder):
                self.notes.unlink(missing_ok=True)
                prefix = ("## F Rollout\r\n" + EN.replace("\n", "\r\n") +
                          "| F0 Plan | Jun | F1 | ask | fine | meeting |\r\n"
                          "| F1 Flag | us ✅ | done | check | ")
                suffix = "  | receipt |\r\nOpen questions: none"
                data = (prefix + placeholder + suffix).encode()
                path = self.write(self.machine, data)
                self.command("note", "add", "Read | check then file", "--to", "pitfall:F1")
                inserted = r"Read \| check then file, 2026-03-12 (N-20260312-01)".encode()
                if placeholder:
                    after = path.read_bytes()
                    self.assertEqual(after[:len(prefix.encode())], prefix.encode())
                    self.assertEqual(after[len(prefix.encode()):], inserted + suffix.encode())
                else:
                    self.assert_inserted(path, data, len(prefix.encode()), inserted)
                rows, _ = parse_tables(path.read_text(), self.machine)
                self.assertEqual([row.state for row in rows], ["F0", "F1"])
                self.assertEqual(rows[1].pitfalls, "Read | check then file, 2026-03-12 (N-20260312-01)")

    def test_refused_destinations_leave_bytes_and_open_note(self):
        self.write(self.cards, CARDS)
        self.write(self.machine, EN + "| F1 | Jun | done | proof | none | meeting |\r\n" * 2)
        missing = self.root / "absent.md"
        guide = self.write("guide.md", b"# Rules\r\n\r\n## rules\r\nLast")
        self.write("fenced.md", b"# Rules\r\n```md\r\nUnclosed")
        self.write("invalid.md", b"\xff")
        config_file = self.write("settings.toml", b'name = "project"')
        cases = [
            ("gone/absent.md", missing, "dest_missing"),
            ("../outside.md", missing, "dest_invalid"),
            ("guide.md#Not here", guide, "heading_missing"),
            ("guide.md#RULES", guide, "heading_ambiguous"),
            ("card:Missing", self.root / self.cards, "no_card"),
            ("pitfall:F2", self.root / self.machine, "state_missing"),
            ("pitfall:F1", self.root / self.machine, "state_ambiguous"),
            ("fenced.md#Rules", self.root / "fenced.md", "dest_invalid"),
            ("invalid.md", self.root / "invalid.md", "dest_unreadable"),
            ("settings.toml", config_file, "dest_invalid"),
        ]
        record = self.command("note", "add", "Keep open.")["record"]
        for dest, path, issue in cases:
            with self.subTest(dest=dest):
                before = path.read_bytes() if path.exists() else None
                inbox = self.notes.read_bytes()
                process = self.invoke("note", "file", record["id"], "--to", dest)
                self.assert_text_failure(process, 2, issue, f"next: wm note file {record['id']} --to")
                self.assertEqual(path.read_bytes() if path.exists() else None, before)
                self.assertEqual(self.notes.read_bytes(), inbox)
        process = self.invoke("note", "add", "Still keep this.", "--to", "guide.md#RULES")
        self.assert_text_failure(process, 2, "heading_ambiguous", "next: wm note file N-20260312-02")
        self.assertEqual([note["text"] for note in self.command("note", "list", "--open")["notes"]],
                         ["Keep open.", "Still keep this."])

    def test_card_rule_standing_and_log_write_exact_slices(self):
        for eol, final in ((b"\r\n", b"\r\n"), (b"\n", b"")):
            with self.subTest(eol=eol, final=final):
                self.notes.unlink(missing_ok=True)
                data = eol.join((b"# Cards", b"## Mara", b"Aliases: lead", b"", b"Open questions: none")) + final
                card = self.write(self.cards, data)
                self.command("card", "add", "Jun", "--role", "Tracks the rollout", "--aliases", "J, Juno")
                offset = data.index(b"Aliases: lead") + len(b"Aliases: lead") + len(eol)
                self.assert_inserted(card, data, offset, eol.join((
                    b"", b"## Jun", b"", b"Aliases: J, Juno", b"- Role: Tracks the rollout", b"")))
                before = card.read_bytes()
                response = self.command("card", "add", "juno", code=2)
                self.assertEqual(response["problems"][0]["code"], "card_exists")
                self.assertEqual(card.read_bytes(), before)
                self.assertIn("## Jun", self.command("card", "J")["card"]["person"])

                data = b"# rules" + eol + b"# last comment" + final
                self.write(self.rules, data)
                note = self.command("note", "add", "Mention the asker.")["record"]["id"]
                self.command("rule", "add", "--id", "mention", "--message", "Mention the asker.",
                             "--from", note, "--require-mention", "--channels", "release, app-team")
                block = eol.join((b"", b"[[outbound]]", b'id = "mention"', b'message = "Mention the asker."',
                                  b'channels = ["release", "app-team"]', b"require_mention = true",
                                  f'src = "{note}"'.encode(), b""))
                self.assert_inserted(self.rules, data, len(data), (b"" if final else eol) + block)
                self.assertEqual(self.command("note", "list")["notes"][0]["filed"], "rules.toml")
                data = self.rules.read_bytes()
                self.command("standing", "add", "--id", "ack", "--kind", "chat", "--targets", "on-call",
                             "--src", "user", "--match", "^ack", "--expires", "2026-10-01")
                block = eol.join((b"", b"[[standing]]", b'id = "ack"', b'kind = "chat"', b'targets = ["on-call"]',
                                  b'src = "user"', b'match = "^ack"', b'expires = "2026-10-01"', b""))
                self.assert_inserted(self.rules, data, len(data), block)
                self.command("lint", "rules")

                data = eol.join((b"# 2026-03-12", b"", b"- 12:00 First", b"", b"## Start here", b"Check this.")) + final
                self.write(self.day, data)
                self.command("log", "One more thing.")
                self.assert_inserted(self.day, data, data.index(b"## Start here"), b"- 18:30 One more thing." + eol)

    def test_rule_refusals_and_missing_file_creation(self):
        args = ("rule", "add", "--id", "test", "--src", "user", "--message", "Check it.")
        self.command(*args, "--max-chars", "100")
        self.assertEqual(tomllib.loads(self.rules.read_text())["outbound"][0]["max_chars"], 100)
        self.assertFalse(self.notes.exists())
        base = args[:3] + ("another",) + args[4:]
        standing = ("standing", "add", "--id", "ack", "--kind", "chat", "--targets", "ops", "--src", "user")
        bad = [
            (args + ("--require-mention",), "duplicate_rule"),
            (base + ("--forbid", "["), "bad_regex"),
            (base, "rule_without_check"),
            (base + ("--check", " "), "bad_rule"),
            (standing + ("--match", "["), "bad_regex"),
            (("rule", "add", "--id", "missing-note", "--from", "N-20260312-99", "--message", "Need proof",
              "--require-receipt"), "unknown_id"),
        ]
        before = self.rules.read_bytes()
        for arguments, issue in bad:
            with self.subTest(issue=issue):
                result = self.command(*arguments, code=2)
                self.assertIn(issue, {problem["code"] for problem in result["problems"]})
                self.assertEqual(self.rules.read_bytes(), before)
        self.command(*standing, "--expires", "2026-01-01")
        self.assertIn("standing_expired", {problem["code"] for problem in
                                           self.command("lint", "rules", code=1)["problems"]})
        self.rules.write_bytes(b"invalid = [")
        result = self.command(*base, "--require-mention", code=2)
        self.assertEqual(result["problems"][0]["code"], "bad_rule")
        self.assertEqual(self.rules.read_bytes(), b"invalid = [")
        self.command("log", "Created today.")
        self.assertEqual(self.day.read_bytes(), b"# 2026-03-12\n\n- 18:30 Created today.\n")
        fenced = b"# 2026-03-12\n- 18:30 Created today.\n```\nFenced text"
        self.day.write_bytes(fenced)
        refused = self.command("log", "Created today.", code=2)
        self.assertEqual(refused["problems"][0]["code"], "dest_invalid")
        self.assertEqual(self.day.read_bytes(), fenced)

    def test_item_writer_keeps_other_keys_entries_and_order(self):
        self.write(self.machine, EN + "| F1 | us ✅ | done | check | none | evidence |\n")
        existing = {"first": {"unchanged": True}, "items": [{"id": "I1", "state": "F1", "private": 1}],
                    "receipts": [{"id": "R1", "what": "original"}], "last": 9}
        original = self.write(self.receipts, json.dumps(existing)).read_bytes()
        for args, issue, code in (((), "usage", 64), (("--state", "F99"), "unknown_state", 2)):
            result = self.command("item", "set", "I2", *args, code=code)
            self.assertEqual(result["problems"][0]["code"], issue)
            self.assertEqual(self.receipts.read_bytes(), original)
        self.command("item", "set", "I2", "--state", "F99", "--force", "--title", "Manual")
        self.command("item", "set", "I1", "--evidence", "Looked at it.")
        self.command("item", "done", "I2", "--evidence", "Finished.")
        items = self.command("item", "list")["items"]
        self.assertEqual(items, [
            {"id": "I1", "state": "F1", "private": 1, "evidence": "Looked at it.", "at": NOW},
            {"id": "I2", "state": "done", "title": "Manual", "at": NOW, "evidence": "Finished."},
        ])
        existing["items"] = items
        self.assertEqual(self.receipts.read_text(), json.dumps(existing, indent=2) + "\n")

    def chat(self, readback=""):
        self.config(f"""
[[channel]]
id = "ops"
send = "printf '#25\\\\n'"
readback = {json.dumps(readback)}

[[command]]
id = "look"
run = "printf '%s\\\\n'"
max_output = 1
""")
        self.write(self.cards, CARDS)

    def test_claimed_proof_needs_user_but_checked_proof_auto_sends(self):
        self.chat()
        self.write(self.rules, """[[outbound]]
id = "proof"
when = "complete"
require_receipt = true
message = "Need proof"
src = "user"

[[standing]]
id = "allowed"
kind = "chat"
targets = ["ops"]
src = "user"
""")
        check = ("outbound", "check", "--to", "Mara", "--channel", "ops")
        self.command(*check, "--verified", "I looked.", payload="It is complete.")
        self.assertFalse(self.receipts.exists())
        send = ("action", "send", "--kind", "chat", "--target", "ops", "--to", "Mara")
        claim = self.invoke(*send, "--verified", "I looked.", payload="It is complete.")
        self.assert_text_failure(claim, 1, "needs_user", "I looked.", "payload: It is complete.", "--run")
        recorded = json.loads(self.receipts.read_text())["receipts"]
        self.assertEqual([(r["what"], r["how"]) for r in recorded], [("I looked.", "claimed")])
        self.assertEqual(self.command("action", "show", "A-20260312-01")["action"]["state"], "draft")
        self.assertEqual(len(self.actions.read_text().splitlines()), 1)

        verified = self.command("receipt", "add", "Adapter saw proof", "--item", "I1", "--by-cmd", "look",
                                "--expect", "found", "--", "proof found")["receipt"]
        self.assertEqual({"how": "checked", "command": "look", "match": "found", "item": "I1",
                          "sha256": hashlib.sha256(b"proof found\n").hexdigest()},
                         {key: verified[key] for key in ("how", "command", "match", "item", "sha256")})
        action = self.command(*send, "--receipt", verified["id"], payload="It is complete.")["action"]
        self.assertEqual((action["state"], action["approved_by"]), ("sent", "standing:allowed"))

        self.rules.write_text(self.rules.read_text().replace("require_receipt = true", 'require_receipt = "checked"'))
        before = self.receipts.read_bytes()
        for args in (("--verified", "I looked."), ("--receipt", recorded[0]["id"])):
            output = self.command(*send, *args, payload="It is complete.", code=2)
            self.assertEqual(output["violations"][0]["check"], "require_receipt")
        self.assertEqual(self.receipts.read_bytes(), before)
        self.assertEqual(len(self.command("action", "list")["actions"]), 2)
        self.command(*check, "--receipt", verified["id"], payload="It is complete.")

    def test_receipt_command_refusals_write_nothing(self):
        self.config("""
[[command]]
id = "failure"
run = "printf 'matched'; exit 9"

[[command]]
id = "mismatch"
run = "printf 'different'"

[[command]]
id = "writer"
run = "touch forbidden"
writes = true
""")
        for command in ("failure", "mismatch", "writer", "unknown"):
            with self.subTest(command=command):
                output = self.command("receipt", "add", "Evidence", "--by-cmd", command, "--expect", "matched", code=2)
                self.assertEqual(output["problems"][0]["code"], "check_failed")
                self.assertFalse(self.receipts.exists())
        self.assertFalse((self.root / "forbidden").exists())
        self.write(self.receipts, b'{"items": []}')
        self.command("receipt", "add", "Evidence", "--by-cmd", "failure", "--expect", "matched", code=2)
        self.assertEqual(self.receipts.read_bytes(), b'{"items": []}')

    def test_readback_must_print_the_stored_nonempty_result(self):
        cases = (("printf 'different'", "sent", 1), ("printf '%s' \"$WM_RESULT\"", "verified", 0),
                 ("printf 'anything'", "sent", 1))
        for index, (readback, expected, code) in enumerate(cases):
            with self.subTest(readback=readback):
                self.chat(readback)
                self.write(self.rules, """[[standing]]
id = "allowed"
kind = "chat"
targets = ["ops"]
src = "user"
""")
                args = ("action", "send", "--kind", "chat", "--target", "ops", "--to", "Mara")
                result = self.command(*args, payload=f"Other message {index}", code=code)
                self.assertEqual(result["action"]["state"], expected)
                self.assertEqual(self.command("action", "show", result["action"]["id"])["action"]["state"], expected)
                if code:
                    self.assertEqual(result["problems"][0]["code"], "readback_failed")
                    self.assertNotIn("proof", result["action"])
                else:
                    self.assertEqual(result["action"]["proof"], "readback:auto")
                if index == 2:
                    self.assertEqual(result["action"]["result"], "#25\n")

    def test_wrapup_write_blocks_prior_open_note(self):
        self.command("note", "add", "Yesterday's note.", now="2026-03-11T18:30:00+09:00")
        self.write(self.receipts, "{}")
        self.assert_wrapup_blocked("note_unfiled", code=2)

    def test_wrapup_write_blocks_unknown_action(self):
        self.write(self.receipts, "{}")
        preview = self.command("action", "preview", "--kind", "other", "--target", "t",
                               "--", "exit 137", payload="Confirm.")
        identity = preview["action"]["id"]
        self.command("action", "approve", identity, "--by", "yes")
        self.command("action", "run", identity, code=4)
        self.assert_wrapup_blocked("action_unknown", code=2)

    def test_wrapup_write_blocks_stale_items(self):
        self.write(self.machine, EN + "| F1 | us ✅ | done | check | none | source |\n")
        self.write(self.receipts, json.dumps({"items": [{"id": "I1", "title": "Verify me", "state": "F1",
                                                      "at": "2026-03-08T18:30:00+09:00"}]}))
        self.assert_wrapup_blocked("item_unverified", code=4)
        self.assert_text_failure(self.invoke("next"), 4, "unverified", "data/evidence.json")
        self.assert_text_failure(self.invoke("wrapup", "check"), 4, "unverified", "data/evidence.json")

    def assert_wrapup_blocked(self, issue, code):
        before = self.day.read_bytes() if self.day.exists() else None
        process = self.invoke("wrapup", "write", "--start", "Review the rollout.")
        self.assert_text_failure(process, code, issue)
        self.assertEqual(self.day.read_bytes() if self.day.exists() else None, before)
        self.assertFalse((self.root / ".wm/wrapup-pass").exists())
        self.assertTrue((self.root / ".wm/wrapup-pending").is_file())

    def test_wrapup_write_is_once_unless_replaced(self):
        self.write(self.machine, EN + "| F1 Flag | us ✅ | done | proof | beware | source |\n")
        self.command("item", "set", "I1", "--state", "F1", "--title", "First item")
        self.write("worklog/commitments.md", COMMIT)
        a = self.command("action", "preview", "--kind", "other", "--target", "TICKET", payload="unshared payload")
        before = self.write(self.day, b"# 2026-03-12\r\n\r\n- 12:00 Entry").read_bytes()
        handoff = (
            "\r\n\r\n## Start here\r\n\r\n- Review the rollout.\r\n- Read the issue.\r\n"
            "\r\n### Open items\r\n\r\n- do I1 First item | F1 Flag\r\n"
            "\r\n### Open commitments\r\n\r\n- C-20260312-01 2026-03-12 Send the checklist.\r\n"
            f"\r\n### Open actions\r\n\r\n- {a['action']['id']} draft TICKET\r\n"
            "\r\n## Unpushed commits\r\n\r\nnone\r\n"
        ).encode()
        args = ("wrapup", "write", "--start", "Review the rollout.", "--note", "Read the issue.")
        self.assertEqual(self.command(*args)["result"], "PASS")
        self.assert_inserted(self.day, before, len(before), handoff)
        self.assertTrue((self.root / ".wm/wrapup-pass").is_file())
        self.assertFalse((self.root / ".wm/wrapup-pending").exists())
        self.assert_text_failure(self.invoke(*args), 2, "handoff_exists")
        self.assertEqual(self.day.read_bytes(), before + handoff)
        self.assertEqual(self.command("wrapup", "write", "--start", "New text.", "--replace")["result"], "PASS")
        replaced = self.day.read_bytes()
        self.assertTrue(replaced.startswith(before + b"\r\n\r\n## Start here\r\n"))
        self.assertEqual(replaced.count(b"## Start here"), 1)
        self.assertNotIn(b"Review the rollout.", replaced)
        self.assertIn(b"- New text.", replaced)

    def test_rule_command_pass_violation_and_timeout(self):
        self.chat()
        self.write("validator.sh", """read -r draft
if [ "$WM_TO" = Mara ] && [ "$WM_CHANNEL" = ops ] && [ "$draft" = correct ]; then
    exit 0
fi
printf 'first detail line\\nsecond is private\\n'
exit 3
""")
        args = ("rule", "add", "--id", "custom", "--src", "meeting", "--message", "Follow the policy.")
        self.command(*args, "--check", "sh validator.sh")
        checks = ("outbound", "check", "--to", "Mara", "--channel", "ops")
        self.assertEqual(self.invoke(*checks, payload="correct\n").stdout, "ok\n")
        failed = self.command(*checks, payload="declined\n", code=2)
        self.assertEqual(failed["violations"], [{"rule": "custom", "check": "check",
                                                "message": "Follow the policy.", "src": "meeting",
                                                "detail": "first detail line"}])
        self.assert_text_failure(self.invoke(*checks, payload="declined\n"), 2, "first detail line")

        self.rules.unlink()
        self.command(*args, "--check", "exec sleep 30")
        stdout = io.StringIO()
        with patch("wm.outbound.CHECK_TIMEOUT", 0.05), patch("sys.stdin", io.StringIO("draft")), redirect_stdout(stdout):
            self.assertEqual(cli.main(self.argv(*checks)[1:]), 4)
        self.assertIn("unverified check_unverified rules.toml:1", stdout.getvalue())
        self.assertTrue(stdout.getvalue().splitlines()[-1].startswith("next: "))

        self.rules.unlink()
        self.command(*args, "--check", "exit 127")
        explicit_exit = self.command(*checks, payload="draft", code=2)
        self.assertEqual(explicit_exit["violations"][0]["check"], "check")

        self.rules.unlink()
        self.command(*args, "--check", "./does-not-exist")
        missing = self.command(*checks, payload="draft", code=4)
        self.assertEqual(missing["problems"][0]["code"], "check_unverified")
        self.assertNotIn("does-not-exist", str(missing))

    def test_run_output_limit_preserves_full_stdout_stderr_and_exit(self):
        self.config(r"""
[[command]]
id = "loud"
run = "printf 'é猫abcdef'; printf diagnostic >&2; exit 7"
max_output = 3

[[command]]
id = "unlimited"
run = "printf 'abcdef'"
max_output = 0

[[command]]
id = "default"
run = "python3 -c 'print(42 * \"ab\" * 100)'"
""")
        proc = self.invoke("run", "loud")
        self.assertEqual((proc.returncode, proc.stderr), (7, "diagnostic"))
        self.assertEqual(proc.stdout, "é猫a\n... truncated: 8 characters in total, full output in .wm/last/loud.txt\n")
        saved = self.root / ".wm/last/loud.txt"
        self.assertEqual(saved.read_bytes(), "é猫abcdef".encode())
        saved.unlink()
        proc = self.invoke("run", "loud", "--full")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (7, "é猫abcdef", "diagnostic"))
        self.assertFalse(saved.exists())
        proc = self.invoke("run", "unlimited")
        self.assertEqual((proc.returncode, proc.stdout), (0, "abcdef"))
        self.assertFalse((self.root / ".wm/last/unlimited.txt").exists())
        proc = self.invoke("run", "default")
        self.assertTrue(proc.stdout.endswith("... truncated: 8401 characters in total, full output in .wm/last/default.txt\n"))
        self.assertEqual(proc.stdout.splitlines()[0], "ab" * 4000)
        self.assertEqual((self.root / ".wm/last/default.txt").read_bytes(), b"ab" * 4200 + b"\n")

    def test_adapter_output_does_not_print_commands_secrets_or_prior_payloads(self):
        self.config('\n[[command]]\nid = "show"\nrun = "sh inspect.sh"\n')
        self.write(self.cards, CARDS)
        self.command("action", "preview", "--kind", "other", "--target", "test", payload="prior draft text")
        self.write("inspect.sh", """
printf '%s\\n' 'sh inspect.sh' 'ghp_exampleToken' 'prior draft text'
printf '%s\\n' 'ghp_exampleToken' >&2
exit 3
""")
        proc = self.invoke("run", "show")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr),
                         (3, "sh inspect.sh\nghp_exampleToken\nprior draft text\n", "ghp_exampleToken\n"))
        self.command("rule", "add", "--id", "custom", "--src", "user", "--message", "Review the draft.",
                     "--check", "sh inspect.sh")
        check = self.command("outbound", "check", "--to", "Mara", payload="new draft", code=2)
        self.assertEqual(check["violations"][0]["detail"], "[redacted]")

    def test_approve_run_keeps_going_after_expired_id(self):
        self.chat()
        args = ("action", "preview", "--kind", "chat", "--target", "ops", "--to", "Mara")
        expired = self.command(*args, "--ttl", "1", now="2026-03-12T18:00:00+09:00",
                               payload="old message")["action"]["id"]
        valid = self.command(*args, payload="new message")["action"]["id"]
        outcome = self.command("action", "approve", expired, valid, "--by", "yes", "--run", code=2)
        first, second = outcome["actions"]
        self.assertEqual((first["id"], first["problems"][0]["code"]), (expired, "expired"))
        self.assertEqual((second["id"], second["state"], second["run"]["action"]["state"]), (valid, "sent", "sent"))
        self.assertEqual(self.command("action", "show", expired)["action"]["state"], "expired")
        self.assertEqual(self.command("action", "show", valid)["action"]["state"], "sent")
        self.assertNotIn("new message", json.dumps(outcome))
        events = [json.loads(event) for event in self.actions.read_text().splitlines()]
        self.assertEqual([event["event"] for event in events if event["id"] == valid],
                         ["draft", "approved", "running", "sent"])

    def test_other_non_ok_text_ends_with_followup_and_keeps_locations(self):
        cases = [
            (("brief",), 4, "worklog"),
            (("next",), 4, "data/evidence.json"),
            (("lint",), 4, "no_state_machines"),
            (("catalog", "--kind", "unknown"), 64, "usage"),
            (("card", "Missing"), 2, "no_card"),
            (("sm", "F99"), 4, "state_missing"),
            (("item", "list"), 4, "data/evidence.json"),
            (("doctor",), 1, "hook_not_registered"),
        ]
        for args, code, detail in cases:
            with self.subTest(args=args):
                proc = self.invoke(*args)
                self.assert_text_failure(proc, code, detail)
                if code == 4:
                    self.assertIn("unverified", proc.stdout)
