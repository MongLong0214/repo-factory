"""phase-gate v3 — 로컬 게이트 / assurance level / 정직한 NOT_CHECKED."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers as h

PHASE_GATE = h.SKILL / "scripts" / "phase-gate.py"


def git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


class TestPhaseGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "repo"
        h.make_repo(cls.repo)
        git(cls.repo, "init", "-b", "main")
        git(cls.repo, "add", "-A")
        git(cls.repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "genesis")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_gate(self, *extra, path=None):
        result = subprocess.run(
            [sys.executable, str(PHASE_GATE), "4", "--repo", "acme/demo",
             "--path", str(path or self.repo), "--tier", "M", "--json", *extra],
            capture_output=True, text=True, check=False)
        return result

    def test_offline_gate_passes_local_verified(self):
        result = self.run_gate("--offline")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["assurance_level"], "LOCAL_VERIFIED")
        names = {c["name"] for c in payload["checks"]}
        self.assertIn("governance validate", names)
        self.assertIn("clean worktree", names)

    def test_never_claims_canary_without_evidence(self):
        result = self.run_gate("--offline")
        payload = json.loads(result.stdout)
        self.assertNotIn(payload["assurance_level"],
                         ("GITHUB_CANARY_VERIFIED", "MULTI_REPO_DOGFOOD_VERIFIED"))

    def test_missing_kit_fails_design_only(self):
        broken = Path(self._tmp.name) / "broken"
        import shutil
        shutil.copytree(self.repo, broken)
        (broken / "governance" / "policy.v1.json").unlink()
        result = self.run_gate("--offline", path=broken)
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["passed"])
        self.assertEqual(payload["assurance_level"], "DESIGN_ONLY")

    def test_dirty_worktree_fails(self):
        dirty = Path(self._tmp.name) / "dirty"
        import shutil
        shutil.copytree(self.repo, dirty)
        (dirty / "src" / "wip.py").write_text("# uncommitted\n", encoding="utf-8")
        result = self.run_gate("--offline", path=dirty)
        payload = json.loads(result.stdout)
        clean = next(c for c in payload["checks"] if c["name"] == "clean worktree")
        self.assertFalse(clean["passed"])

    def test_phase_5_refused(self):
        result = subprocess.run(
            [sys.executable, str(PHASE_GATE), "5", "--repo", "acme/demo",
             "--path", str(self.repo)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("범위 밖", result.stderr)


if __name__ == "__main__":
    unittest.main()
