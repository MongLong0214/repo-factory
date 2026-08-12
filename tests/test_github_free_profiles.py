"""GitHub Free profile — resolver 결정성 · lock 불변식 · 보증 혼동 금지."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers as h

RESOLVER = h.KIT / "scripts" / "github-profile.py"
FAKE_GH = h.FIXTURES / "fake_gh"
GHP = h.load_module("github_profile", RESOLVER)


def api_world(owner_type="User", visibility="public", plan="free", full_name="acme/demo"):
    return {
        "issues": [], "next": 1,
        "api": {
            f"repos/{full_name}": {
                "full_name": full_name, "visibility": visibility,
                "owner": {"login": full_name.split("/")[0], "type": owner_type},
                "default_branch": "dev",
            },
            "user": {"login": "acme", "plan": {"name": plan}},
            f"repos/{full_name}/actions/permissions": {"enabled": True},
        },
    }


class ResolverBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.world = Path(self._tmp.name) / "world.json"
        os.chmod(FAKE_GH, 0o755)

    def tearDown(self):
        self._tmp.cleanup()

    def resolve(self, world: dict, *extra, fail=False):
        self.world.write_text(json.dumps(world), encoding="utf-8")
        env = dict(os.environ, REPO_GOVERNANCE_GH=str(FAKE_GH), FAKE_GH_WORLD=str(self.world))
        if fail:
            env["FAKE_GH_FAIL"] = "1"
        result = subprocess.run(
            [sys.executable, str(RESOLVER), "resolve", "--repo", "acme/demo",
             "--expected-plan", "free", "--json", *extra],
            capture_output=True, text=True, env=env, check=False)
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
        return result.returncode, payload


class TestProfileResolution(ResolverBase):
    def test_user_public_is_native_without_queue(self):
        code, payload = self.resolve(api_world("User", "public"))
        self.assertEqual(code, 0, payload)
        lock = payload["lock"]
        self.assertEqual(lock["profile"], "FREE_PUBLIC_USER_NATIVE")
        self.assertFalse(lock["merge_queue"])  # 개인 계정 public 에 merge queue 없음
        self.assertTrue(lock["native_branch_enforcement"])
        self.assertTrue(lock["plan_verified"])

    def test_org_public_gets_queue_profile(self):
        code, payload = self.resolve(api_world("Organization", "public"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["lock"]["profile"], "FREE_PUBLIC_ORG_NATIVE_QUEUE")
        self.assertTrue(payload["lock"]["merge_queue"])

    def test_private_is_compensating_never_native(self):
        code, payload = self.resolve(api_world("User", "private"))
        self.assertEqual(code, 0)
        lock = payload["lock"]
        self.assertEqual(lock["profile"], "FREE_PRIVATE_COMPENSATING")
        self.assertFalse(lock["native_branch_enforcement"])
        self.assertFalse(lock["native_auto_merge"])
        self.assertEqual(lock["assurance_limit"], "COMPENSATING_CONTROLS_ONLY")

    def test_visibility_mismatch_exit_1(self):
        code, payload = self.resolve(api_world("User", "private"),
                                     "--expected-visibility", "public")
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "VISIBILITY_MISMATCH")

    def test_owner_type_unsupported(self):
        code, payload = self.resolve(api_world("Bot", "public"))
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "OWNER_TYPE_UNSUPPORTED")

    def test_plan_mismatch(self):
        code, payload = self.resolve(api_world(plan="pro"))
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "PLAN_MISMATCH")

    def test_api_outage_is_external_state_not_unavailable_claim(self):
        code, payload = self.resolve(api_world(), fail=True)
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "EXTERNAL_STATE_UNAVAILABLE")

    def test_wrong_repo_echo_fails_closed(self):
        world = api_world()
        world["api"]["repos/acme/demo"]["full_name"] = "someone/else"
        code, payload = self.resolve(world)
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "EXTERNAL_STATE_UNAVAILABLE")


class TestLockInvariants(unittest.TestCase):
    def lock(self, **overrides):
        base = {
            "schema": "repo-governance.github-profile.lock.v1",
            "plan": "free", "plan_verified": True, "owner_type": "User",
            "repository": "acme/demo", "visibility": "public",
            "profile": "FREE_PUBLIC_USER_NATIVE",
            "native_branch_enforcement": True, "native_auto_merge": True,
            "merge_queue": False, "assurance_limit": None,
            "resolved_at": "2026-08-08T00:00:00+00:00",
        }
        base.update(overrides)
        return base

    def test_private_claiming_native_rejected(self):
        problems = GHP.validate_lock_shape(self.lock(
            visibility="private", profile="FREE_PRIVATE_COMPENSATING",
            native_branch_enforcement=True, assurance_limit="COMPENSATING_CONTROLS_ONLY"))
        self.assertTrue(any("native" in p for p in problems), problems)

    def test_private_without_assurance_limit_rejected(self):
        problems = GHP.validate_lock_shape(self.lock(
            visibility="private", profile="FREE_PRIVATE_COMPENSATING",
            native_branch_enforcement=False, native_auto_merge=False,
            merge_queue=False, assurance_limit=None))
        self.assertTrue(any("assurance_limit" in p for p in problems), problems)

    def test_personal_public_with_queue_rejected(self):
        problems = GHP.validate_lock_shape(self.lock(merge_queue=True))
        self.assertTrue(any("merge queue" in p for p in problems), problems)

    def test_public_private_never_same_profile(self):
        problems = GHP.validate_lock_shape(self.lock(
            visibility="private", profile="FREE_PUBLIC_USER_NATIVE",
            native_branch_enforcement=False, native_auto_merge=False,
            assurance_limit="COMPENSATING_CONTROLS_ONLY"))
        self.assertTrue(problems)


class TestRepoProfileLockValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "repo"
        h.make_repo(cls.repo)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def mutate_lock(self, repo: Path, **changes):
        lock_path = repo / "governance" / "github-profile.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock.update(changes)
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

    def copy_repo(self) -> Path:
        import shutil
        target = Path(tempfile.mkdtemp(dir=self._tmp.name)) / "repo"
        shutil.copytree(self.repo, target)
        return target

    def test_missing_lock_fails_validate(self):
        repo = self.copy_repo()
        (repo / "governance" / "github-profile.lock.json").unlink()
        problems, _ = h.validate(repo)
        self.assertIn("PROFILE_LOCK_MISSING", h.problem_codes(problems))

    def test_private_native_claim_fails_validate(self):
        repo = self.copy_repo()
        self.mutate_lock(repo, native_auto_merge=True)
        problems, _ = h.validate(repo)
        self.assertIn("PROFILE_NATIVE_CLAIM", h.problem_codes(problems))

    def test_visibility_drift_vs_policy_fails(self):
        repo = self.copy_repo()
        self.mutate_lock(repo, visibility="public", profile="FREE_PUBLIC_USER_NATIVE",
                         native_branch_enforcement=True, native_auto_merge=True,
                         assurance_limit=None)
        problems, _ = h.validate(repo)
        self.assertIn("PROFILE_VISIBILITY_DRIFT", h.problem_codes(problems))


if __name__ == "__main__":
    unittest.main()
