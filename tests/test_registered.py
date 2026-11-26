from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_wm import CARDS, COMMIT, CONFIG, HANDOFF, NOW, REPO, SHIM


class RegisteredTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-registered-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = dict(os.environ, WM_HOSTNAME="agent.example", PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.root / "absent-global-config"))
        self.write("workplace.toml", CONFIG)
        self.log = self.root / ".wm/actions.jsonl"

    def write(self, path: str, data: str | bytes) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())
        return target

    def argv(self, *args: str, now: str = NOW) -> list[str]:
        return [str(SHIM), "--root", str(self.root), "--now", now, *args]

    def invoke(self, *args: str, payload: str | bytes = b"", now: str = NOW) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(*args, now=now), cwd=self.root, env=self.env, timeout=10,
                              input=payload if isinstance(payload, bytes) else payload.encode(), capture_output=True)

    def command(self, *args: str, code: int = 0, **kwargs: object) -> dict:
        proc = self.invoke("--json", *args, **kwargs)
        self.assertEqual(proc.returncode, code, (proc.stdout, proc.stderr))
        self.assertEqual(proc.stderr, b"")
        data = json.loads(proc.stdout)
        self.assertIs(data["ok"], code == 0)
        return data

    @staticmethod
    def codes(data: dict) -> set[str]:
        return {issue["code"] for issue in data["problems"]}

    def configure(self, rest: str = "") -> None:
        self.write("workplace.toml", CONFIG + rest)

    def preview(self, payload: str = "do the thing", *args: str,
                command: str | None = None, **kwargs: object) -> dict:
        command_args = ("--", command) if command is not None else ()
        return self.command("action", "preview", "--kind", "other", "--target", "tickets", *args,
                            *command_args, payload=payload, **kwargs)["action"]

    def approve(self, *ids: str, code: int = 0) -> dict:
        return self.command("action", "approve", *ids, "--by", "yes, proceed", code=code)

    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_catalog_projections_filters_and_registered_paths(self) -> None:
        self.configure("""resident_host = "resident"
[paths]
actions = "journal/events.jsonl"
[[system]]
id = "tracker"
what = "tickets"
reach = "direct"
hosts = ["AGENT.office.example"]
login = "single sign-on"
may_leave = "notes"
probe = "printf hidden-probe"
[[channel]]
id = "tracker"
what = "chat"
kind = "custom"
send = "printf hidden-send"
max_chars = 9
threads = true
system = "tracker"
[[command]]
id = "tracker"
what = "read"
run = "printf hidden-run"
system = "tracker"
""")
        data = self.command("catalog")
        self.assertEqual(data["systems"], [{"id": "tracker", "what": "tickets", "reach": "direct",
                                           "hosts": ["AGENT.office.example"], "login": "single sign-on",
                                           "may_leave": "notes", "probe": True}])
        self.assertEqual(data["channels"], [{"id": "tracker", "what": "chat", "kind": "custom",
                                            "send": True, "threads": True, "max_chars": 9}])
        self.assertEqual(data["commands"], [{"id": "tracker", "what": "read", "system": "tracker", "writes": False}])
        for group in ("channels", "systems", "commands"):
            with self.subTest(group=group):
                self.assertEqual(self.command("catalog", "--kind", group),
                                 {"ok": True, "status": "ok", "problems": [], group: data[group]})
        self.assertEqual(self.command("access", "list")["systems"], data["systems"])
        self.preview()
        self.assertTrue((self.root / "journal/events.jsonl").is_file())
        self.assertFalse(self.log.exists())
        self.assertIn("usage", self.codes(self.command("catalog", "--kind", "unknown", code=64)))

    def test_config_lint_reports_errors_and_secrets_without_leaking(self) -> None:
        self.configure("""
[[system]]
what = "missing id"
[[system]]
id = "invalid id"
[[system]]
id = "ok"
[[system]]
id = "ok"
[[channel]]
id = "bad-regex"
mention = "["
[[channel]]
id = "bad-system"
system = "missing"
[[command]]
id = "missing-run"
[[command]]
id = "bad-timeout"
run = "printf x"
timeout = 0
[[command]]
id = "bad-writes"
run = "echo token=unique-config-credential"
writes = "true"
""")
        found = self.command("lint", "config", code=2)["problems"]
        self.assertEqual(len(found), 8)
        self.assertTrue(all(issue["code"] == "bad_config" for issue in found))
        self.assertEqual(self.codes(self.command("catalog", code=2)), {"bad_config"})
        secret = self.command("lint", "secrets", code=2)
        self.assertEqual(self.codes(secret), {"secret_like"})
        self.assertNotIn("unique-config-credential", str(secret))
        self.assertTrue(secret["problems"][0]["where"].startswith("workplace.toml:"))
        self.configure('resident_host = 123\n')
        self.command("lint", "config", code=64)
        self.configure('[paths]\nactions = "../outside.jsonl"\n')
        self.command("lint", "config", code=64)

    def test_run_passthrough_guards_and_timeout_kills_group(self) -> None:
        self.write("read.sh", 'printf "%s\\n" "$PWD" "$1" "$2"; printf "\\377error" >&2; exit 7\n')
        self.configure("""
[[system]]
id = "remote"
hosts = ["AgEnT.office"]
[[command]]
id = "read"
system = "remote"
run = "sh read.sh"
[[command]]
id = "write"
writes = true
run = "touch should-not-run"
[[command]]
id = "slow"
timeout = 0.10
run = "(sleep 0.45; touch child-survived) & wait"
""")
        proc = self.invoke("run", "read", "--", "one two", "$(touch unquoted)")
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(proc.stdout.splitlines(), [os.fsencode(self.root), b"one two", b"$(touch unquoted)"])
        self.assertEqual(proc.stderr, b"\xfferror")
        self.assertFalse((self.root / "unquoted").exists())
        guarded = self.command("run", "write", code=2)
        self.assertEqual(self.codes(guarded), {"use_action"})
        self.assertFalse((self.root / "should-not-run").exists())
        self.env["WM_HOSTNAME"] = "wrong"
        guarded = self.command("run", "read", code=2)
        self.assertEqual(self.codes(guarded), {"wrong_host"})
        self.assertIn("AgEnT.office", guarded["problems"][0]["detail"])
        self.command("run", "absent", code=64)
        proc = self.invoke("run", "slow")
        self.assertEqual((proc.returncode, proc.stdout), (3, b""))
        self.assertEqual(self.codes(json.loads(proc.stderr)), {"timeout"})
        time.sleep(0.45)
        self.assertFalse((self.root / "child-survived").exists())

    def test_access_all_statuses_and_precedence(self) -> None:
        self.configure("""
[[system]]
id = "up"
hosts = ["AGENT.office"]
probe = "printf should-not-print; exit 0"
[[system]]
id = "down"
probe = "printf should-not-print >&2; exit 5"
[[system]]
id = "slow"
probe = "sleep 5"
[[system]]
id = "manual"
[[system]]
id = "remote"
hosts = ["other"]
probe = "touch should-not-probe"
""")
        data = self.command("access", "check", "--timeout", "0.1", code=4)
        self.assertEqual(data["systems"], [
            {"id": "up", "status": "reachable"},
            {"id": "down", "status": "unreachable", "exit": 5},
            {"id": "slow", "status": "unreachable", "exit": "timeout"},
            {"id": "manual", "status": "no_probe"},
            {"id": "remote", "status": "wrong_host", "hosts": ["other"]},
        ])
        self.assertEqual(self.codes(data), {"unreachable"})
        self.assertEqual(self.codes(self.command("access", "check", "remote", "manual", code=1)), {"no_probe"})
        self.assertEqual(self.command("access", "check", "manual", "up")["systems"], [
            {"id": "manual", "status": "no_probe"}, {"id": "up", "status": "reachable"}])
        self.assertFalse((self.root / "should-not-probe").exists())
        self.command("access", "check", "missing", "up", code=64)
        self.command("access", "check", "--timeout", "nan", code=64)

    def test_outbound_channel_limits_and_mention_fallback(self) -> None:
        self.configure(r"""
[[channel]]
id = "release"
mention = '!\w+'
max_chars = 7
src = "chat guide"
[[channel]]
id = "general"
""")
        self.write(".agents/skills/team/people-cards.md", CARDS)
        self.write("rules.toml", """[[outbound]]
id = "mention"
require_mention = true
src = "operator"
""")
        self.command("lint", "rules")
        base = ("outbound", "check", "--to", "Mara", "--channel")
        data = self.command(*base, "release", payload="hello @mara", code=2)
        self.assertEqual(data["channel"], {"id": "release", "what": "", "kind": "custom", "threads": False,
                                           "max_chars": 7})
        self.assertEqual({(v["rule"], v["check"]) for v in data["violations"]},
                         {("mention", "require_mention"), ("channel:release", "max_chars")})
        self.command(*base, "release", payload="!mara")
        self.command(*base, "general", payload="@mara")
        unknown = self.command(*base, "unknown", payload="@mara", code=1)
        self.assertEqual(self.codes(unknown), {"unknown_channel"})
        self.assertNotIn("channel", unknown)
        self.command("outbound", "check", "--to", "Mara", payload="@mara")

    def test_preview_exact_bytes_running_event_and_readback(self) -> None:
        self.write(".agents/skills/team/people-cards.md", CARDS)
        self.configure("""
[[command]]
id = "write"
writes = true
run = "python3 adapter.py"
""")
        payload = b"hello\r\n\xc3\xa9\x00\n"
        self.write("draft", payload)
        self.write("adapter.py", """import hashlib, json, os, sys
from pathlib import Path
events = [json.loads(line) for line in Path('.wm/actions.jsonl').read_text().splitlines()]
data = sys.stdin.buffer.read()
assert events[-1]['event'] == 'running'
assert events[-1]['id'] == os.environ['WM_ACTION_ID']
assert events[-1]['target'] == 'write'
assert events[-1]['command_sha256'] == hashlib.sha256(b'python3 adapter.py').hexdigest()
assert os.environ['WM_KIND'] == 'command'
assert os.environ['WM_TARGET'] == 'write'
assert os.environ['WM_TO'] == 'Mara'
assert os.environ['WM_PAYLOAD_SHA256'] == hashlib.sha256(data).hexdigest()
with Path('ran').open('ab') as out:
    out.write(data)
print('#delivery', end='')
""")
        action = self.command("action", "preview", "--kind", "command", "--target", "write",
                              "--to", "Mara", "--receipt", "r1", "--receipt", "r2", "--file", "draft")["action"]
        self.assertEqual(action["id"], "A-20260312-01")
        self.assertEqual(action["state"], "draft")
        self.assertEqual(action["expires"], "2026-03-12T19:00:00+09:00")
        self.assertEqual(action["receipts"], ["r1", "r2"])
        self.assertEqual(action["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(action["payload"].encode("utf-8"), payload)
        identity = action["id"]
        self.assertEqual(self.codes(self.command("action", "run", identity, code=2)), {"bad_state"})
        self.approve(identity)
        self.assertEqual(self.command("action", "show", identity)["action"]["approved_by"], "yes, proceed")
        sent = self.command("action", "run", identity)["action"]
        self.assertEqual((sent["state"], sent["result"]), ("sent", "#delivery"))
        rerun = self.command("action", "run", identity, code=2)
        self.assertEqual(self.codes(rerun), {"bad_state"})
        self.assertEqual((self.root / "ran").read_bytes(), payload)
        self.assertEqual([event["event"] for event in self.events()], ["draft", "approved", "running", "sent"])
        read = self.command("action", "readback", identity, "--proof", "checked in the destination")["action"]
        self.assertEqual((read["state"], read["proof"]), ("verified", "checked in the destination"))
        self.assertEqual(self.command("action", "list", "--open")["actions"], [])
        self.assertEqual(self.command("action", "list")["actions"][0]["state"], "verified")
        self.assertEqual(self.codes(self.command("action", "readback", identity, "--proof", "again", code=2)),
                         {"bad_state"})

    def test_failed_action_keeps_exit_and_stderr_and_cannot_repeat(self) -> None:
        command = "printf 'x' >> runs; printf 'failure' >&2; exit 7"
        identity = self.preview(command=command)["id"]
        self.approve(identity)
        self.command("action", "run", identity, "--", "sh", "-c", command, code=64)
        self.assertEqual(len(self.events()), 2)
        failed = self.command("action", "run", identity, code=2)
        self.assertEqual(self.codes(failed), {"action_failed"})
        self.assertEqual(failed["action"]["state"], "failed")
        self.assertEqual(failed["action"]["exit"], 7)
        self.assertEqual(failed["action"]["stderr"], "failure")
        second = self.command("action", "run", identity, code=2)
        self.assertEqual(self.codes(second), {"bad_state"})
        self.assertEqual((self.root / "runs").read_text(), "x")
        self.assertEqual(self.command("action", "show", identity)["action"]["state"], "failed")

    def test_action_timeout_kill_and_manual_resolution(self) -> None:
        for command, outcome in (("printf t >> runs; (sleep 0.45; touch stray) & wait", "sent"),
                                 ("printf k >> runs; kill -KILL $$", "failed"),
                                 ("printf c >> runs; sh -c 'kill -KILL $$'; exit $?", "failed")):
            with self.subTest(command=command):
                identity = self.preview(command=command)["id"]
                self.approve(identity)
                first = self.command("action", "run", identity, "--timeout", "0.15", code=4)
                self.assertEqual(self.codes(first), {"unknown"})
                self.assertEqual(self.events()[-1]["event"], "running")
                self.assertEqual(self.command("action", "show", identity)["action"]["state"], "unknown")
                before = (self.root / "runs").read_bytes()
                repeat = self.command("action", "run", identity, code=4)
                self.assertEqual(self.codes(repeat), {"unknown"})
                self.assertEqual((self.root / "runs").read_bytes(), before)
                self.assertFalse((self.root / "duplicate").exists())
                settled = self.command("action", "resolve", identity, "--state", outcome,
                                       "--proof", "manual destination check")["action"]
                self.assertEqual((settled["state"], settled["proof"]), (outcome, "manual destination check"))
                self.assertEqual(self.codes(self.command("action", "resolve", identity, "--state", outcome,
                                                        "--proof", "repeat", code=2)), {"bad_state"})
        self.assertEqual((self.root / "runs").read_text(), "tkc")
        self.assertFalse((self.root / "stray").exists())

    def test_send_timeout_returns_the_unknown_action_id(self) -> None:
        draft = self.command("action", "send", "--kind", "other", "--target", "tickets", "--timeout", "0.1",
                             "--", "sleep 5", code=1)["action"]
        self.approve(draft["id"])
        sent = self.command("action", "run", draft["id"], "--timeout", "0.1", code=4)
        self.assertEqual(self.codes(sent), {"unknown"})
        shown = self.command("action", "show", draft["id"])["action"]
        self.assertEqual((shown["id"], shown["state"]), ("A-20260312-01", "unknown"))

    def test_action_diagnostics_keep_at_most_2000_characters(self) -> None:
        sent = self.preview(command="python3 -c \"print('é' * 2005)\"")["id"]
        failed = self.preview(command="python3 -c \"import sys; print('î' * 2005, file=sys.stderr); sys.exit(1)\"")["id"]
        self.approve(sent, failed)
        good = self.command("action", "run", sent)
        bad = self.command("action", "run", failed, code=2)
        self.assertEqual(good["action"]["result"], "é" * 2000)
        self.assertEqual(bad["action"]["stderr"], "î" * 2000)

    def test_batch_approval_expiry_cancel_and_missing_ids(self) -> None:
        first, second, third = (self.preview()["id"] for _ in range(3))
        cancelled = self.command("action", "cancel", second)["action"]
        self.assertEqual(cancelled["state"], "cancelled")
        mixed = self.approve(first, second, third, "missing", code=2)
        self.assertEqual([(item["id"], item["ok"]) for item in mixed["actions"]],
                         [(first, True), (second, False), (third, True), ("missing", False)])
        self.assertEqual(self.codes(mixed), {"bad_state", "unknown_id"})
        self.assertEqual(self.command("action", "cancel", third)["action"]["state"], "cancelled")
        self.assertEqual(self.codes(self.command("action", "cancel", third, code=2)), {"bad_state"})
        expiring = self.preview("short-lived", "--ttl", "1")["id"]
        later = "2026-03-12T19:00:01+09:00"
        self.assertEqual(self.command("action", "show", expiring, now=later)["action"]["state"], "expired")
        self.assertEqual(self.codes(self.command("action", "approve", expiring, "--by", "yes", code=2, now=later)),
                         {"expired"})
        self.assertEqual(self.codes(self.command("action", "run", first, code=2, now=later)),
                         {"expired"})
        self.assertEqual(self.command("action", "list", "--open", now=later)["actions"], [])
        self.assertFalse((self.root / "late").exists())
        self.assertEqual(self.codes(self.command("action", "show", "missing", code=2)), {"unknown_id"})

    def test_action_target_validation_and_host_guard(self) -> None:
        self.configure("""
[[system]]
id = "remote"
hosts = ["other"]
[[channel]]
id = "chat"
[[command]]
id = "read"
run = ":"
[[command]]
id = "write"
system = "remote"
run = "touch ran"
writes = true
""")
        for kind, target in (("chat", "absent"), ("command", "absent"), ("command", "read")):
            with self.subTest(kind=kind, target=target):
                self.command("action", "preview", "--kind", kind, "--target", target, code=64)
        self.assertFalse(self.log.exists())
        identity = self.command("action", "preview", "--kind", "command", "--target", "write")["action"]["id"]
        self.approve(identity)
        self.assertEqual(self.codes(self.command("action", "run", identity, code=2)), {"wrong_host"})
        self.assertEqual(self.events()[-1]["event"], "approved")
        self.assertFalse((self.root / "ran").exists())

    def test_standing_approval_match_and_each_nonmatch(self) -> None:
        self.configure('''[[channel]]
id = "tickets"
send = ":"
[[channel]]
id = "second"
send = ":"
[[channel]]
id = "other"
send = ":"
[[command]]
id = "tickets"
run = ":"
writes = true
''')
        self.write(".agents/skills/team/people-cards.md", CARDS)
        self.write("rules.toml", r"""[[standing]]
id = "ack"
kind = "chat"
targets = ["tickets", "second"]
to = ["Mara"]
match = '(?i)^ack\b'
max_chars = 6
expires = "2026-03-12"
src = "User said acknowledgements are approved"
""")
        cases = [
            ("match", "chat", "tickets", "mAra", "ACK ok", NOW, True),
            ("another target", "chat", "second", "Mara", "ack", NOW, True),
            ("kind", "command", "tickets", "Mara", "ack", NOW, False),
            ("target", "chat", "other", "Mara", "ack", NOW, False),
            ("recipient", "chat", "tickets", "Jun", "ack", NOW, False),
            ("missing recipient", "chat", "tickets", "", "ack", NOW, False),
            ("regex", "chat", "tickets", "Mara", "ok", NOW, False),
            ("length", "chat", "tickets", "Mara", "ack yes", NOW, False),
            ("past last day", "chat", "tickets", "Mara", "ack", "2026-03-13T00:00:00+09:00", False),
        ]
        for label, kind, target, to, payload, now, matches in cases:
            with self.subTest(label=label):
                action = self.command("action", "preview", "--kind", kind, "--target", target, "--to", to,
                                      payload=payload, now=now)["action"]
                self.assertEqual(action["state"], "approved" if matches else "draft")
                self.assertEqual(action.get("approved_by"), "standing:ack" if matches else None)
        self.command("lint", "rules")
        past = self.command("lint", "rules", code=1, now="2026-03-13T00:00:00+09:00")
        self.assertEqual(self.codes(past), {"standing_expired"})

    def test_standing_rule_errors(self) -> None:
        path = self.write("rules.toml", "")
        base = 'id = "standing"\nkind = "chat"\ntargets = ["on-call"]\nsrc = "user"\n'
        for missing in base.splitlines(keepends=True):
            with self.subTest(missing=missing):
                path.write_text("[[standing]]\n" + base.replace(missing, ""))
                self.assertEqual(self.codes(self.command("lint", "rules", code=2)), {"bad_rule"})
        for extra, issue in [('match = "["', "bad_regex"), ('expires = "2026-02-30"', "bad_rule"),
                             ('max_chars = -1', "bad_rule"), ('to = "Mara"', "bad_rule")]:
            with self.subTest(extra=extra):
                path.write_text("[[standing]]\n" + base + extra)
                self.assertEqual(self.codes(self.command("lint", "rules", code=2)), {issue})
        path.write_text(("[[standing]]\n" + base) * 2)
        self.assertEqual(self.codes(self.command("lint", "rules", code=2)), {"duplicate_rule"})

    def test_send_stops_with_needs_user_and_never_auto_approves_violations(self) -> None:
        self.configure("""[[channel]]
id = "on-call"
max_chars = 9
send = "cat > delivered"
""")
        self.write("rules.toml", """[[standing]]
id = "ack"
kind = "chat"
targets = ["on-call"]
to = []
match = "^ack"
src = "user"
""")
        base = ("action", "send", "--kind", "chat", "--target", "on-call")
        blocked = self.command(*base, code=2, payload="ack, too long")
        self.assertEqual(self.codes(blocked), {"outbound_violation"})
        self.assertEqual(blocked["violations"][0]["check"], "max_chars")
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / "delivered").exists())
        self.command(*base, "--", "touch should-not-run", code=64, payload="question")
        manual = self.command(*base, code=1, payload="question")
        self.assertEqual(manual["status"], "needs_user")
        self.assertEqual(manual["action"]["payload"], "question")
        self.assertEqual(manual["action"]["state"], "draft")
        self.assertFalse((self.root / "should-not-run").exists())
        sent = self.command(*base, payload="ack, ok")["action"]
        self.assertEqual((sent["state"], sent["approved_by"]), ("sent", "standing:ack"))
        self.assertEqual((self.root / "delivered").read_text(), "ack, ok")
        fixed = self.command(*base, payload="ack now")["action"]
        self.assertEqual(fixed["state"], "sent")
        self.assertEqual((self.root / "delivered").read_text(), "ack now")

    def test_project_views_and_wrapup_problems_without_payloads(self) -> None:
        self.write("worklog/2026-03-12.md", HANDOFF)
        self.write("worklog/inbox.md", "")
        self.write("worklog/commitments.md", COMMIT)
        self.write("data/evidence.json", "{}")
        sent = self.preview("private draft #0", command="printf accepted")["id"]
        unknown = self.preview("private draft #1", command="kill -TERM $$")["id"]
        draft = self.preview("private draft #2")["id"]
        self.approve(sent, unknown)
        self.command("action", "run", sent)
        warned = self.command("wrapup", "check", code=1)
        self.assertEqual(warned["result"], "WARN")
        self.assertEqual(self.codes(warned), {"action_unverified"})
        self.command("action", "run", unknown, code=4)
        blocked = self.command("wrapup", "check", code=2)
        self.assertEqual(blocked["result"], "FAIL")
        self.assertEqual(self.codes(blocked), {"action_unknown", "action_unverified"})
        brief = self.command("brief")
        self.assertEqual(brief["hostname"], "agent.example")
        self.assertEqual([entry["id"] for entry in brief["actions"]], [sent, unknown, draft])
        self.assertIn(unknown, self.invoke("brief", "--hook").stdout.decode())
        upcoming = self.command("next")
        self.assertEqual([entry["id"] for entry in upcoming["now"]], [unknown, "C-20260312-01"])
        listing = self.command("action", "list", "--open")
        self.assertNotIn("private draft", str((brief, upcoming, listing)))
        self.command("action", "readback", sent, "--proof", "#1")
        self.command("action", "resolve", unknown, "--state", "failed", "--proof", "destination empty")
        self.assertEqual(self.command("wrapup", "check")["result"], "PASS")

    def test_doctor_reports_custom_action_path_ignore_status(self) -> None:
        self.configure('resident_host = "resident"\n[paths]\nactions = "journal/events.jsonl"\n')
        data = self.command("doctor", code=1)
        self.assertIsNone(data["actions_ignored"])
        self.assertEqual(data["resident_host"], "resident")
        subprocess.run(["git", "init", "-q"], cwd=self.root, env=self.env, check=True)
        self.write(".git/info/exclude", "/.wm/\n")
        data = self.command("doctor", code=1)
        self.assertTrue(data["state_ignored"])
        self.assertFalse(data["actions_ignored"])
        self.write(".git/info/exclude", "/.wm/\n/journal/events.jsonl\n")
        data = self.command("doctor", code=1)
        self.assertTrue(data["actions_ignored"])
        self.assertFalse((self.root / "journal").exists())

    def test_action_log_corruption_fails_closed(self) -> None:
        identity = self.preview()["id"]
        self.approve(identity)
        with self.log.open("ab") as handle:
            handle.write(b'{"event":"running"')
        before = self.log.read_bytes()
        for args in (("list",), ("show", identity), ("approve", identity, "--by", "yes"),
                     ("run", identity)):
            with self.subTest(args=args):
                self.assertEqual(self.codes(self.command("action", *args, code=4)), {"unreadable"})
        self.assertEqual(self.log.read_bytes(), before)
        self.assertFalse((self.root / "unsafe").exists())

    def test_generated_output_redacts_secrets_commands_and_other_payloads(self) -> None:
        line = "sh inspect.sh"
        self.configure(f'[[command]]\nid = "echo"\nrun = {json.dumps(line)}\nwrites = true\n')
        self.write("inspect.sh", "printf '%s\\n' 'sh inspect.sh' 'token=unique-action-credential' "
                   "'private prior payload' 'ghp_fakeExampleToken'")
        self.preview("private prior payload")
        identity = self.command("action", "preview", "--kind", "command", "--target", "echo",
                                payload="visible own payload")["action"]["id"]
        self.approve(identity)
        output = self.command("action", "run", identity)
        for value in (line, "unique-action-credential", "private prior payload", "ghp_fakeExampleToken"):
            self.assertNotIn(value, str(output))
        self.assertEqual(output["action"]["payload"], "visible own payload")
        self.assertIn("[redacted]", output["action"]["result"])

    def test_concurrent_processes_append_whole_unique_events(self) -> None:
        args = self.argv("--json", "action", "preview", "--kind", "other", "--target", "tickets")
        payloads = [b"x" * 200000, b"y" * 200000]
        inputs = [self.write(f"draft-{index}", payload) for index, payload in enumerate(payloads)]
        first = subprocess.Popen([*args, "--file", str(inputs[0])], cwd=self.root, env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with first, subprocess.Popen([*args, "--file", str(inputs[1])], cwd=self.root, env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) as second:
            for proc in (first, second):
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout)["action"]["state"], "draft")
        events = self.events()
        self.assertEqual([event["id"] for event in events], ["A-20260312-01", "A-20260312-02"])
        self.assertCountEqual([event["payload"].encode() for event in events], payloads)

    def test_concurrent_run_observes_running_before_first_finishes(self) -> None:
        script = 'printf x >> ran; until [ -e finish ]; do sleep 0.02; done'
        identity = self.preview(command=script)["id"]
        self.approve(identity)
        args = self.argv("--json", "action", "run", identity, "--timeout", "4")
        with subprocess.Popen(args, cwd=self.root, env=self.env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as first:
            end = time.monotonic() + 3
            try:
                while not (self.root / "ran").exists() and time.monotonic() < end:
                    time.sleep(0.02)
                self.assertTrue((self.root / "ran").exists())
                second = self.command("action", "run", identity, code=4)
                self.assertEqual(self.codes(second), {"unknown"})
            finally:
                self.write("finish", "")
            stdout, stderr = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)["action"]["state"], "sent")
        self.assertEqual((self.root / "ran").read_text(), "x")
