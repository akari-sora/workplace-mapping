"""Edge cases found by the second review round: none of them may slip back."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOW = "2026-03-12T18:30:00+00:00"


class RoundTwoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wm-r2-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "project"
        shutil.copytree(REPO / "examples" / "tidepool", self.root)

    def wm(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "wm"), "--root", str(self.root), "--now", NOW, *args],
            capture_output=True, text=True, cwd=self.root, check=False,
            env=dict(os.environ, WM_TEST_CLOCK="1", GIT_CEILING_DIRECTORIES=str(self.tmp)),
        )

    def test_unticked_line_with_a_proof_is_not_overwritten(self) -> None:
        ledger = self.root / "worklog" / "commitments.md"
        text = ledger.read_text("utf-8")
        line = "- [ ] [commitment id=C-20260312-01 due=2026-03-14 to=Ilse ball=us src=#4412]"
        self.assertIn(line, text)
        ledger.write_text(text.replace(line, line[:-1] + " proof=#4444]"), "utf-8")
        before = ledger.read_bytes()
        done = self.wm("commitments", "done", "C-20260312-01", "--proof", "#9999")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("already_done", done.stdout)
        self.assertEqual(ledger.read_bytes(), before)

    def test_unticked_note_with_a_filing_is_not_refiled(self) -> None:
        inbox = self.root / "worklog" / "inbox.md"
        text = inbox.read_text("utf-8")
        ticked = "- [x] [note id=N-20260310-01"
        self.assertIn(ticked, text)
        inbox.write_text(text.replace(ticked, "- [ ] [note id=N-20260310-01"), "utf-8")
        before = inbox.read_bytes()
        filed = self.wm("note", "file", "N-20260310-01", "--to", "drafting")
        self.assertEqual(filed.returncode, 2, filed.stdout + filed.stderr)
        self.assertIn("already_filed", filed.stdout)
        self.assertEqual(inbox.read_bytes(), before)

    def test_finished_item_with_a_future_timestamp_is_unverified(self) -> None:
        evidence = self.root / "data" / "evidence.json"
        data = json.loads(evidence.read_text("utf-8"))
        data["items"][0]["state"] = "done"
        data["items"][0]["at"] = "2030-01-01T00:00:00+00:00"
        evidence.write_text(json.dumps(data), "utf-8")
        shown = self.wm("--json", "next", "--all")
        self.assertEqual(shown.returncode, 4, shown.stdout + shown.stderr)
        item = next(i for i in json.loads(shown.stdout)["items"] if i["id"] == data["items"][0]["id"])
        self.assertEqual(item["status"], "unverified")

    def test_finished_item_with_a_past_timestamp_is_done(self) -> None:
        evidence = self.root / "data" / "evidence.json"
        data = json.loads(evidence.read_text("utf-8"))
        data["items"][0]["state"] = "done"
        evidence.write_text(json.dumps(data), "utf-8")
        shown = self.wm("--json", "next", "--all")
        item = next(i for i in json.loads(shown.stdout)["items"] if i["id"] == data["items"][0]["id"])
        self.assertEqual(item["status"], "done")

    def test_watch_state_refuses_a_planted_symlink(self) -> None:
        ledger = self.root / "worklog" / "commitments.md"
        before = ledger.read_bytes()
        watch_dir = self.root / ".wm" / "watch"
        watch_dir.mkdir(parents=True)
        (watch_dir / "leak").symlink_to(ledger)
        ran = self.wm("watch", "--name", "w", "--probe", "echo status=live", "--done", "live", "--state", "leak")
        self.assertEqual(ran.returncode, 64, ran.stdout + ran.stderr)
        self.assertEqual(ledger.read_bytes(), before)

    def test_watch_state_refuses_a_linked_watch_directory(self) -> None:
        ledger_dir = self.root / "worklog"
        before = sorted(p.name for p in ledger_dir.iterdir())
        (self.root / ".wm").mkdir()
        (self.root / ".wm" / "watch").symlink_to(ledger_dir)
        ran = self.wm("watch", "--name", "w", "--probe", "echo status=live", "--done", "live", "--state", "x")
        self.assertEqual(ran.returncode, 64, ran.stdout + ran.stderr)
        self.assertEqual(sorted(p.name for p in ledger_dir.iterdir()), before)

    def test_watch_state_still_works_normally(self) -> None:
        ran = self.wm("watch", "--name", "w", "--probe", "echo status=live", "--done", "live", "--state", "deploy")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        stored = (self.root / ".wm" / "watch" / "deploy").read_text("utf-8").strip()
        self.assertEqual(len(stored), 64)
        self.assertNotIn("status", stored)


    def leaky_receipt(self, secret_line: str, expect: str) -> str:
        config = self.root / "workplace.toml"
        config.write_text(config.read_text("utf-8") + (
            '\n[[command]]\nid = "leaky"\nwhat = "synthetic output"\n'
            'run = "cat data/leaky.txt"\nwrites = false\n'), "utf-8")
        (self.root / "data" / "leaky.txt").write_text(secret_line + "\n", "utf-8")
        added = self.wm("receipt", "add", "evidence", "--by-cmd", "leaky", "--expect", expect)
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        return (self.root / "data" / "evidence.json").read_text("utf-8")

    def test_receipt_keeps_no_part_of_a_secret_that_crosses_the_cut(self) -> None:
        secret = "Zq7" * 20                      # 60 characters, starts before character 200 and ends after it
        line = "x" * 170 + ' password="' + secret + '" tail'
        stored = self.leaky_receipt(line, "x+ password=.*tail")
        for size in (8, 12, 20, 39):
            self.assertNotIn(secret[:size], stored)

    def test_receipt_keeps_no_fragment_when_the_match_is_inside_a_secret(self) -> None:
        token = "ghp_" + "SYNTHETICTOKENVALUE" * 2
        stored = self.leaky_receipt("token " + token, "SYNTHETICTOKEN")
        self.assertNotIn("SYNTHETICTOKEN", stored)

    def test_receipt_still_keeps_an_ordinary_match(self) -> None:
        stored = self.leaky_receipt("build 4812 passed", "build [0-9]+ passed")
        self.assertIn("build 4812 passed", stored)


if __name__ == "__main__":
    unittest.main()
