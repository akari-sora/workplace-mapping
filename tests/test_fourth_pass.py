"""Regression coverage for the fourth independent review."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_wm import CARDS, CONFIG, EN, HANDOFF, NOW, REPO, SHIM

from wm.config import load_config
from wm.writers import add_rule


class FourthPassTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-fourth-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.root = self.workspace / "project"
        self.root.mkdir()
        self.env = dict(os.environ, WM_TEST_CLOCK="1", PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=str(self.workspace / "absent-global-config"))
        self.write("workplace.toml", CONFIG)

    def write(self, relative: str | Path, data: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def configure(self, extra: str = "") -> None:
        self.write("workplace.toml", CONFIG + extra)

    def invoke(self, *args: str, payload: str | bytes = b"", now: str = NOW,
               env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        return subprocess.run(
            [str(SHIM), "--root", str(self.root), "--now", now, *args],
            cwd=self.root, env=self.env | (env or {}), input=data, capture_output=True,
            timeout=10, check=False,
        )

    def command(self, *args: str, code: int = 0, payload: str | bytes = b"",
                now: str = NOW) -> dict:
        process = self.invoke("--json", *args, payload=payload, now=now)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        self.assertEqual(process.stderr, b"")
        return json.loads(process.stdout)

    @staticmethod
    def codes(data: dict) -> set[str]:
        return {entry["code"] for entry in data["problems"]}

    def events(self) -> list[dict]:
        return [json.loads(line) for line in (self.root / ".wm/actions.jsonl").read_text().splitlines()]

    def test_01_actions_use_one_fixed_command(self) -> None:
        self.configure("""
[[channel]]
id = "chat"
send = "cat > chat.out"
[[command]]
id = "write"
run = "cat > command.out"
writes = true
""")
        for kind, target in (("chat", "chat"), ("command", "write")):
            refused = self.command("action", "preview", "--kind", kind, "--target", target,
                                   "--", "touch override", code=64)
            self.assertEqual(self.codes(refused), {"usage"})
        self.assertFalse((self.root / ".wm/actions.jsonl").exists())

        other = self.command("action", "preview", "--kind", "other", "--target", "queue",
                             "--", "cat > other.out", payload="fixed payload")["action"]
        self.assertEqual(other["command"], "cat > other.out")
        self.command("action", "approve", other["id"], "--by", "yes")
        self.command("action", "run", other["id"], "--", "touch override", code=64)
        self.command("action", "run", other["id"])
        self.assertEqual((self.root / "other.out").read_text(), "fixed payload")
        running = next(event for event in self.events()
                       if event["id"] == other["id"] and event["event"] == "running")
        self.assertEqual(running["command"], "cat > other.out")
        self.assertNotIn("command_sha256", running)

        registered = self.command("action", "preview", "--kind", "command", "--target", "write",
                                  payload="registered")["action"]
        self.command("action", "approve", registered["id"], "--by", "yes")
        self.command("action", "run", registered["id"])
        running = next(event for event in self.events()
                       if event["id"] == registered["id"] and event["event"] == "running")
        self.assertEqual(running["target"], "write")
        self.assertEqual(running["command_sha256"], hashlib.sha256(b"cat > command.out").hexdigest())

        changed = self.command("action", "preview", "--kind", "command", "--target", "write",
                               payload="do not run")["action"]
        self.command("action", "approve", changed["id"], "--by", "yes")
        self.configure('[[command]]\nid = "write"\nrun = "touch changed"\nwrites = true\n')
        self.assertEqual(self.codes(self.command("action", "run", changed["id"], code=2)),
                         {"command_changed"})
        self.assertFalse((self.root / "changed").exists())

        manual = self.command("action", "preview", "--kind", "other", "--target", "person",
                              payload="do this")["action"]
        self.command("action", "approve", manual["id"], "--by", "yes")
        self.command("action", "run", manual["id"], code=64)
        settled = self.command("action", "resolve", manual["id"], "--state", "sent",
                               "--proof", "ticket #8")["action"]
        self.assertEqual(settled["state"], "sent")

        self.configure('[[channel]]\nid = "no-send"\n')
        self.command("action", "preview", "--kind", "chat", "--target", "no-send", code=64)
        self.configure('[[command]]\nid = "no-run"\nwrites = true\n')
        self.command("action", "preview", "--kind", "command", "--target", "no-run", code=64)
        self.command("standing", "add", "--id", "never", "--kind", "other", "--targets", "queue",
                     "--src", "user", code=64)

    def test_02_rules_apply_to_all_kinds_and_support_kinds_filter(self) -> None:
        self.configure('[[command]]\nid = "write"\nrun = ":"\nwrites = true\n')
        self.write("rules.toml", """[[outbound]]
