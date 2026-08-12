"""installer — idempotency / dry-run 무쓰기 / 충돌 fail-closed / policy 생성."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers as h


def run_installer(config_path: Path, target: Path, *extra):
    return subprocess.run(
        [sys.executable, str(h.INSTALLER), "--config", str(config_path),
         "--path", str(target), *extra],
        capture_output=True, text=True, check=False)


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.target = self.base / "target"
        self.target.mkdir()
        self.config = self.base / "repo-factory.json"
        self.config.write_text(json.dumps(h.base_config()), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        result = run_installer(self.config, self.target, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertTrue(manifest["dry_run"])
        self.assertTrue(manifest["plan"]["create"])
        self.assertEqual(list(self.target.iterdir()), [])

    def test_install_then_idempotent_rerun(self):
        first = run_installer(self.config, self.target)
        self.assertEqual(first.returncode, 0, first.stderr)
        for rel in ("governance/policy.v1.json", "governance/factory-lock.json",
                    "governance/github-profile.lock.json", "governance/actions-lock.v1.json",
                    "scripts/governance.py", "scripts/merge-broker.py",
                    "scripts/github-profile.py",
                    ".github/workflows/post-merge.yml",
                    "governance/adapters/worker-a.json",
                    "governance/external-write-plan.json"):
            self.assertTrue((self.target / rel).is_file(), rel)
        # App-dependent workflows 는 더 이상 생성되지 않는다 (로컬 controller 모델)
        for gone in (".github/workflows/merge-broker.yml", ".github/workflows/autopilot.yml"):
            self.assertFalse((self.target / gone).is_file(), gone)
        second = run_installer(self.config, self.target)
        self.assertEqual(second.returncode, 0, second.stderr)
        manifest = json.loads(second.stdout)
        self.assertEqual(manifest["plan"]["create"], [])
        self.assertEqual(manifest["plan"]["update"], [])
        self.assertEqual(manifest["plan"]["conflict"], [])

    def test_conflict_fails_closed_without_update(self):
        run_installer(self.config, self.target)
        policy_path = self.target / "governance/policy.v1.json"
        policy_path.write_text(policy_path.read_text(encoding="utf-8") + "\n// drift",
                               encoding="utf-8")
        result = run_installer(self.config, self.target)
        self.assertEqual(result.returncode, 1)
        result = run_installer(self.config, self.target, "--update")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generated_policy_is_valid_and_substituted(self):
        run_installer(self.config, self.target)
        policy = json.loads((self.target / "governance/policy.v1.json").read_text(encoding="utf-8"))
        problems = h.GOV.Problems()
        h.GOV.validate_policy(policy, problems)
        self.assertFalse(problems.items, problems.items)
        self.assertEqual(policy["repository"]["name"], "acme/demo")
        self.assertEqual(policy["agent_runtime"]["reviewers"], ["rev-a", "rev-b"])
        ci = (self.target / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        for placeholder in ("{{RUNTIME_LOWER}}", "{{RUNTIME_LATEST}}",
                            "{{FULL_CMD}}", "{{BUILD_CMD}}"):
            self.assertNotIn(placeholder, ci)  # ${{ }} 는 GitHub 표현식이라 남는다

    def test_bad_config_exits_2(self):
        bad = self.base / "bad.json"
        bad.write_text(json.dumps({"repository": {"name": "nope"}}), encoding="utf-8")
        result = run_installer(bad, self.target)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
