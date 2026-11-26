from __future__ import annotations

import io
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["WM_TEST_CLOCK"] = "1"


REPO = Path(__file__).resolve().parents[1]
SHIM = REPO / "bin" / "wm"
NOW = "2026-03-12T18:30:00+09:00"
CONFIG = '[workplace]\nname = "tidepool"\ntimezone = "Asia/Tokyo"\n'
EN = """| State | Trigger / who | Next | Verified by | Pitfalls | Source |
| --- | --- | --- | --- | --- | --- |
"""
ZH = """| 狀態 | 觸發／誰 | 下一個狀態 | 怎麼驗證做到了 | 常見坑 | 來源 |
| --- | --- | --- | --- | --- | --- |
"""
NOTE = '- [ ] [note id=N-20260312-01 kind=pitfall who=us src="build log 2026-03-12"] Version is set twice.'
COMMIT = "- [ ] [commitment id=C-20260312-01 due=2026-03-12 to=Mara ball=us src=#7] Send the checklist."
HANDOFF = "## Start here\nRead the release log.\n\n## Unpushed commits\nnone\n"
RULE = r'''[[outbound]]
id = "claims"
when = '(?i)\bdone\b'
channels = ["release"]
to = ["Mara", "M"]
require_receipt = true
require = '@\w+'
forbid = '\bASAP\b'
max_chars = 20
message = "Check the release evidence."
src = "N-20260312-03"
'''
CARDS = """# Cards
## Before drafting
- Read the original thread.

## Mara
Aliases: M, Mara T
Use a thread reply.
### Proof
Read the receipt.

## Jun
Keep the first line short.
"""

sys.path.insert(0, str(REPO / "src"))
from wm import cli  # noqa: E402