id = "all"
forbid = "blocked"
message = "blocked everywhere"
src = "user"

[[outbound]]
id = "chat-only"
kinds = ["chat"]
forbid = "chatword"
message = "chat only"
src = "user"
""")
        for kind, target in (("command", "write"), ("other", "queue")):
            blocked = self.command("action", "preview", "--kind", kind, "--target", target,
                                   payload="blocked", code=2)
            self.assertEqual({entry["rule"] for entry in blocked["violations"]}, {"all"})
        allowed = self.command("action", "preview", "--kind", "other", "--target", "queue",
                               payload="chatword")["action"]
        self.assertEqual(allowed["state"], "draft")
        self.command("rule", "add", "--id", "commands", "--src", "user", "--message", "commands",
                     "--kinds", "command", "--forbid", "never")

    def test_03_test_clock_future_evidence_and_actions(self) -> None:
        env = dict(self.env)
        env.pop("WM_TEST_CLOCK", None)
        process = subprocess.run([str(SHIM), "--root", str(self.root), "--now", NOW, "--json", "doctor"],
                                 cwd=self.root, env=env, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 64)

        self.write(".agents/skills/work/state-machines.md",
                   EN + "| F1 | us ✅ | done | check | none | source |\n")
        self.write("data/evidence.json", json.dumps({"items": [
            {"id": "I1", "state": "F1", "at": "2026-03-13T00:00:00+09:00"}]}))
        self.assertEqual(self.codes(self.command("next", code=4)), {"item_unverified"})

        action = self.command("action", "preview", "--kind", "other", "--target", "queue",
                              "--", ":", payload="payload")["action"]
        self.command("action", "approve", action["id"], "--by", "yes")
        later = "2026-03-12T20:00:00+09:00"
        self.assertEqual(self.command("action", "show", action["id"], now=later)["action"]["state"], "expired")
        earlier = "2026-03-12T18:00:00+09:00"
        self.assertEqual(self.codes(self.command("action", "run", action["id"], now=earlier, code=2)),
                         {"bad_clock"})

    def test_04_wrapup_rejects_existing_invalid_evidence(self) -> None:
        day = self.write("worklog/2026-03-12.md", HANDOFF)
        self.write("data/evidence.json", "not json")
        before = day.read_bytes()
        self.assertEqual(self.codes(self.command("wrapup", "check", code=4)), {"evidence_unverified"})
        self.assertEqual(self.codes(self.command("wrapup", "write", "--start", "Tomorrow", code=4)),
                         {"evidence_unverified"})
        self.assertEqual(day.read_bytes(), before)
        (self.root / "data/evidence.json").unlink()
        self.assertEqual(self.command("wrapup", "check")["result"], "PASS")

    def test_05_item_set_requires_a_change_and_title_does_not_refresh(self) -> None:
        old = "2026-03-01T00:00:00+09:00"
        path = self.write("data/evidence.json", json.dumps({"items": [
            {"id": "I1", "state": "done", "title": "Old", "at": old}]}))
        before = path.read_bytes()
        self.command("item", "set", "I1", code=64)
        self.assertEqual(path.read_bytes(), before)
        title = self.command("item", "set", "I1", "--title", "New")["item"]
        self.assertEqual(title["at"], old)
        evidence = self.command("item", "set", "I1", "--evidence", "checked")["item"]
        self.assertEqual(evidence["at"], NOW)

    def test_06_checked_receipts_need_nonempty_matches_and_store_args(self) -> None:
        self.configure("""[[command]]
