"""Actions 하드닝 · security profile · broker provenance/OOB — negative 중심."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers as h

PHASE_GATE_MOD = None


def load_phase_gate():
    global PHASE_GATE_MOD
    if PHASE_GATE_MOD is None:
        import sys
        spec = importlib.util.spec_from_file_location(
            "rf_phase_gate", h.SKILL / "scripts" / "phase-gate.py")
        PHASE_GATE_MOD = importlib.util.module_from_spec(spec)
        sys.modules["rf_phase_gate"] = PHASE_GATE_MOD  # dataclass 처리에 필요
        spec.loader.exec_module(PHASE_GATE_MOD)
    return PHASE_GATE_MOD


class HardeningBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pristine = Path(cls._tmp.name) / "repo"
        h.make_repo(cls.pristine)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def copy_repo(self) -> Path:
        target = Path(tempfile.mkdtemp(dir=self._tmp.name)) / "repo"
        shutil.copytree(self.pristine, target)
        return target


class TestActionsHardening(HardeningBase):
    def test_action_not_in_lock_fails(self):
        repo = self.copy_repo()
        (repo / ".github/workflows/rogue.yml").write_text(
            "name: rogue\non:\n  push:\npermissions:\n  contents: read\n"
            "jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: evil/backdoor@" + "a" * 40 + "\n",
            encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("ACTION_NOT_IN_LOCK", h.problem_codes(problems))

    def test_lock_drift_fails(self):
        repo = self.copy_repo()
        lock_path = repo / "governance/actions-lock.v1.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["actions"][0]["commit"] = "f" * 40
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("ACTION_LOCK_DRIFT", h.problem_codes(problems))

    def test_lock_missing_fails(self):
        repo = self.copy_repo()
        (repo / "governance/actions-lock.v1.json").unlink()
        problems, _ = h.validate(repo)
        self.assertIn("ACTIONS_LOCK_MISSING", h.problem_codes(problems))

    def test_pull_request_target_trigger_banned(self):
        repo = self.copy_repo()
        (repo / ".github/workflows/pwn.yml").write_text(
            "name: pwn\non:\n  pull_request_target:\npermissions:\n  contents: read\n"
            "jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
            encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("WORKFLOW_PR_TARGET", h.problem_codes(problems))

    def test_pr_workflow_with_write_permission_banned(self):
        repo = self.copy_repo()
        (repo / ".github/workflows/leaky.yml").write_text(
            "name: leaky\non:\n  pull_request:\npermissions:\n  contents: write\n"
            "jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
            encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("WORKFLOW_PR_WRITE", h.problem_codes(problems))

    def test_workflow_without_permissions_banned(self):
        repo = self.copy_repo()
        (repo / ".github/workflows/nude.yml").write_text(
            "name: nude\non:\n  push:\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: echo hi\n",
            encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("WORKFLOW_PERMISSIONS_MISSING", h.problem_codes(problems))

    def test_shipped_kit_passes_its_own_hardening(self):
        problems, _ = h.validate(self.pristine)
        self.assertFalse(problems.items, problems.items)


class TestSecurityScan(HardeningBase):
    def scan(self, repo):
        return h.GOV.security_scan(repo)

    def item(self, items, name):
        return next(i for i in items if i["name"] == name)

    def test_pristine_repo_scan_ok(self):
        items, ok = self.scan(self.pristine)
        self.assertTrue(ok, items)
        # custom lane 이 "true" 로 구성돼 있으므로 PASS — NOT_APPLICABLE 을 PASS 로 가장하지 않는다
        self.assertEqual(self.item(items, "custom_sast")["status"], "PASS")

    def test_secret_in_tree_fails(self):
        repo = self.copy_repo()
        (repo / "src" / "config.py").write_text('TOKEN = "ghp_' + "a" * 36 + '"\n',
                                                encoding="utf-8")
        items, ok = self.scan(repo)
        self.assertFalse(ok)
        self.assertEqual(self.item(items, "secret_like_diff_scan")["status"], "FAIL")

    def test_forbidden_env_file_fails(self):
        repo = self.copy_repo()
        (repo / ".env").write_text("DB_PASSWORD=hunter2\n", encoding="utf-8")
        items, ok = self.scan(repo)
        self.assertFalse(ok)
        self.assertEqual(self.item(items, "forbidden_file_scan")["status"], "FAIL")

    def test_manifest_without_lockfile_fails(self):
        repo = self.copy_repo()
        (repo / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}\n',
                                           encoding="utf-8")
        items, ok = self.scan(repo)
        self.assertFalse(ok)
        self.assertEqual(self.item(items, "lockfile_consistency")["status"], "FAIL")

    def test_unavailable_scanner_is_blocking_not_pass(self):
        repo = self.copy_repo()
        policy_path = repo / "governance/policy.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["security_commands"]["sast"] = "definitely-not-a-binary-xyz --scan"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        items, ok = self.scan(repo)
        self.assertFalse(ok)
        self.assertEqual(self.item(items, "custom_sast")["status"], "UNAVAILABLE_BLOCKING")

    def test_private_null_lane_is_not_applicable_never_pass(self):
        repo = self.copy_repo()
        policy_path = repo / "governance/policy.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["security_commands"]["sast"] = None
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        items, ok = self.scan(repo)
        status = self.item(items, "custom_sast")["status"]
        self.assertEqual(status, "NOT_APPLICABLE")
        self.assertNotEqual(status, "PASS")
        self.assertTrue(ok)  # NOT_APPLICABLE 은 차단은 아니다 — gating 은 broker 가 한다


class TestBrokerProvenance(HardeningBase):
    def test_private_high_without_sast_refused(self):
        base = Path(tempfile.mkdtemp(dir=self._tmp.name)) / "repo"
        config = h.base_config()
        config["security_commands"] = {"sast": None, "dependency_audit": None,
                                       "secret_scan": None}
        h.make_repo(base, config)
        intent = h.make_intent(base, ticket="F2-001", pr=9)
        result = h.BROKER.evaluate(base, intent, h.make_facts(intent), h.parse_now(h.LATER))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "SECURITY_LANE_MISSING"))

    def test_broker_marker_binds_operation(self):
        intent = h.make_intent(self.copy_repo())
        marker = h.BROKER.broker_marker(intent)
        for key in (intent["operation_id"], intent["ticket_id"],
                    intent["policy_digest"], intent["expected_head_sha"]):
            self.assertIn(key, marker)

    def test_private_audit_marker_required(self):
        repo = self.pristine  # private profile
        commits = [
            {"sha": "1" * 40, "message": "genesis: contract", "parents": 0},
            {"sha": "2" * 40, "message": "Merge F1-001\n\nRepo-Factory-Operation: sha256:aa\nTicket: F1-001",
             "parents": 2},
            {"sha": "3" * 40, "message": "sneaky manual merge", "parents": 2},
            {"sha": "4" * 40, "message": "direct push", "parents": 1},
        ]
        findings, clean = h.BROKER.audit_commits(repo, commits)
        verdicts = {f["sha"][:1]: f["verdict"] for f in findings}
        self.assertEqual(verdicts["1"], "GENESIS")
        self.assertEqual(verdicts["2"], "OK")
        self.assertEqual(verdicts["3"], "OUT_OF_BAND_WRITE")
        self.assertEqual(verdicts["4"], "OUT_OF_BAND_WRITE")
        self.assertFalse(clean)

    def test_private_shallow_clone_oob_not_faked_genesis(self):
        # canary 실측 회귀: shallow checkout 이 OOB direct push 를 parents=0 로 보여도
        # private 은 marker 부재 → OUT_OF_BAND_WRITE (parent count 로 genesis 오탐 금지)
        repo = self.pristine  # private profile
        commits = [{"sha": "7" * 40, "message": "manual hotpatch without ticket",
                    "parents": 0, "associated_prs": []}]
        findings, clean = h.BROKER.audit_commits(repo, commits)
        self.assertEqual(findings[0]["verdict"], "OUT_OF_BAND_WRITE")
        self.assertFalse(clean)

    def test_public_audit_requires_pr_provenance(self):
        base = Path(tempfile.mkdtemp(dir=self._tmp.name)) / "repo"
        config = h.base_config()
        config["repository"] = {"name": "acme/pub", "owner": "acme", "visibility": "public"}
        h.make_repo(base, config)
        commits = [
            {"sha": "5" * 40, "message": "Merge pull request #3", "parents": 2,
             "associated_prs": [3]},
            {"sha": "6" * 40, "message": "raw push", "parents": 1, "associated_prs": []},
        ]
        findings, clean = h.BROKER.audit_commits(base, commits)
        self.assertEqual(findings[0]["verdict"], "OK")
        self.assertEqual(findings[1]["verdict"], "OUT_OF_BAND_WRITE")
        self.assertFalse(clean)


class TestOnlineFactsProtection(unittest.TestCase):
    """load_facts_online 이 production 에서 protection 을 어떻게 읽는지 — canary 가
    가렸던 경로(runner 가 facts 를 주입). ruleset(=공장이 만드는 것)을 읽어야 하고
    classic protection API 에 의존하면 안 된다."""

    def facts_for(self, repo, rule_types):
        pr = {"number": 5, "state": "open", "merged": False,
              "head": {"sha": "a" * 40}, "base": {"ref": "dev", "sha": "b" * 40},
              "body": "Ticket: F1-001", "draft": False}

        def fake_gh_json(args):
            path = args[1]
            if path.startswith(f"repos/{repo}/pulls/5"):
                return pr
            if path.startswith(f"repos/{repo}/pulls?"):
                return [pr]
            if path.startswith(f"repos/{repo}/branches/dev"):
                return {"commit": {"sha": "b" * 40}}
            if path == f"repos/{repo}":
                return {"allow_auto_merge": True}
            if path.startswith(f"repos/{repo}/rules/branches/dev"):
                return [{"type": t} for t in rule_types]
            if path.startswith(f"repos/{repo}/branches/dev/protection"):
                raise h.BROKER.gov.FactsUnavailable("404 (classic protection 없음)")
            return None

        intent = {"repository": repo, "pr_number": 5, "ticket_id": "F1-001"}
        with mock.patch.object(h.BROKER.gov, "gh_json", side_effect=fake_gh_json), \
             mock.patch.object(h.BROKER.gov, "check_runs_for", return_value=[]):
            return h.BROKER.load_facts_online({"repository": {"name": repo}}, intent)

    def test_ruleset_only_public_reads_protection_true(self):
        # classic protection 이 404 여도 ruleset 이 있으면 protection_ok=True 여야 한다
        facts = self.facts_for("acme/pub",
                               ["pull_request", "required_status_checks", "deletion"])
        self.assertTrue(facts["branch_protection_ok"])

    def test_no_rules_yields_false_not_crash(self):
        facts = self.facts_for("acme/priv", [])
        self.assertFalse(facts["branch_protection_ok"])


class TestAssuranceLadder(HardeningBase):
    def write_evidence(self, repo: Path, payload: dict):
        evidence_dir = repo / "governance" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "canary.json").write_text(json.dumps(payload), encoding="utf-8")

    def evidence(self, profile, steps_ok=True):
        return {
            "schema": "repo-factory.canary-evidence.v2",
            "profile": profile,
            "merge_gate": "local_controller",
            "steps": {"a": True, "b": steps_ok},
        }

    def test_private_evidence_grants_compensating_level_with_limit_note(self):
        gate = load_phase_gate()
        repo = self.copy_repo()
        self.write_evidence(repo, self.evidence("FREE_PRIVATE_COMPENSATING"))
        level, notes = gate.assurance_level(repo, True, True, True)
        self.assertEqual(level, "FREE_PRIVATE_COMPENSATING_VERIFIED")
        self.assertTrue(any("COMPENSATING_CONTROLS_ONLY" in n for n in notes))

    def _public_repo(self):
        repo = self.copy_repo()
        lock_path = repo / "governance/github-profile.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock.update({"visibility": "public", "profile": "FREE_PUBLIC_USER_NATIVE",
                     "native_branch_enforcement": True, "native_auto_merge": True,
                     "assurance_limit": None})
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        return repo

    def test_public_native_verified_by_local_controller(self):
        # App 없이 로컬 controller 로 FREE_PUBLIC_NATIVE_VERIFIED 달성
        gate = load_phase_gate()
        repo = self._public_repo()
        self.write_evidence(repo, self.evidence("FREE_PUBLIC_USER_NATIVE"))
        level, notes = gate.assurance_level(repo, True, True, True)
        self.assertEqual(level, "FREE_PUBLIC_NATIVE_VERIFIED")
        self.assertTrue(any("위조 불가" in n for n in notes))

    def test_profile_mismatch_blocks_assurance_transfer(self):
        gate = load_phase_gate()
        repo = self.copy_repo()  # private lock
        self.write_evidence(repo, self.evidence("FREE_PUBLIC_USER_NATIVE"))
        level, notes = gate.assurance_level(repo, True, True, True)
        self.assertEqual(level, "LOCAL_VERIFIED")
        self.assertTrue(any("보증 이전 금지" in n for n in notes))

    def test_legacy_v1_evidence_not_promoted(self):
        gate = load_phase_gate()
        repo = self.copy_repo()
        self.write_evidence(repo, {"schema": "repo-factory.canary-evidence.v1",
                                   "steps": {"x": True}})
        level, notes = gate.assurance_level(repo, True, True, True)
        self.assertEqual(level, "LOCAL_VERIFIED")
        self.assertTrue(any("legacy" in n for n in notes))

    def test_private_never_reaches_9_9(self):
        gate = load_phase_gate()
        repo = self.copy_repo()
        self.write_evidence(repo, self.evidence("FREE_PRIVATE_COMPENSATING"))
        evidence_dir = repo / "governance" / "evidence"
        (evidence_dir / "dogfood.json").write_text(json.dumps({
            "schema": "repo-factory.dogfood-evidence.v1",
            "repos": ["a", "b", "c"], "total_ticket_lifecycles": 40,
            "wrong_target": 0, "unauthorized_merge": 0, "false_verified": 0,
            "duplicate_pr_or_merge": 0, "wrong_check_source_merge": 0,
            "native_enforcement_drift": 0,
        }), encoding="utf-8")
        level, notes = gate.assurance_level(repo, True, True, True)
        self.assertEqual(level, "MULTI_REPO_DOGFOOD_VERIFIED")
        self.assertTrue(any("9_9_CANDIDATE 를 발급하지 않는다" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
