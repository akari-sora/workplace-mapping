"""Writers must never touch a file outside the project root, however the destination is spelled."""
from __future__ import annotations

import shutil
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOW = "2026-03-12T18:30:00+00:00"


class ContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wm-contain-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "project"
        shutil.copytree(REPO / "examples" / "tidepool", self.root)
        self.outside = self.tmp / "outside.md"
        self.outside.write_bytes(b"# outside\n\n## Rules\n\n- keep me\n")
        (self.root / "linked.md").symlink_to(self.outside)

    def wm(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "bin" / "wm"), "--root", str(self.root), "--now", NOW, *args],
            capture_output=True, text=True, cwd=self.root, check=False,
            env=dict(os.environ, WM_TEST_CLOCK="1"),
        )

    def test_existing_outside_file_is_never_written(self) -> None:
        before = self.outside.read_bytes()
        destinations = [
            "../outside.md",                 # traversal to a file that exists
            "../outside.md#Rules",           # traversal with a heading that exists there
            str(self.outside),               # absolute path
            f"{self.outside}#Rules",
            "linked.md",                     # symlink inside the root that points outside
            "linked.md#Rules",
            "worklog/../../outside.md",
        ]
        for dest in destinations:
            with self.subTest(dest=dest):
                added = self.wm("note", "add", "escape attempt", "--to", dest)
                self.assertEqual(added.returncode, 2, added.stdout + added.stderr)
                self.assertEqual(self.outside.read_bytes(), before)
        # the notes were still recorded, open, so nothing the agent learned is lost
        listed = self.wm("--json", "note", "list", "--open")
        self.assertEqual(listed.stdout.count("escape attempt"), len(destinations))

    def test_rule_writers_stay_in_the_configured_rules_file(self) -> None:
        before = self.outside.read_bytes()
        added = self.wm("rule", "add", "--id", "inside-only", "--message", "Stay inside.",
                        "--forbid", "outside", "--src", "user, in session")
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertIn('id = "inside-only"', (self.root / "rules.toml").read_text("utf-8"))
        self.assertEqual(self.outside.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