id = "read"
run = "printf '%s'"
""")
        self.command("receipt", "add", "proof", "--by-cmd", "read", "--expect", "", code=64)
        self.command("receipt", "add", "proof", "--by-cmd", "read", "--expect", "^",
                     "--", "hello", code=2)
        receipt = self.command("receipt", "add", "proof", "--by-cmd", "read", "--expect", "ell",
                               "--", "hello")["receipt"]
        self.assertEqual(receipt["args"], ["hello"])
        self.assertEqual(receipt["match"], "ell")
        self.assertEqual(receipt["match_sha256"], hashlib.sha256(b"ell").hexdigest())
        self.assertEqual(receipt["stdout_sha256"], hashlib.sha256(b"hello").hexdigest())

    def test_07_outbound_hook_blocks_a_violation_despite_unverified_check(self) -> None:
        self.write("rules.toml", """[[outbound]]
id = "violation"
forbid = "blocked"
message = "do not"
src = "user"

[[outbound]]
id = "broken-check"
check = "./missing-check"
message = "check it"
src = "user"
""")
        hook = json.dumps({"tool_input": {"text": "blocked"}})
        process = self.invoke("outbound", "hook", "--text-field", "text", payload=hook)
        self.assertEqual(process.returncode, 2)
        self.assertIn(b'"rule": "violation"', process.stderr)

    def test_08_needs_user_payload_is_exact_and_secrets_are_never_recorded(self) -> None:
        secret = self.command("action", "preview", "--kind", "other", "--target", "queue",
                              payload="password=abcdefgh", code=2)
        self.assertEqual(self.codes(secret), {"secret_like"})
        self.assertFalse((self.root / ".wm/actions.jsonl").exists())
        payload = "off\nsecond line"
        needs = self.command("action", "send", "--kind", "other", "--target", "queue",
                             "--", ":", payload=payload, code=1)
        self.assertEqual(needs["action"]["payload"], payload)
        text = self.invoke("action", "show", needs["action"]["id"]).stdout.decode()
        self.assertIn("payload: off\nsecond line", text)

    def test_09_empty_approval_is_refused(self) -> None:
        action = self.command("action", "preview", "--kind", "other", "--target", "queue")["action"]
        self.command("action", "approve", action["id"], "--by", "", code=64)
        self.assertEqual(self.command("action", "show", action["id"])["action"]["state"], "draft")

    def test_10_note_destinations_are_markdown_and_not_internal_files(self) -> None:
        paths = [".wm/safe.md", "worklog/commitments.md", "data/evidence.json",
                 ".wm/actions.jsonl", "workplace.toml", "rules.toml"]
        for relative in paths:
            if relative != "workplace.toml":
                self.write(relative, "original")
        before = {relative: (self.root / relative).read_bytes() for relative in paths}
        for relative in paths:
            with self.subTest(relative=relative):
                result = self.command("note", "add", "keep this", "--to", relative, code=2)
                self.assertEqual(self.codes(result), {"dest_invalid"})
                self.assertEqual(result["problems"][0]["where"], relative)
                self.assertEqual((self.root / relative).read_bytes(), before[relative])

    def test_11_completed_records_are_immutable(self) -> None:
        commitment = self.command("commitments", "add", "--to", "Mara", "--src", "user", "Do it")["record"]
        self.command("commitments", "done", commitment["id"], "--proof", "#1")
        ledger = self.root / "worklog/commitments.md"
        before = ledger.read_bytes()
        result = self.command("commitments", "done", commitment["id"], "--proof", "#2", code=2)
        self.assertEqual(self.codes(result), {"already_done"})
        self.assertEqual(ledger.read_bytes(), before)

        self.write("guide.md", "# Guide\n")
        self.write("other.md", "# Other\n")
        note = self.command("note", "add", "Remember", "--to", "guide.md")["record"]
        notes = self.root / "worklog/inbox.md"
        before_notes = notes.read_bytes()
        other = (self.root / "other.md").read_bytes()
        result = self.command("note", "file", note["id"], "--to", "other.md", code=2)
        self.assertEqual(self.codes(result), {"already_filed"})
        self.assertEqual(notes.read_bytes(), before_notes)
        self.assertEqual((self.root / "other.md").read_bytes(), other)
        self.command("rule", "add", "--id", "from-filed", "--message", "remember",
                     "--from", note["id"], "--forbid", "never")
        self.assertEqual(notes.read_bytes(), before_notes)

    def test_12_watch_state_is_confined_and_hashed(self) -> None:
        commitment = self.write("worklog/commitments.md", "do not replace")
        process = self.invoke("watch", "--name", "probe", "--probe", "printf ready", "--done", "ready",
                              "--state", "status")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        state = self.root / ".wm/watch/status"
        self.assertEqual(state.read_text(), hashlib.sha256(b"ready").hexdigest() + "\n")
        self.command("watch", "--name", "probe", "--probe", ":", "--state",
                     "worklog/commitments.md", code=64)
        self.assertEqual(commitment.read_text(), "do not replace")

    def test_13_init_keeps_existing_files(self) -> None:
        templates = self.workspace / "templates/en"
        templates.mkdir(parents=True)
        (templates / "workplace.toml").write_text('[workplace]\nname = "{{name}}"\n')
        (templates / "keep.md").write_text("template")
        (templates / "new.md").write_text("new")
        destination = self.workspace / "initialized"
        destination.mkdir()
        (destination / "keep.md").write_text("user content")
        process = subprocess.run(
            [str(SHIM), "--json", "init", "--name", "safe", "--dir", str(destination)],
            cwd=self.root, env=self.env | {"WM_TEMPLATES": str(self.workspace / "templates")},
            capture_output=True, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        data = json.loads(process.stdout)
        self.assertEqual(data["kept"], ["keep.md"])
        self.assertEqual((destination / "keep.md").read_text(), "user content")
        self.assertEqual((destination / "new.md").read_text(), "new")

    def test_14_rule_writers_ignore_warnings_and_validate_ids(self) -> None:
        self.write("rules.toml", """[[standing]]