class WMTest(unittest.TestCase):

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-test-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / "project"
        self.root.mkdir()
        self.env = dict(os.environ)
        self.env.pop("WM_ROOT", None)
        self.env.pop("WM_TEMPLATES", None)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.env["GIT_CONFIG_GLOBAL"] = str(self.workspace / "absent-global-config")
        self.env["GIT_CONFIG_SYSTEM"] = str(self.workspace / "absent-system-config")
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.env["GIT_TERMINAL_PROMPT"] = "0"
        self.write("workplace.toml", CONFIG)

    def write(self, path: str, contents: str | bytes) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents, encoding="utf-8")
        return target

    def evidence(self, **data: object) -> Path:
        return self.write("data/evidence.json", json.dumps(data))

    def invoke(
        self, *args: str, stdin: str = "", root: bool = True, cwd: Path | None = None,
        env: dict[str, str] | None = None, module: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "-m", "wm"] if module else [str(SHIM)]
        environment = self.env | (env or {})
        if module:
            environment["PYTHONPATH"] = str(REPO / "src")
        flags = ["--now", NOW] + (["--root", str(self.root)] if root else [])
        return subprocess.run(
            command + flags + list(args), cwd=cwd or self.workspace, env=environment,
            input=stdin, capture_output=True, encoding="utf-8", timeout=10, check=False,
        )

    def command(self, *args: str, code: int = 0, **kwargs: object) -> dict:
        run = self.invoke("--json", *args, **kwargs)
        self.assertEqual(run.returncode, code, run.stdout + run.stderr)
        self.assertEqual(run.stderr, "")
        data = json.loads(run.stdout)
        self.assertIs(data["ok"], code == 0)
        self.assertEqual(data["status"], {0: "ok", 1: "warn", 2: "fail", 4: "unverified", 64: "fail"}[code])
        for issue in data["problems"]:
            self.assertEqual(set(issue), {"code", "level", "where", "detail"})
        return data

    def codes(self, data: dict) -> set[str]:
        return {item["code"] for item in data["problems"]}

    def git(self, *args: str, path: Path | None = None) -> str:
        process = subprocess.run(
            ["git", "-C", str(path or self.root), *args], env=self.env, capture_output=True,
            text=True, timeout=10, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return process.stdout.strip()

    def repo(self, upstream: bool = True) -> None:
        self.git("init", "-q", "-b", "main")
        self.git("config", "--local", "user.name", "WM Test")
        self.git("config", "--local", "user.email", "wm-test@example.invalid")
        self.git("add", "workplace.toml")
        self.git("commit", "-qm", "Base")
        if upstream:
            remote = self.workspace / "upstream.git"
            self.git("init", "-q", "--bare", str(remote), path=self.workspace)
            self.git("remote", "add", "origin", str(remote))
            self.git("push", "-qu", "origin", "main")

    def test_root_precedence_pretty_and_module_entry_point(self) -> None:
        nested = self.root / "nested" / "deep"
        nested.mkdir(parents=True)
        outside = self.workspace / "no-project"
        outside.mkdir()
        self.command("lint", "records", root=False, cwd=nested, module=True)
        self.command("lint", "records", root=False, env={"WM_ROOT": str(self.root)})
        self.command("lint", "records", env={"WM_ROOT": str(outside)})
        self.command("lint", "records", code=64, root=False, cwd=nested, env={"WM_ROOT": str(outside)})
        run = self.invoke("lint", "records", "--pretty")
        self.assertTrue(run.stdout.startswith('{\n  "ok": true,'))
        self.command("not-a-command", code=64)
        self.command("lint", "records", "--now", "incorrect", code=64)

    def test_invalid_config(self) -> None:
        for data in ('[workplace\n', '[workplace]\nname = "Wrong"\n',
                     CONFIG + '[paths]\nnotes = "../outside.md"\n',
                     CONFIG + '[next]\nstale_hours = -1\n'):
            with self.subTest(data=data):
                self.write("workplace.toml", data)
                report = self.command("doctor", code=64)
                self.assertEqual(self.codes(report), {"usage"})

    def test_init_copies_its_own_templates_and_refuses_conflicts(self) -> None:
        templates = self.workspace / "templates"
        lang = templates / "en"
        nested = lang / ".agents" / "team-__name__"
        nested.mkdir(parents=True)
        (lang / "workplace.toml").write_text('[workplace]\nname = "{{name}}"\n', encoding="utf-8")
        (nested / "__name__.txt").write_text("Welcome {{name}}.", encoding="utf-8")
        (lang / ".hidden").write_bytes(b"\x00binary")
        (lang / "empty").mkdir()
        dest = self.workspace / "new"
        env = {"WM_TEMPLATES": str(templates)}
        info = self.command("init", "--name", "fresh-2", "--dir", str(dest), root=False, env=env)
        self.assertEqual(set(info["files"]), {".agents/team-fresh-2/fresh-2.txt", ".hidden", "workplace.toml"})
        self.assertEqual((dest / ".agents/team-fresh-2/fresh-2.txt").read_text(), "Welcome fresh-2.")
        self.assertEqual((dest / ".hidden").read_bytes(), b"\x00binary")
        self.assertTrue((dest / "empty").is_dir())
        self.command("init", "--name", "fresh-2", "--dir", str(dest), code=64, env=env)
        self.command("init", "--name", "fresh-2", "--lang", "zh-TW", root=False, code=64, env=env)
        self.command("init", "--name", "Fresh_2", root=False, code=64, env=env)

    def test_init_uses_cwd_and_selected_language(self) -> None:
        templates = self.workspace / "templates"
        lang = templates / "zh-TW"
        lang.mkdir(parents=True)
        (lang / "workplace.toml").write_text('[workplace]\nname = "{{name}}"\n', encoding="utf-8")
        target = self.workspace / "new"
        target.mkdir()
        self.command("init", "--name", "tw", "--lang", "zh-TW", cwd=target, root=False,
                     env={"WM_TEMPLATES": str(templates)})
        self.assertIn('name = "tw"', (target / "workplace.toml").read_text())

    def test_note_commands_and_quoted_fields(self) -> None:
        path = self.write("worklog/inbox.md", NOTE + "\n" + NOTE.replace("-01", "-03") + "\nIgnored")
        record = self.command("note", "add", "--kind", "correction", "--who", 'Mara "M"',
                              "--src", 'log [draft] \\ release', "Reply in the thread.")["record"]
        self.assertEqual(record["id"], "N-20260312-02")
        self.assertEqual(record["src"], 'log [draft] \\ release')
        self.assertFalse(record["checked"])
        notes = self.command("note", "list", "--open", "--kind", "correction")["notes"]
        self.assertEqual(notes, [record])
        self.assertEqual(self.command("note", "list")["notes"][0]["src"], "build log 2026-03-12")

        copy = path.read_bytes()
        target = ".agents/skills/with space/SKILL.md"
        missing = self.command("note", "file", record["id"], "--to", target, code=2)
        self.assertEqual(self.codes(missing), {"dest_missing"})
        self.assertEqual(path.read_bytes(), copy)
        self.write(target, f"Rule based on {record['id']}9.\n")
        filed = self.command("note", "file", record["id"], "--to", target)["record"]
        self.assertTrue(filed["checked"])
        self.assertEqual(filed["filed"], target)
        self.assertEqual(self.command("note", "list", "--open", "--kind", "correction")["notes"], [])
        self.assertIn('filed=".agents/skills/with space/SKILL.md"', path.read_text())

    def test_commitment_commands_due_filter_and_timezone(self) -> None:
        for number, due in enumerate(("2026-03-11", "2026-03-12", "2026-03-14", "2026-03-15", None), 1):
            options = ["--due", due] if due else []
            entry = self.command("commitments", "add", "--to", "Mara T", "--src", f"#{number}",
                                 *options, f"Send item {number}.")["record"]
            self.assertEqual(entry["id"], f"C-20260312-0{number}")
            self.assertEqual(entry["due"], due or "none")
            self.assertEqual(entry["ball"], "us")
        data = self.command("commitments", "list", "--due-within", "2d")
        self.assertEqual([item["due"] for item in data["commitments"]], ["2026-03-11", "2026-03-12", "2026-03-14"])
        self.assertEqual([item["id"] for item in data["undated"]], ["C-20260312-05"])
        proof = "thread #44"
        done = self.command("commitments", "done", "C-20260312-02", "--proof", proof)["record"]
        self.assertEqual((done["checked"], done["proof"]), (True, proof))
        self.assertEqual(len(self.command("commitments", "list", "--open")["commitments"]), 3)
        self.assertEqual(len(self.command("commitments", "list")["commitments"]), 4)
        unknown = self.command("commitments", "done", "C-20260312-99", "--proof", proof, code=2)
        self.assertEqual(self.codes(unknown), {"unknown_id"})
        entry = self.command("commitments", "add", "--now", "2026-03-12T18:00:00Z", "--to", "Jun",
                             "--src", "#4", "--ball", "them", "Wait for Jun.")["record"]
        self.assertEqual(entry["id"], "C-20260313-01")
        self.assertEqual(entry["ball"], "them")
        self.command("commitments", "add", "--to", "Jun", "--src", "#4", "--due", "2026-02-30", "Wait.", code=64)
        self.command("commitments", "list", "--due-within", "-1d", code=64)

    def test_concurrent_adds_allocate_unique_ids(self) -> None:
        def add(index: int) -> str:
            return self.command("note", "add", "--kind", "decision", "--src", f"#{index}",
                                f"Decision {index}")["record"]["id"]

        with ThreadPoolExecutor(max_workers=6) as pool:
            identities = list(pool.map(add, range(6)))
        self.assertEqual(sorted(identities), [f"N-20260312-0{index}" for index in range(1, 7)])
        self.assertEqual(len(self.command("note", "list")["notes"]), 6)

    def test_lint_records_all_codes_and_levels(self) -> None:
        self.write("worklog/inbox.md", "\n".join([
            "Header", "Text [note ignored]", "- just a list item",
            "- [ ] [note no-field] Bad.", NOTE, NOTE.replace("20260312", "20260311"),
            '- [x] [note id=N-20260312-07 src=#1 filed=missing.md] Target gone.',
            '- [x] [note id=N-20260312-08 src=#2 filed=existing.md] Citation gone.',
        ]))
        self.write("existing.md", "Something else.")
        self.write("worklog/commitments.md", "\n".join([
            COMMIT, COMMIT.replace(" src=#7", "").replace("2026-03-12", "yesterday"),
            "- [x] [commitment id=C-20260310-01 due=none to=Jun ball=us src=#3] No proof.",
            "- [ ] [commitment id=C-20260311-01 due=2026-03-11 to=Jun ball=US src=#4] By us.",
            "- [ ] [commitment id=C-20260311-02 due=2026-03-11 to=Jun ball=them src=#5] By them.",
        ]))
        data = self.command("lint", "records", code=2)
        self.assertEqual(self.codes(data), {"bad_record", "duplicate_id", "missing_src", "bad_due",
                                             "done_without_proof", "overdue", "filed_target_missing",
                                             "filed_id_not_found", "note_unfiled"})
        for code in ("overdue", "note_unfiled"):
            self.assertEqual([item["level"] for item in data["problems"] if item["code"] == code],
                             ["fail", "warn"] if code == "overdue" else ["warn", "fail"])
        self.assertIn("worklog/inbox.md:4", [item["where"] for item in data["problems"]])
        self.write("worklog/inbox.md", b"\xff\xfe")
        self.assertIn("unreadable", self.codes(self.command("lint", "records", code=4)))

    def test_lint_empty_ledgers_and_all_checks(self) -> None:
        self.command("lint", "records")
        self.command("lint", "rules")
        self.command("lint", "global")
        no_tables = self.command("lint", "state-machines", code=4)
        self.assertEqual(self.codes(no_tables), {"no_state_machines"})
        self.write("worklog/inbox.md", NOTE)
        self.write("worklog/key.txt", "api_key = 'synthetic-value'")
        data = self.command("lint", code=4)
        self.assertEqual(self.codes(data), {"note_unfiled", "no_state_machines", "secret_like"})

    def test_lint_state_machines_both_languages_and_codes(self) -> None:
        self.write(".agents/skills/a/state-machines.md", EN
                   + "| R0 Start | us ✅ open | R1/done,R2 | Log | None | #1 |\n"
                   + "| R1 Review | Mara | done | Log | None | #1 |\n"
                   + "| R2 Ship | 我方處理 🙋 | 完成 | Log | None | #1 |\n\nOpen questions: none\n")
        self.write(".agents/skills/b/state-machines.md", ZH
                   + "| QA12 驗證 | 我方讀取 📖 | — | 紀錄 | 無 | #2 |\n\n待確認：無\n"
                   + "```md\n" + EN + "| bogus | us | BAD | - | - | - |\n```\n")
        self.command("lint", "state-machines")
        self.write(".agents/skills/b/state-machines.md", ZH
                   + "| QA12 驗證 | 我方讀取 | ZZ21, end | - | 無 | |\n"
                   + "| R0 重複 | 對方 | - | 紀錄 | 無 | #2 |\n"
                   + "| invalid | us 📖 | 完成 | 紀錄 | 無 | #2 |\n"
                   + "## A new heading\nOpen questions: too late\n")
        data = self.command("lint", "state-machines", code=2)
        self.assertEqual(self.codes(data), {"bad_state_id", "duplicate_state", "missing_verify", "missing_source",
                                             "unknown_next", "missing_auth", "no_open_questions"})

    def test_lint_uses_configured_machine_files(self) -> None:
        self.write("workplace.toml", CONFIG + '[paths]\nstate_machines = ["flow.md", "absent.md"]\n')
        self.write(".agents/skills/unused/state-machines.md", EN + "| bad | them | end | - | - | - |\n")
        self.write("flow.md", EN + "| A0 Verify | them | end | proof | - | #8 |\nOpen questions: none\n")
        self.command("lint", "state-machines")

    def test_lint_rules_all_codes(self) -> None:
        self.write("rules.toml", RULE + '''
[[outbound]]
id = "claims"
require = "["
src = "note"

[[outbound]]
id = "unchecked"
require_receipt = false

[[outbound]]
id = "wrong-type"
max_chars = true
''')
        data = self.command("lint", "rules", code=2)
        self.assertEqual(self.codes(data), {"bad_rule", "duplicate_rule", "rule_without_src", "bad_regex",
                                             "rule_without_check"})
        self.write("rules.toml", "[[broken")
        self.assertEqual(self.codes(self.command("lint", "rules", code=4)), {"unreadable"})

    def test_secret_scans_categories_without_disclosing_values(self) -> None:
        samples = [
            "-----BEGIN OPENSSH PRIVATE KEY-----", "otpauth://totp/demo?secret=AAAAAAAA",
            "ghp_SYNTHETIC_1234", "github_pat_SYNTHETIC_1234", "glpat-SYNTHETIC-1234", "sk-SYNTHETIC-1234",
            "xoxb-SYNTHETIC-1234", "xoxp-SYNTHETIC-1234", "AKIA0123456789ABCDEF",
            "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhIn0.fake", 'PASSWORD = "long string"',
            'passwd: "dummy888"', "secret=12345678", "token: TESTWORD", "api_key='fake-value'",
        ]
        self.write(".agents/skills/sample/leaks.txt", "\n".join(samples[:6]) + "\n")
        self.write("worklog/secret.txt", "\n".join(samples[6:-1]) + "\n")
        self.write("rules.toml", samples[-1] + "\n")
        self.write("worklog/image.bin", b"\x00" + samples[4].encode())
        run = self.invoke("lint", "secrets", "--json")
        self.assertEqual(run.returncode, 2)
        self.assertEqual(len(json.loads(run.stdout)["problems"]), len(samples))
        for sample in samples:
            self.assertNotIn(sample.split("=", 1)[-1].strip().strip('"\''), run.stdout + run.stderr)
        self.assertEqual(self.codes(json.loads(run.stdout)), {"secret_like"})

    def test_global_terms_report_only_line_numbers(self) -> None:
        self.write("workplace.toml", CONFIG + '[lint]\nglobal_skills = ["shared"]\nprivate_terms = "private.txt"\n')
        self.write("private.txt", "\nFake-Pelican\nDo-Not-Print-This\n")
        self.write("shared/example/SKILL.md", "A harmless line.\nFake-Pelican, Do-Not-Print-This\n")
        data = self.command("lint", "global", code=2)
        self.assertEqual(self.codes(data), {"private_term_in_global"})
        self.assertEqual([issue["detail"] for issue in data["problems"]],
                         ["private term from line 2 of terms file", "private term from line 3 of terms file"])
        self.assertNotIn("Fake-Pelican", json.dumps(data))
        self.assertNotIn("Do-Not-Print-This", json.dumps(data))

    def test_next_outputs_all_statuses_preserving_now_order(self) -> None:
        self.write(".agents/skills/sample/state-machines.md", EN + """
| R0 Ship | us ✅ deploy | R1 | check | wait | #1 |
| R1 Review | 我方審查 🙋 | R2 | check | wait | #2 |
| R2 Query | US 📖 consult | R3 | check | wait | #3 |
| R3 Wait | them ✅ publish | R4 | check | wait | #4 |
| R4 Look | me inspect | end | check | wait | #5 |

Open questions: none
""".lstrip())
        self.write("worklog/commitments.md", COMMIT + "\n"
                   + COMMIT.replace("-01", "-02").replace("ball=us", "ball=them") + "\n")
        fresh = "2026-03-12T13:00:00"
        item = lambda ref, state, stamp=fresh: {"id": ref, "title": "Tracked " + ref, "state": state, "at": stamp}
        self.evidence(items=[item("user", "R1"), item("read", "R2"), item("wait", "R3"), item("default", "R4"),
                             item("do", "R0"), item("finish", "DONE", None), item("unknown", "X8"),
                             item("old", "R0", "2026-03-01T19:00:00+09:00"), item("bad-date", "R1", "invalid"),
                             {"id": "no-date", "state": "R4"}])
        data = self.command("next", code=4)
        output = {entry["id"]: entry for entry in data["items"]}
        self.assertEqual({ref: entry["status"] for ref, entry in output.items()},
                         {"user": "needs_user", "read": "read_only", "wait": "waiting", "default": "read_only",
                          "do": "actionable", "unknown": "unverified", "old": "unverified",
                          "bad-date": "unverified", "no-date": "unverified"})
        self.assertEqual(output["wait"]["waiting_on"], "them ✅ publish")
        self.assertEqual({key: output["do"][key] for key in ("flow", "state", "state_name", "next", "verify", "pitfalls",
                                                           "source", "trigger")},
                         {"flow": "R", "state": "R0", "state_name": "Ship", "next": "R1", "verify": "check",
                          "pitfalls": "wait", "source": "#1", "trigger": "us ✅ deploy"})
        self.assertEqual([entry["id"] for entry in data["now"]], ["C-20260312-01", "do", "user"])
        all_items = self.command("next", "--all", code=4)["items"]
        self.assertEqual(next(entry["status"] for entry in all_items if entry["id"] == "finish"), "done")

    def test_next_evidence_missing_malformed_and_stale_threshold(self) -> None:
        self.write("worklog/commitments.md", COMMIT)
        for contents in (None, b"\xff", "{invalid json", '{"items": false}', '{"items": [NaN]}'):
            with self.subTest(contents=contents):
                if contents is not None:
                    self.write("data/evidence.json", contents)
                data = self.command("next", code=4)
                self.assertEqual(self.codes(data), {"evidence_unverified"})
                self.assertEqual(data["now"][0]["id"], "C-20260312-01")
        self.write("workplace.toml", CONFIG + '[next]\nstale_hours = 1\n')
        self.write(".agents/skills/sample/state-machines.md", EN
                   + "| R0 Proof | us ✅ | end | log | - | #1 |\nOpen questions: none\n")
        self.evidence(items=[{"id": "boundary", "state": "R0", "at": "2026-03-12T17:30:00"}])
        self.assertEqual(self.command("next")["items"][0]["status"], "actionable")

    def test_outbound_checks_receipts_filters_and_cards(self) -> None:
        self.write("rules.toml", RULE)
        self.write(".agents/skills/sample/people-cards.md", CARDS)
        self.evidence(receipts=[{"id": "proof", "verified": True}, {"id": "false", "verified": False},
                                {"id": "not-bool", "verified": 1}])
        self.write("draft.txt", "Done ASAP without a mention.")
        data = self.command("outbound", "check", "--to", "m", "--channel", "release", "--receipt", "false",
                            "--receipt", "not-bool", "--file", "draft.txt", code=2)
        self.assertEqual([v["check"] for v in data["violations"]], ["require_receipt", "require", "forbid", "max_chars"])
        self.assertEqual(data["violations"][0],
                         {"rule": "claims", "check": "require_receipt", "message": "Check the release evidence.",
                          "src": "N-20260312-03"})
        self.assertEqual(data["card"]["before_drafting"], "- Read the original thread.")
        self.assertIn("### Proof", data["card"]["person"])
        self.assertNotIn("Jun", data["card"]["person"])
        self.command("outbound", "check", "--to", "mArA", "--channel", "release", "--receipt", "absent",
                     "--receipt", "proof", stdin="@Mara done")
        self.command("outbound", "check", "--to", "Mara", "--channel", "other", stdin="Done ASAP")
        self.command("outbound", "check", "--to", "Jun", "--channel", "release", stdin="Done ASAP")
        self.command("outbound", "check", "--to", "Mara", "--channel", "release", stdin="Still running")
        missing = self.command("outbound", "check", "--to", "Unknown", code=1, stdin="hello")
        self.assertIsNone(missing["card"]["person"])
        self.assertEqual(self.codes(missing), {"no_card"})
        self.write("rules.toml", "invalid...toml")
        self.assertEqual(self.codes(self.command("outbound", "check", "--to", "Mara", code=4)), {"unreadable"})

    def test_outbound_chinese_cards_and_fenced_alias(self) -> None:
        self.write("workplace.toml", CONFIG + '[paths]\npeople_cards = "people.md"\n')
        self.write("people.md", "# 人員卡\n## 擬稿前\n- 先讀討論串\n## 小文\n別名：阿文、文文\n回覆討論串\n"
                   + "```\n## Fake\n別名：Phantom\n```\n")
        card = self.command("outbound", "check", "--to", "文文")["card"]
        self.assertEqual(card["before_drafting"], "- 先讀討論串")
        self.assertTrue(card["person"].startswith("## 小文"))
        self.assertEqual(self.codes(self.command("outbound", "check", "--to", "Phantom", code=1)), {"no_card"})

    def test_outbound_hook_blocks_then_allows_verified_receipt(self) -> None:
        self.write("rules.toml", RULE)
        self.evidence(receipts=[{"id": "proof", "verified": True}])
        args = ("outbound", "hook", "--text-field", "text", "--to-field", "recipient",
                "--channel-field", "channel", "--receipt-field", "proof")
        source = {"text": "@Mara done", "recipient": "Mara", "channel": "release"}
        block = self.invoke(*args, stdin=json.dumps({"tool_input": source}))
        self.assertEqual(block.returncode, 2)
        self.assertEqual(block.stdout, "")
        self.assertEqual(json.loads(block.stderr)[0]["check"], "require_receipt")
        source["proof"] = ["proof"]
        allow = self.invoke(*args, stdin=json.dumps({"tool_input": source}))
        self.assertEqual((allow.returncode, allow.stdout, allow.stderr), (0, "", ""))

    def test_wrapup_check_markers_headings_and_warning(self) -> None:
        self.command("wrapup", "begin")
        pending = self.root / ".wm/wrapup-pending"
        passed = self.root / ".wm/wrapup-pass"
        self.assertTrue(pending.exists())
        missing = self.command("wrapup", "check", code=2)
        self.assertEqual(missing["result"], "FAIL")
        self.assertEqual(self.codes(missing), {"no_worklog_today"})
        self.assertTrue(pending.exists())
        self.assertFalse(passed.exists())

        self.write("worklog/2026-03-12.md", "````md\n" + HANDOFF + "```\n"
                   + "## Start here\nAlso fenced.\n````\n")
        hidden = self.command("wrapup", "check", code=2)
        self.assertEqual(self.codes(hidden), {"no_start_section", "no_unpushed_section"})
        self.write("worklog/2026-03-12.md", "## Start here\n\n## Unpushed commits\nnone")
        self.assertEqual(self.codes(self.command("wrapup", "check", code=2)), {"no_start_section"})

        self.write("worklog/2026-03-12.md", "## 明天從哪開始\n繼續驗證\n## 未推提交\n無\n")
        notes = self.write("worklog/inbox.md", NOTE)
        warned = self.command("wrapup", "check", code=1)
        self.assertEqual(warned["result"], "WARN")
        self.assertIn("note_unfiled", self.codes(warned))
        self.assertTrue(pending.exists())
        self.assertFalse(passed.exists())
        allowed = self.invoke("stop-gate", stdin="{}")
        self.assertEqual((allowed.returncode, allowed.stdout, allowed.stderr), (0, "", ""))
        notes.unlink()
        self.assertEqual(self.command("wrapup", "check")["result"], "PASS")
        self.assertFalse(pending.exists())
        self.assertEqual(passed.read_text().strip(), NOW)

    def test_wrapup_git_unpushed_and_draft_no_writes(self) -> None:
        self.repo()
        self.write("tracked.txt", "changes")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "Ahead")
        identity = self.git("rev-parse", "HEAD")[:7]
        worklog = self.write("worklog/2026-03-12.md", HANDOFF)
        missing = self.command("wrapup", "check", code=2)
        self.assertEqual(self.codes(missing), {"unpushed_not_listed"})
        self.assertIn(identity, missing["problems"][0]["detail"])
        self.write("worklog/commitments.md", COMMIT)
        self.write("worklog/inbox.md", NOTE)
        self.evidence(items=[])
        before = {path: path.read_bytes() for path in worklog.parent.iterdir()}
        draft = self.command("wrapup", "draft")["markdown"]
        for expected in ("## Start here", "## Unpushed commits", "Open commitments", "### Next",
                         "Unfiled notes", "Branch: main", identity, "Send the checklist.", "Version is set twice."):
            self.assertIn(expected, draft)
        self.assertEqual({path: path.read_bytes() for path in worklog.parent.iterdir()}, before)
        self.assertFalse((self.root / ".wm").exists())

        self.write("worklog/2026-03-12.md", HANDOFF.replace("none", f"- {identity} Ahead"))
        (self.root / "worklog/inbox.md").unlink()
        self.assertEqual(self.command("wrapup", "check")["result"], "PASS")
        self.assertEqual(self.command("wrapup", "write", "--replace", "--start", "Check the upstream.")["result"],
                         "PASS")
        self.assertIn(f"\n- {identity} Ahead\n", worklog.read_text())

    def test_wrapup_unverified_upstream_and_git_unavailable(self) -> None:
        self.repo(upstream=False)
        self.write("worklog/2026-03-12.md", HANDOFF)
        data = self.command("wrapup", "check", code=4)
        self.assertEqual(data["result"], "UNVERIFIED")
        self.assertEqual(self.codes(data), {"unpushed_unverified"})
        self.command("wrapup", "begin")
        blocked = self.invoke("stop-gate", stdin="{}")
        self.assertEqual(blocked.returncode, 0)
        self.assertEqual(json.loads(blocked.stdout)["decision"], "block")
        self.assertIn("unpushed_unverified worklog/2026-03-12.md", blocked.stdout)
        with patch("wm.git.subprocess.run", side_effect=FileNotFoundError):
            data = self.direct("wrapup", "check")
        self.assertEqual(data[0], 4)
        self.assertIn("git is unavailable", data[1])

    def test_stop_gate_active_absent_pending_and_failure(self) -> None:
        quiet = self.invoke("stop-gate", stdin="{}")
        self.assertEqual((quiet.returncode, quiet.stdout, quiet.stderr), (0, "", ""))
        self.command("wrapup", "begin")
        quiet = self.invoke("stop-gate", stdin='{"stop_hook_active": true}')
        self.assertEqual((quiet.returncode, quiet.stdout, quiet.stderr), (0, "", ""))
        block = self.invoke("stop-gate", stdin='{"stop_hook_active": false}')
        self.assertEqual(block.returncode, 0)
        self.assertEqual(block.stderr, "")
        data = json.loads(block.stdout)
        self.assertEqual(data["decision"], "block")
        self.assertIn("no_worklog_today worklog/2026-03-12.md", data["reason"])
        self.write("worklog/2026-03-12.md", HANDOFF)
        quiet = self.invoke("stop-gate", stdin="{}")
        self.assertEqual((quiet.returncode, quiet.stdout, quiet.stderr), (0, "", ""))
        self.assertFalse((self.root / ".wm/wrapup-pending").exists())

    def test_brief_reports_worklog_git_notes_and_commitments(self) -> None:
        self.repo()
        subdir = self.root / "sub"
        subdir.mkdir()
        self.write("worklog/2026-03-11.md", "```\n## Start here\nFake\n```\n" + HANDOFF)
        self.write("worklog/2026-03-13.md", HANDOFF.replace("Read", "Future"))
        self.write("worklog/inbox.md", NOTE)
        self.write("worklog/commitments.md", COMMIT + "\n" + COMMIT.replace("12-01", "12-02")
                   .replace("due=2026-03-12", "due=none"))
        data = self.command("brief", cwd=subdir)
        self.assertEqual(data["hostname"], socket.gethostname())
        self.assertEqual(data["cwd"], str(subdir))
        self.assertEqual(data["branch"], "main")
        self.assertEqual(data["changed_files"], 4)
        self.assertEqual(data["worklog"],
                         {"date": "2026-03-11", "path": "worklog/2026-03-11.md", "start": "Read the release log."})
        self.assertEqual([record["id"] for record in data["due"]], ["C-20260312-01"])
        self.assertEqual([record["id"] for record in data["undated"]], ["C-20260312-02"])
        self.assertEqual([record["id"] for record in data["unfiled_notes"]], ["N-20260312-01"])
        self.assertEqual(data["unverified"], [])
        hooked = self.invoke("brief", "--hook", stdin="{}", cwd=subdir)
        self.assertEqual((hooked.returncode, hooked.stderr), (0, ""))
        self.assertIn("Read the release log.", self.session_context(hooked.stdout))

    def test_brief_marks_unreadable_inputs_and_respects_lookback(self) -> None:
        self.write("workplace.toml", CONFIG + "[handoff]\nlookback_days = 1\n")
        self.write("worklog/2026-03-12.md", b"\xfe")
        self.write("worklog/2026-03-10.md", HANDOFF)
        self.write("worklog/inbox.md", b"\xff")
        data = self.command("brief", code=4)
        self.assertIsNone(data["worklog"])
        self.assertEqual([issue["where"] for issue in data["unverified"]],
                         ["worklog/2026-03-12.md", "worklog/inbox.md", "worklog/commitments.md"])
        run = self.invoke("brief", "--hook")
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertIn("worklog/inbox.md", self.session_context(run.stdout))

    def direct(self, *args: str, stdin: str = "{}") -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(stdin)), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(["--root", str(self.root), "--now", NOW, *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def session_context(self, stdout: str) -> str:
        data = json.loads(stdout)
        self.assertEqual(set(data), {"hookSpecificOutput"})
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return data["hookSpecificOutput"]["additionalContext"]

    def test_all_hooks_fail_open_on_bad_stdin_and_no_root(self) -> None:
        options = [("brief", "--hook"), ("outbound", "hook", "--text-field", "text"), ("stop-gate",)]
        for args in options:
            for stdin, root, error in (
                ("{invalid", True, "hook:"), ('{"number":NaN}', True, "not valid JSON"),
                ("{}", False, "no project root"),
            ):
                with self.subTest(args=args, stdin=stdin, root=root):
                    run = self.invoke(*args, stdin=stdin, root=root)
                    self.assertEqual(run.returncode, 0)
                    self.assertIn(error, run.stderr)
                    if args[0] == "brief":
                        self.assertEqual(self.session_context(run.stdout), run.stderr.strip())
                    else:
                        self.assertEqual(run.stdout, "")

    def test_all_hooks_fail_open_on_internal_error(self) -> None:
        self.command("wrapup", "begin")
        for function, args, stdin in (
            ("brief", ("brief", "--hook"), "{}"),
            ("check_outbound", ("outbound", "hook", "--text-field", "text"), '{"tool_input":{"text":"draft"}}'),
            ("wrapup_check", ("stop-gate",), "{}"),
        ):
            with self.subTest(mode=args[0]), patch.object(cli, function, side_effect=RuntimeError("injected\nerror")):
                code, stdout, stderr = self.direct(*args, stdin=stdin)
                self.assertEqual(code, 0)
                self.assertIn("injected error", stderr)
                if function == "brief":
                    self.assertEqual(self.session_context(stdout), stderr.strip())
                else:
                    self.assertEqual(stdout, "")

    def test_outbound_hook_fails_open_for_invalid_rules_and_arguments(self) -> None:
        self.write("rules.toml", "[no")
        run = self.invoke("outbound", "hook", "--text-field", "text", stdin='{"tool_input":{"text":"done"}}')
        self.assertEqual((run.returncode, run.stdout), (0, ""))
        self.assertIn("unreadable", run.stderr)
        missing_arg = self.invoke("outbound", "hook", stdin="{}")
        self.assertEqual((missing_arg.returncode, missing_arg.stdout), (0, ""))
        self.assertIn("--text-field", missing_arg.stderr)

    def test_doctor_checks_paths_git_ignore_and_registrations(self) -> None:
        self.repo()
        data = self.command("doctor", code=1)
        self.assertEqual(self.codes(data), {"state_not_ignored", "actions_not_ignored", "hook_not_registered"})
        self.assertIs(data["state_ignored"], False)
        self.assertTrue(all(not info["registered"] for info in data["hooks"].values()))
        self.assertIn({"key": "rules", "path": "rules.toml", "exists": False}, data["paths"])
        (self.root / ".git/info/exclude").write_text("/.wm/\n", encoding="utf-8")
        settings = {}
        for event, command in {
            "Stop": 'wm --root "$CLAUDE_PROJECT_DIR" stop-gate',
            "SessionStart": 'wm --root "$CLAUDE_PROJECT_DIR" brief --hook',
            "PreToolUse": 'bin/wm --root "$CLAUDE_PROJECT_DIR" outbound hook --text-field text',
        }.items():
            settings[event] = [{"hooks": [{"type": "command", "command": command}]}]
        self.write(".claude/settings.json", json.dumps({"hooks": settings}))
        data = self.command("doctor")
        self.assertIs(data["state_ignored"], True)
        self.assertTrue(all(info["registered"] for info in data["hooks"].values()))
        self.assertFalse((self.root / ".wm").exists())

    def watch_output(self, name, probes, *, expire=False, **options):
        elapsed = 0

        def sleep(delay):
            nonlocal elapsed
            elapsed += delay

        clock = SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep)
        children = []
        for exit_code, stdout in probes:
            process = MagicMock(returncode=exit_code, pid=1234)
            process.__enter__.return_value = process
            process.communicate.return_value = (stdout, None)
            children.append(process)
        if expire:
            def timeout(**kwargs):
                sleep(kwargs["timeout"])
                raise subprocess.TimeoutExpired("probe", kwargs["timeout"])
            children[-1].communicate.side_effect = timeout

        stdout = io.StringIO()
        stderr = io.StringIO()
        args = ["--root", str(self.root), "--now", NOW, "watch", "--name", name, "--probe", "probe",
                "--every", "20"]
        for key, value in options.items():
            args.extend(("--" + key.replace("_", "-"), str(value)))
        with (patch("wm.watch.time", clock), patch("wm.watch.subprocess.Popen", side_effect=children) as spawn,
              patch("wm.watch.os.killpg", create=True) as kill, redirect_stdout(stdout), redirect_stderr(stderr)):
            code = cli.main(args)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(spawn.call_count, len(probes))
        return code, stdout.getvalue().splitlines(), children, kill

    def test_watch_event_warning_and_done(self) -> None:
        code, lines, _, _ = self.watch_output(
            "deploy", [(0, "status=running"), (0, "status=running"), (7, ""), (7, ""), (0, "status=live")],
            timeout=100, warn_after=2, done="status=live", state="last")
        self.assertEqual(code, 0)
        self.assertEqual(lines, [
            f"{NOW} deploy EVENT status=running",
            f"{NOW} deploy WARN probe failed 2 times in a row (exit 7)",
            f"{NOW} deploy DONE status=live",
        ])
        self.assertEqual((self.root / ".wm/watch/last").read_text(),
                         hashlib.sha256(b"status=live").hexdigest() + "\n")

    def test_watch_timeout_and_state_prevents_duplicate_event(self) -> None:
        probes = [(0, "ready\n")] * 3
        code, lines, _, _ = self.watch_output("probe", probes, timeout=60, state="last")
        self.assertEqual(code, 3)
        self.assertEqual(lines, [f"{NOW} probe EVENT ready", f"{NOW} probe TIMEOUT last=ready"])
        self.assertEqual((self.root / ".wm/watch/last").read_text(),
                         hashlib.sha256(b"ready").hexdigest() + "\n")
        code, lines, _, _ = self.watch_output("probe", probes, timeout=60, state="last")
        self.assertEqual(code, 3)
        self.assertEqual(lines, [f"{NOW} probe TIMEOUT last=ready"])

    def test_watch_no_success_exit_four_and_validates_arguments(self) -> None:
        code, lines, _, _ = self.watch_output("quiet", [(0, "")] * 6, timeout=120, warn_after=2)
        self.assertEqual(code, 4)
        self.assertEqual(lines, [
            f"{NOW} quiet WARN probe failed 2 times in a row (empty stdout)",
            f"{NOW} quiet TIMEOUT last=",
        ])
        self.command("watch", "--name", "bad", "--probe", ":", "--done", "[", code=64)
        self.command("watch", "--name", "bad", "--probe", ":", "--every", "nan", code=64)

    def test_watch_deadline_also_bounds_an_in_progress_probe(self) -> None:
        code, lines, children, kill = self.watch_output(
            "slow", [(0, "ready")], timeout=36, warn_after=1, expire=True)
        self.assertEqual(code, 4)
        children[0].communicate.assert_called_once_with(timeout=36)
        if os.name == "posix":
            kill.assert_called_once_with(1234, signal.SIGKILL)
        else:
            children[0].kill.assert_called_once_with()
        self.assertEqual(lines, [
            f"{NOW} slow WARN probe failed 1 times in a row (probe timed out)",
            f"{NOW} slow TIMEOUT last=",
        ])


if __name__ == "__main__":
    unittest.main()
