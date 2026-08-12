"""issue sync — 0 create / 1 sync / 2+ fail-closed / rerun duplicate 0 / outage exit 2."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers as h

CREATE_ISSUES = h.SKILL / "scripts" / "create-issues.py"
FAKE_GH = h.FIXTURES / "fake_gh"


class TestIssueSync(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        h.make_repo(self.repo)
        self.world = Path(self._tmp.name) / "world.json"
        self.world.write_text(json.dumps({"issues": [], "next": 1}), encoding="utf-8")
        os.chmod(FAKE_GH, 0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def run_sync(self, *extra, fail=False):
        env = dict(os.environ)
        env["REPO_GOVERNANCE_GH"] = str(FAKE_GH)
        env["FAKE_GH_WORLD"] = str(self.world)
        if fail:
            env["FAKE_GH_FAIL"] = "1"
        return subprocess.run(
            [sys.executable, str(CREATE_ISSUES), "--root", str(self.repo), *extra],
            capture_output=True, text=True, env=env, check=False)

    def world_issues(self):
        return json.loads(self.world.read_text(encoding="utf-8"))["issues"]

    def test_create_then_idempotent_rerun(self):
        result = self.run_sync("--confirm-external-write")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        issues = self.world_issues()
        self.assertEqual(len(issues), 2)
        markers = [body for body in (i["body"] for i in issues)]
        self.assertTrue(any("repo-governance-ticket:F1-001" in b for b in markers))
        self.assertTrue(any("repo-governance-ticket:F2-001" in b for b in markers))

        rerun = self.run_sync("--confirm-external-write")
        self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
        self.assertEqual(len(self.world_issues()), 2)  # rerun duplicate 0
        self.assertIn("create 0", rerun.stdout)

    def test_drifted_issue_synced_not_duplicated(self):
        self.run_sync("--confirm-external-write")
        world = json.loads(self.world.read_text(encoding="utf-8"))
        world["issues"][0]["title"] = "누가 손으로 바꿨다"
        self.world.write_text(json.dumps(world), encoding="utf-8")
        result = self.run_sync("--confirm-external-write")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.world_issues()), 2)
        self.assertIn("sync 1", result.stdout)

    def test_duplicate_markers_fail_closed(self):
        self.run_sync("--confirm-external-write")
        world = json.loads(self.world.read_text(encoding="utf-8"))
        world["issues"].append(dict(world["issues"][0], number=99))
        self.world.write_text(json.dumps(world), encoding="utf-8")
        result = self.run_sync("--confirm-external-write")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(self.world_issues()), 3)  # 아무것도 쓰지 않았다

    def test_api_outage_exit_2(self):
        result = self.run_sync("--confirm-external-write", fail=True)
        self.assertEqual(result.returncode, 2)

    def test_dry_run_writes_nothing(self):
        result = self.run_sync("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.world_issues(), [])

    def test_no_static_label_violation(self):
        result = self.run_sync("--confirm-external-write")
        self.assertEqual(result.returncode, 0)
        for issue in self.world_issues():
            for label in issue.get("labels", []):
                self.assertFalse(label.startswith("status:"), label)


if __name__ == "__main__":
    unittest.main()