id = "expired"
kind = "chat"
targets = ["ops"]
expires = "2026-01-01"
src = "user"
""")
        self.command("rule", "add", "--id", "new_rule", "--message", "new", "--src", "user",
                     "--forbid", "never")
        self.command("standing", "add", "--id", "new.standing", "--kind", "chat",
                     "--targets", "ops", "--src", "user")
        for command in ("rule", "standing"):
            args = ([command, "add", "--id", "bad/id"] +
                    (["--message", "bad", "--src", "user", "--forbid", "x"] if command == "rule" else
                     ["--kind", "chat", "--targets", "ops", "--src", "user"]))
            self.command(*args, code=64)

    def test_15_wm_run_prints_raw_output(self) -> None:
        self.configure("""[[command]]
id = "raw"
run = "printf 'off\\npassword=abcdefgh\\n'"
""")
        self.command("action", "preview", "--kind", "other", "--target", "queue", payload="off")
        process = self.invoke("run", "raw")
        self.assertEqual((process.returncode, process.stdout, process.stderr),
                         (0, b"off\npassword=abcdefgh\n", b""))

    def test_16_stop_gate_reason_is_redacted(self) -> None:
        self.command("action", "preview", "--kind", "other", "--target", "queue",
                     payload="no_worklog_today")
        self.command("wrapup", "begin")
        process = self.invoke("stop-gate", payload="{}")
        self.assertEqual(process.returncode, 0)
        decision = json.loads(process.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertNotIn("no_worklog_today", decision["reason"])
        self.assertIn("[redacted]", decision["reason"])

    def test_17_draft_bytes_match_and_invalid_utf8_is_refused(self) -> None:
        self.write("rules.toml", """[[outbound]]
