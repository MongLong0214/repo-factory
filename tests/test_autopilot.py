"""Autopilot controller — reconcile/run-once/rollback/재시도 분류/operation id."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers as h


class AutopilotBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "repo"
        h.make_repo(cls.repo)
        h.write_ticket(cls.repo, h.ticket_meta(
            "G0-001", title="Ticket govchange", kind="governance-change", risk="critical",
            adr_refs=[], prd_ref=None,
            owned_paths=["governance/policy.v1.json"], oracle_paths=[], acceptance=[]))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_cli(self, *args, root=None):
        result = subprocess.run(
            [sys.executable, str(h.KIT / "scripts" / "autopilot.py"), *args,
             "--root", str(root or self.repo)],
            capture_output=True, text=True, check=False)
        return result


class TestOperationId(AutopilotBase):
    def test_deterministic(self):
        first = h.AUTOPILOT.operation_id("acme/demo", "F1-001", {}, "sha256:" + "1" * 64)
        second = h.AUTOPILOT.operation_id("acme/demo", "F1-001", {}, "sha256:" + "1" * 64)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_changes_with_inputs(self):
        base = h.AUTOPILOT.operation_id("acme/demo", "F1-001", {}, "sha256:" + "1" * 64)
        self.assertNotEqual(base, h.AUTOPILOT.operation_id("acme/demo", "F2-001", {}, "sha256:" + "1" * 64))
        self.assertNotEqual(base, h.AUTOPILOT.operation_id("acme/demo", "F1-001", {"D": "x"}, "sha256:" + "1" * 64))
        self.assertNotEqual(base, h.AUTOPILOT.operation_id("acme/demo", "F1-001", {}, "sha256:" + "2" * 64))


class TestFailureClassification(AutopilotBase):
    def test_assertion_failure_is_not_transient(self):
        self.assertEqual(h.AUTOPILOT.classify_failure(
            "AssertionError: expected 5 got 3"), "REPAIRABLE")

    def test_network_timeout_is_transient(self):
        self.assertEqual(h.AUTOPILOT.classify_failure(
            "connect ETIMEDOUT 140.82.112.3:443"), "TRANSIENT")
        self.assertEqual(h.AUTOPILOT.classify_failure("HTTP 429 rate limit"), "TRANSIENT")

    def test_ownership_violation_is_contract(self):
        self.assertEqual(h.AUTOPILOT.classify_failure(
            "diff touches unowned path src/other.py"), "CONTRACT")

    def test_secret_leak_is_security(self):
        self.assertEqual(h.AUTOPILOT.classify_failure(
            "detected credential leak in artifact"), "SECURITY")

    def test_predelegation_gap_is_policy(self):
        self.assertEqual(h.AUTOPILOT.classify_failure(
            "high ticket not predelegated"), "POLICY")


class TestRunOnce(AutopilotBase):
    def test_offline_plan_holds_critical_and_respects_wip(self):
        result = self.run_cli("run-once", "--offline")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        held = {item["ticket"]: item["reason_code"] for item in plan["held"]}
        self.assertEqual(held.get("G0-001"), "CRITICAL_HALT")
        self.assertLessEqual(len(plan["startable"]), plan["wip"]["cap"])
        self.assertIn("F1-001", plan["startable"])
        self.assertNotIn("G0-001", plan["startable"])
        self.assertEqual(plan["dispatched"], [])

    def test_dependent_not_ready_before_verified(self):
        repo = Path(self._tmp.name) / "repo-dep"
        h.make_repo(repo)
        h.write_ticket(repo, h.ticket_meta("F2-001", title="Ticket highwork",
                                           risk="high", predelegated=True,
                                           dependencies=["F1-001"]))
        result = self.run_cli("run-once", "--offline", root=repo)
        plan = json.loads(result.stdout)
        self.assertNotIn("F2-001", plan["ready"])
        result = self.run_cli("run-once", "--offline", "--verified", "F1-001", root=repo)
        plan = json.loads(result.stdout)
        self.assertIn("F2-001", plan["ready"])
        self.assertNotIn("F1-001", plan["ready"])  # verified 는 다시 시작하지 않는다


class TestRollback(AutopilotBase):
    def test_rollback_ticket_created_with_invalidates(self):
        repo = Path(self._tmp.name) / "repo-rollback"
        h.make_repo(repo)
        result = self.run_cli("rollback", "--ticket", "F1-001",
                              "--reason", "post-merge CI red", root=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["invalidates"], ["F1-001"])
        problems, context = h.validate(repo)
        self.assertFalse(problems.items, problems.items)
        rollback_meta = context["tickets"][payload["rollback_ticket"]]["meta"]
        self.assertEqual(rollback_meta["kind"], "rollback")
        self.assertEqual(rollback_meta["invalidates"], ["F1-001"])
        # 원 티켓은 offline status 에서 invalidated 로 표시된다
        status = h.GOV.offline_status(repo)
        self.assertEqual(status["tickets"]["F1-001"]["declared_state"], "invalidated")


class TestRecover(AutopilotBase):
    def test_expired_lease_inspect_not_new_pr(self):
        repo = Path(self._tmp.name) / "repo-recover"
        h.make_repo(repo)
        lease_dir = repo / ".autopilot" / "leases"
        lease_dir.mkdir(parents=True)
        (lease_dir / "F1-001.json").write_text(json.dumps({
            "ticket_id": "F1-001", "operation_id": "sha256:" + "5" * 64,
            "branch": "feat/F1-001-parse", "attempt": 1, "repair_rounds": 0,
            "claimed_at": "2026-08-08T00:00:00+00:00",
            "heartbeat_at": "2026-08-08T00:00:00+00:00",
            "worker_run_id": "w1", "ttl_seconds": 60,
        }), encoding="utf-8")
        result = self.run_cli("recover", "--now", "2026-08-08T01:00:00Z", root=repo)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["leases"][0]["action"], "inspect_and_resume")
        self.assertTrue(payload["leases"][0]["expired"])

    def test_worker_output_requires_machine_evidence(self):
        errors = h.AUTOPILOT.validate_worker_output({"status": "completed", "note": "다 했어요"})
        self.assertTrue(errors)
        complete = {k: "x" for k in h.AUTOPILOT.WORKER_OUTPUT_REQUIRED}
        self.assertEqual(h.AUTOPILOT.validate_worker_output(complete), [])


if __name__ == "__main__":
    unittest.main()
