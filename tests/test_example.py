"""Exercise the Tidepool walkthrough against a disposable project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_wm import REPO, SHIM


class TidepoolTest(unittest.TestCase):
    def test_tidepool_walkthrough(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-example-") as directory:
            root = Path(directory) / "tidepool"
            shutil.copytree(REPO / "examples/tidepool", root)
            # Keep the copy in the workspace and stop Git discovery before its parent repository.
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=directory, PYTHONDONTWRITEBYTECODE="1")
            git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=10)
            self.assertNotEqual(git.returncode, 0, git.stdout)

            def wm(*args: str, code: int = 0, payload: str = "") -> dict:
                process = subprocess.run([str(SHIM), "--root", str(root), "--now", "2026-03-12T18:30:00+00:00", "--json", *args],
                                         cwd=root, env=env, input=payload, capture_output=True, text=True, timeout=10)
                self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                self.assertEqual(process.stderr, "")
                return json.loads(process.stdout)

            with self.subTest(step="lint"):
                wm("lint")
            with self.subTest(step="next"):
                items = {item["id"]: item["status"] for item in wm("next")["items"]}
                self.assertEqual(items, {"REQ-15": "actionable", "REQ-9": "needs_user", "REQ-12": "waiting"})
            with self.subTest(step="wrapup"):
                self.assertEqual(wm("wrapup", "check")["result"], "PASS")
            with self.subTest(step="catalog"):
                catalog = wm("catalog")
                self.assertEqual([item["id"] for item in catalog["commands"]], ["flags.status", "flags.set"])
                self.assertEqual([item["id"] for item in catalog["channels"]], ["release", "app-team", "on-call"])
                self.assertEqual([item["id"] for item in catalog["systems"]], ["tracker", "flags"])
            with self.subTest(step="initial flags"):
                self.assertIn("csv_export", wm("run", "flags.status"))
            outbound = ("outbound", "check", "--to", "Ilse", "--channel", "release")
            with self.subTest(step="blocked release"):
                blocked = wm(*outbound, "--file", "drafts/blocked.txt", code=2)
                self.assertEqual({v["check"] for v in blocked["violations"]}, {"require_receipt", "require"})
            with self.subTest(step="approved release draft"):
                self.assertEqual(wm(*outbound, "--receipt", "RCPT-7", "--file", "drafts/ok.txt")["violations"], [])
            with self.subTest(step="access"):
                systems = wm("access", "check", code=1)["systems"]
                self.assertEqual([entry["status"] for entry in systems], ["no_probe", "no_probe"])
            with self.subTest(step="standing chat approval"):
                chat = wm("action", "send", "--kind", "chat", "--target", "on-call", "--to", "Okafor",
                          payload="ack, on it")["action"]
                self.assertEqual((chat["state"], chat["approved_by"]), ("verified", "standing:ack-on-call"))
                self.assertEqual(chat["proof"], "readback:auto")
                outbox = [json.loads(line) for line in (root / ".wm/outbox.jsonl").read_text().splitlines()]
                self.assertEqual(outbox, [{"id": chat["result"].strip(), "channel": "on-call", "to": "Okafor",
                                          "action": chat["id"], "text": "ack, on it"}])
            with self.subTest(step="manual flag approval and run"):
                draft = wm("action", "send", "--kind", "command", "--target", "flags.set",
                           payload="moon_strip off all", code=1)
                self.assertEqual(draft["status"], "needs_user")
                self.assertEqual(draft["action"]["payload"], "moon_strip off all")
                self.assertNotIn("moon_strip", wm("run", "flags.status"))
                identity = draft["action"]["id"]
                wm("action", "approve", identity, "--by", "yes, create it")
                self.assertEqual(wm("action", "run", identity)["action"]["state"], "sent")
                self.assertEqual(wm("run", "flags.status")["moon_strip"],
                                 {"beta": "off", "production": "off", "staging": "off"})

    def test_tidepool_text_goldens_and_budgets(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO, prefix=".wm-example-") as directory:
            root = Path(directory) / "tidepool"
            shutil.copytree(REPO / "examples/tidepool", root)
            env = dict(os.environ, WM_HOSTNAME="tidepool-test", GIT_CEILING_DIRECTORIES=directory,
                       PYTHONDONTWRITEBYTECODE="1")

            def assert_output(args, expected, *, code=0, payload="", budget=None):
                process = subprocess.run(
                    [str(SHIM), "--root", str(root), "--now", "2026-03-12T18:30:00+00:00", *args],
                    cwd=root, env=env, input=payload, capture_output=True, text=True, timeout=10)
                self.assertEqual((process.returncode, process.stderr), (code, ""))
                self.assertEqual(process.stdout, expected)
                if budget is not None:
                    self.assertLessEqual(len(process.stdout), budget)
                if code:
                    self.assertTrue(process.stdout.splitlines()[-1].startswith("next: "))

            drafting = """drafting:
- Answer the question that was asked, first.
- Reply inside the thread, not at the channel root (Mara, 10 March 2026, #4379; N-20260310-01).
- Match the length and tone of earlier messages to this person.
- Show the draft to the user before sending.
"""
            ilse = """card Ilse:
Aliases: ilse, product
- Call them: Ilse
- Role: product owner; her agreement in the request thread is what starts a rollout
- Reads messages in: release, request threads
- Language: English
- Wants messages shaped like: what changed for the user, in one sentence, then where to see it
- Usually replies: the same afternoon
- Do not: describe the implementation
- What we said to them before: "Tide alerts are on for internal accounts. You can try it with yours from 11:00." (#4406)
- Corrections learned: mention the requester in release notices (N-20260312-01, now a rule)
"""
            okafor = """card Okafor:
Aliases: okafor, ops
- Call them: Okafor
- Role: operations; owns the on-call rota and rollout windows
- Reads messages in: on-call
- Language: English
- Wants messages shaped like: flag name, percentage, requested window
- Usually replies: slowly on Fridays (verbal, 9 March 2026)
- Do not: ask product questions in on-call
- What we said to them before: "tide_alerts, 5%, any window before Thursday 16:00?" (#4410)
- Corrections learned: none yet
Open questions: Jun has no card yet.
"""
            assert_output(["brief"], """ok tidepool-test
Start here 2026-03-12
REQ-15: create the flag `moon_strip`, default off, in all three environments, then read it back (state F1).
- REQ-9 is at F2 and needs your yes before the flag goes on for internal accounts.
- REQ-12 is at F3, waiting on Okafor to name a rollout window (C-20260312-02).
- Due 14 March: the offline-behaviour write-up for Ilse (C-20260312-01).
C-20260312-01 2026-03-14 Write up how tide alerts behave when the phone is offline.
C-20260312-02 none Okafor to say which window suits the 5% rollout.
""", budget=800)
            assert_output(["next"], """ok
do REQ-15 Moon phase strip on the day view | F1 Flag exists, default off | verify: Settings page read back: flag exists and is off in all three environments | pitfall: Created in staging only, 11 March 2026 (N-20260311-02)
ask REQ-9 CSV export of saved locations | F2 On for internal accounts | verify: Viewed once with an internal and once with a normal account; behaviour differs
wait REQ-12 Tide alerts: push a notice one hour before low tide | F3 5% rollout | on: Okafor picks the window and makes the change
""", budget=750)
            assert_output(["lint"], "ok\n")
            assert_output(["catalog"], """ok
system tracker direct | requests and tickets
system flags vpn:office | feature flag settings and the per-flag dashboard
channel release custom | what went live and what is about to
channel app-team custom | day-to-day questions; reply inside threads
channel on-call custom | rollout windows and incidents; no product questions
command flags.status read | every flag and its state in the three environments
command flags.set write | create or switch a flag; payload: <flag> <on|off|internal|N%> <environment|all>
""", budget=700)
            assert_output(["sm"], """ok
F Rolling out a feature behind a flag F0-F5
S A question from support S0-S2
""")
            assert_output(["sm", "F"], """ok
F0 Proposal | Ilse agrees in the request thread / us 📖 | -> F1
F1 Flag exists, default off | us ✅ | -> F2
F2 On for internal accounts | us 🙋 | -> F3
F3 5% rollout | Okafor picks the window and makes the change | -> F4
F4 100% rollout | Okafor / us 📖 | -> F5
F5 Flag and old branch removed | us 🙋 | -> done
""")
            assert_output(["sm", "F1"], """ok
F1 Flag exists, default off
who: us ✅   next: F2
verify: Settings page read back: flag exists and is off in all three environments
pitfalls: Created in staging only, 11 March 2026 (N-20260311-02)
source: tested 11 March 2026
""")
            assert_output(["card", "product"], "ok\n" + drafting + ilse)
            outbound = ["outbound", "check", "--to", "Ilse", "--channel", "release"]
            assert_output([*outbound, "--receipt", "RCPT-7", "--file", "drafts/ok.txt"], "ok\n")
            assert_output([*outbound, "--file", "drafts/blocked.txt"], """FAIL
fail outbound_violation rules.toml 2 outbound checks failed
violation claim-needs-receipt require_receipt A completion claim needs a verified receipt that covers every affected place. (N-20260312-02)
violation release-mentions-requester require A release notice must @ the person who asked for the change. (N-20260312-01)
""" + drafting + ilse +
                'next: fix the draft; for a completion claim add --receipt <id> or --verified "<what you checked and where>"\n',
                code=2)
            assert_output(["wrapup", "write", "--start", "Check the rollout window.", "--note", "Ask Okafor.",
                           "--replace"], "ok PASS worklog/2026-03-12.md\n")
            send = ["action", "send", "--kind", "chat", "--target", "on-call", "--to", "Okafor"]
            assert_output(send, "ok sent A-20260312-01 #5000 verified\n", payload="ack, on it")
            assert_output(send, "warn needs_user A-20260312-02\n" + drafting + okafor +
                'payload: Please confirm the window.\n'
                'next: show the payload to the user; after their yes: wm action approve A-20260312-02 --by "<their words>" --run\n',
                code=1, payload="Please confirm the window.")