id = "size"
max_chars = 3
message = "too long"
src = "user"
""")
        draft = self.write("draft.bin", b"a\r\nb")
        outbound = self.command("outbound", "check", "--to", "", "--file", draft.name, code=2)
        action = self.command("action", "preview", "--kind", "other", "--target", "queue",
                              "--file", draft.name, code=2)
        self.assertEqual(outbound["violations"], action["violations"])
        self.assertFalse((self.root / ".wm/actions.jsonl").exists())
        draft.write_bytes(b"bad\xff")
        self.assertEqual(self.invoke("outbound", "check", "--to", "", "--file", draft.name).returncode, 4)
        self.assertEqual(self.invoke("action", "preview", "--kind", "other", "--target", "queue",
                                     "--file", draft.name).returncode, 4)
        self.assertFalse((self.root / ".wm/actions.jsonl").exists())

    def test_18_refused_destination_reports_requested_path(self) -> None:
        outside = self.workspace / "outside.md"
        outside.write_text("outside")
        result = self.command("note", "add", "remember", "--to", "../outside.md", code=2)
        self.assertEqual(result["problems"][0]["where"], "../outside.md")
        self.assertEqual(outside.read_text(), "outside")

    def test_19_replace_refuses_ambiguous_start_heading(self) -> None:
        day = self.write("worklog/2026-03-12.md", """# Day
## Start here
first
## Start here
second
## Unpushed commits
none
""")
        before = day.read_bytes()
        result = self.command("wrapup", "write", "--start", "new", "--replace", code=2)
        self.assertEqual(self.codes(result), {"heading_ambiguous"})
        self.assertEqual(day.read_bytes(), before)

    def test_20_writers_reject_line_separators_and_controls(self) -> None:
        bad = "ordinary\u2028## Injected"
        cases = [
            ("note", "add", bad),
            ("commitments", "add", "--to", "Mara", "--src", "user", bad),
            ("card", "add", "Mallory", "--role", bad),
            ("rule", "add", "--id", "bad", "--message", bad, "--src", "user", "--forbid", "x"),
            ("standing", "add", "--id", "bad", "--kind", "chat", "--targets", "ops", "--src", bad),
            ("item", "set", "I1", "--state", "done", "--title", bad),
            ("receipt", "add", bad),
            ("log", "bad\x01value"),
            ("wrapup", "write", "--start", bad),
            ("action", "preview", "--kind", "other", "--target", bad),
        ]
        for args in cases:
            with self.subTest(args=args[:2]):
                result = self.command(*args, code=2)
                self.assertEqual(self.codes(result), {"bad_value"})

    def test_21_aliases_split_on_all_supported_commas(self) -> None:
        cards = self.write(".agents/skills/work/people-cards.md", CARDS)
        before = cards.read_bytes()
        result = self.command("card", "add", "Mallory", "--aliases", "safe、Mara", code=2)
        self.assertEqual(self.codes(result), {"card_exists"})
        self.assertEqual(cards.read_bytes(), before)
        self.command("card", "add", "Mallory", "--aliases", "safe、另名，third")
        text = cards.read_text()
        self.assertIn("Aliases: safe, 另名, third", text)
        for alias in ("safe", "另名", "third"):
            self.assertIn("## Mallory", self.command("card", alias)["card"]["person"])

    def test_22_checked_receipt_and_watch_files_contain_no_secret(self) -> None:
        self.configure("""[[command]]
id = "read"
run = "printf 'token=abcdefgh'"
""")
        receipt = self.command("receipt", "add", "proof", "--by-cmd", "read",
                               "--expect", "token=abcdefgh")["receipt"]
        evidence = (self.root / "data/evidence.json").read_text()
        self.assertNotIn("token=abcdefgh", evidence)
        self.assertIn("[redacted]", receipt["match"])
        self.assertNotIn("abcdefgh", receipt["match"])
        self.assertEqual(receipt["match_sha256"], hashlib.sha256(b"token=abcdefgh").hexdigest())
        process = self.invoke("watch", "--name", "secret", "--probe", "printf 'token=abcdefgh'",
                              "--done", "abcdefgh", "--state", "secret")
        self.assertEqual(process.returncode, 0)
        state = (self.root / ".wm/watch/secret").read_text()
        self.assertNotIn("token=abcdefgh", state)
        self.assertEqual(state, hashlib.sha256(b"token=abcdefgh").hexdigest() + "\n")

    def test_23_state_rows_must_have_exactly_six_cells(self) -> None:
        self.write(".agents/skills/work/state-machines.md",
                   EN + "| F1 | us ✅ | done | check | none | source | extra |\nOpen questions: none\n")
        linted = self.command("lint", "state-machines", code=2)
        self.assertIn("bad_row", self.codes(linted))
        shown = self.command("sm", "F1", code=2)
        self.assertIn("bad_row", self.codes(shown))

    def test_24_claimed_receipt_never_uses_standing_command_approval(self) -> None:
        self.configure("""[[command]]
id = "write"
run = "cat > delivered"
writes = true
""")
        self.write("rules.toml", """[[outbound]]
id = "proof"
when = "complete"
require_receipt = true
message = "need proof"
src = "user"

[[standing]]
id = "allowed"
kind = "command"
targets = ["write"]
src = "user"
""")
        claimed = self.command("receipt", "add", "I looked")["receipt"]
        result = self.command("action", "send", "--kind", "command", "--target", "write",
                              "--receipt", claimed["id"], payload="complete", code=1)
        self.assertEqual(result["status"], "needs_user")
        self.assertEqual(result["action"]["state"], "draft")
        self.assertFalse((self.root / "delivered").exists())

    def test_25_rule_from_note_rolls_back_when_second_replace_fails(self) -> None:
        note = self.command("note", "add", "Remember")["record"]
        notes = self.root / "worklog/inbox.md"
        before = notes.read_bytes()
        config = load_config(str(self.root), NOW)
        real_replace = os.replace
        failed = False

        def replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
            nonlocal failed
            if Path(target).resolve() == notes.resolve() and not failed:
                failed = True
                raise OSError("simulated second replacement failure")
            real_replace(source, target)

        with patch("wm.files.os.replace", side_effect=replace):
            code, output = add_rule(config, "outbound", {
                "id": "atomic", "message": "atomic", "src": note["id"], "forbid": "never",
            }, note["id"])
        self.assertEqual(code, 2)
        self.assertEqual(output["problems"][0]["where"], "worklog/inbox.md")
        self.assertEqual(notes.read_bytes(), before)
        self.assertFalse((self.root / "rules.toml").exists())

    def test_26_doctor_text_warns_for_both_unignored_paths(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.root, env=self.env, check=True)
        process = self.invoke("doctor")
        self.assertEqual(process.returncode, 1)
        text = process.stdout.decode()
        self.assertIn("warn state_not_ignored", text)
        self.assertIn("warn actions_not_ignored", text)

    def test_27_saved_run_output_is_mode_0600(self) -> None:
        self.configure("""[[command]]
id = "loud"
run = "printf abcdef"
max_output = 2
""")
        path = self.write(".wm/last/loud.txt", "old")
        path.chmod(0o644)
        process = self.invoke("run", "loud")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_text(), "abcdef")

    def test_readback_requires_zero_exit_and_unaltered_result(self) -> None:
        self.write(".agents/skills/work/people-cards.md", CARDS)
        cases = (
            ("printf '#1'; exit 1", "printf '#1'", "#1", "sent"),
            ("printf '%s' \"$WM_RESULT\"", "printf 'ghp_exampleToken'", "ghp_exampleToken", "sent"),
        )
        for index, (readback, send, raw, expected) in enumerate(cases):
            with self.subTest(index=index):
                (self.root / ".wm/actions.jsonl").unlink(missing_ok=True)
                self.configure(f'''[[channel]]
id = "ops"
send = {json.dumps(send)}
readback = {json.dumps(readback)}
''')
                self.write("rules.toml", """[[standing]]
id = "allowed"
kind = "chat"
targets = ["ops"]
src = "user"
""")
                result = self.command("action", "send", "--kind", "chat", "--target", "ops",
                                      "--to", "Mara", payload="hello", code=1)
                self.assertEqual(result["action"]["state"], expected)
                self.assertEqual(self.codes(result), {"readback_failed"})
                if index == 1:
                    self.assertNotEqual(result["action"]["result"], raw)


if __name__ == "__main__":
    unittest.main()
