"""governance kernel — validate / check-pr / status / manifest."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers as h


class GovernanceBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.pristine = Path(cls._tmp.name) / "pristine"
        h.make_repo(cls.pristine)
        # 추가 kind fixture: contract-change / governance-change
        h.write_ticket(cls.pristine, h.ticket_meta(
            "D0-010", title="Ticket contract", kind="contract-change",
            adr_refs=[], prd_ref=None,
            owned_paths=["docs/prd/PRD-F1-core.md"], oracle_paths=[], acceptance=[]))
        h.write_ticket(cls.pristine, h.ticket_meta(
            "G0-001", title="Ticket govchange", kind="governance-change", risk="critical",
            adr_refs=[], prd_ref=None,
            owned_paths=["governance/policy.v1.json"], oracle_paths=[], acceptance=[]))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def copy_repo(self) -> Path:
        target = Path(tempfile.mkdtemp(dir=self._tmp.name)) / "repo"
        shutil.copytree(self.pristine, target)
        return target


class TestValidate(GovernanceBase):
    def test_pristine_repo_passes(self):
        problems, context = h.validate(self.pristine)
        self.assertFalse(problems.items, problems.items)
        self.assertEqual(len(context["tickets"]), 4)
        self.assertTrue(str(context["policy_digest"]).startswith("sha256:"))

    def test_duplicate_ticket_id_fails(self):
        repo = self.copy_repo()
        src = repo / "docs/tickets/F1-001-parse.md"
        (repo / "docs/tickets/F1-001-copy.md").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_DUPLICATE_ID", h.problem_codes(problems))

    def test_dag_cycle_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("F1-001", title="Ticket parse",
                                           dependencies=["F2-001"]))
        h.write_ticket(repo, h.ticket_meta("F2-001", title="Ticket highwork",
                                           risk="high", predelegated=True,
                                           dependencies=["F1-001"]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_DAG_CYCLE", h.problem_codes(problems))

    def test_missing_adr_reference_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("F1-001", title="Ticket parse",
                                           adr_refs=["ADR-0099"]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_ADR_FILE", h.problem_codes(problems))

    def test_acceptance_without_case_fails(self):
        repo = self.copy_repo()
        meta = h.ticket_meta("F1-001", title="Ticket parse")
        meta["acceptance"][0]["cases"] = []
        h.write_ticket(repo, meta)
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_AC_NO_CASE", h.problem_codes(problems))

    def test_path_traversal_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("F1-001", title="Ticket parse",
                                           owned_paths=["../escape.py", "src/f1-001.py"]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_PATH", h.problem_codes(problems))

    def test_active_ownership_overlap_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("F2-001", title="Ticket highwork",
                                           risk="high", predelegated=True,
                                           owned_paths=["src/f1-001.py"]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_OWNERSHIP_OVERLAP", h.problem_codes(problems))

    def test_rollback_without_invalidates_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("R1-001", title="Ticket revertwork",
                                           kind="rollback", adr_refs=[], prd_ref=None,
                                           owned_paths=["src/f9.py"], oracle_paths=[],
                                           acceptance=[], invalidates=[]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_ROLLBACK_INVALIDATES", h.problem_codes(problems))

    def test_kernel_path_owned_by_implementation_fails(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("F1-001", title="Ticket parse",
                                           owned_paths=["scripts/governance.py"]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_KERNEL_OWNED", h.problem_codes(problems))

    def test_governance_change_must_be_critical(self):
        repo = self.copy_repo()
        h.write_ticket(repo, h.ticket_meta("G0-001", title="Ticket govchange",
                                           kind="governance-change", risk="standard",
                                           adr_refs=[], prd_ref=None,
                                           owned_paths=["governance/policy.v1.json"],
                                           oracle_paths=[], acceptance=[]))
        problems, _ = h.validate(repo)
        self.assertIn("TICKET_GOVERNANCE_RISK", h.problem_codes(problems))

    def test_policy_automerge_not_subset_fails(self):
        repo = self.copy_repo()
        policy_path = repo / "governance/policy.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["autonomy"]["auto_start"] = ["low"]
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("POLICY_AUTOMERGE_SUBSET", h.problem_codes(problems))

    def test_pat_allowed_fails(self):
        repo = self.copy_repo()
        policy_path = repo / "governance/policy.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["security"]["allow_personal_access_token"] = True
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("POLICY_PAT", h.problem_codes(problems))

    def test_raw_agent_credentials_fails(self):
        repo = self.copy_repo()
        policy_path = repo / "governance/policy.v1.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["merge"]["direct_agent_merge_credentials"] = True
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("POLICY_RAW_CREDENTIAL", h.problem_codes(problems))

    def test_policy_duplicate_copy_fails(self):
        repo = self.copy_repo()
        shutil.copy(repo / "governance/policy.v1.json", repo / "docs/policy-copy.json")
        problems, _ = h.validate(repo)
        self.assertIn("POLICY_DUPLICATE", h.problem_codes(problems))

    def test_current_state_artifact_fails(self):
        repo = self.copy_repo()
        (repo / "docs" / "ready-set.md").write_text("F1-001\n", encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("CURRENT_STATE_COMMITTED", h.problem_codes(problems))

    def test_genesis_approval_committed_fails(self):
        repo = self.copy_repo()
        (repo / "governance" / "genesis-approval.json").write_text("{}", encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("PRIVATE_METADATA_COMMITTED", h.problem_codes(problems))

    def test_action_tag_not_sha_fails(self):
        repo = self.copy_repo()
        wf = repo / ".github/workflows/ci.yml"
        wf.write_text(wf.read_text(encoding="utf-8").replace(
            "actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11",
            "actions/checkout@v4"), encoding="utf-8")
        problems, _ = h.validate(repo)
        self.assertIn("ACTION_NOT_SHA", h.problem_codes(problems))

    def test_high_ticket_missing_oracle_file_fails(self):
        repo = self.copy_repo()
        (repo / "conformance" / "F2-001.acceptance.py").unlink()
        problems, _ = h.validate(repo)
        self.assertIn("ORACLE_MISSING", h.problem_codes(problems))


class TestCheckPr(GovernanceBase):
    def run_check(self, changed, *, body="Ticket: F1-001\n", branch="feat/F1-001-parse",
                  base="dev", root=None):
        problems, not_checked = h.GOV.check_pr(
            root or self.pristine, body=body, branch=branch, base_ref=base,
            changed=changed, online=False)
        return h.problem_codes(problems), not_checked

    def test_owned_diff_passes(self):
        codes, not_checked = self.run_check([("M", "src/f1-001.py"), ("M", "test/test_f1-001.py")])
        self.assertFalse(codes, codes)
        self.assertTrue(any(item["state"] == "NOT_CHECKED" for item in not_checked))

    def test_unowned_diff_fails(self):
        codes, _ = self.run_check([("M", "src/rogue.py")])
        self.assertIn("PR_UNOWNED_DIFF", codes)

    def test_zero_ticket_lines_fails(self):
        codes, _ = self.run_check([("M", "src/f1-001.py")], body="no linkage\n")
        self.assertIn("PR_TICKET_LINE", codes)

    def test_two_ticket_lines_fails(self):
        codes, _ = self.run_check([("M", "src/f1-001.py")],
                                  body="Ticket: F1-001\nTicket: F2-001\n")
        self.assertIn("PR_TICKET_LINE", codes)

    def test_implementation_modifies_own_ticket_fails(self):
        codes, _ = self.run_check([("M", "docs/tickets/F1-001-parse.md")])
        self.assertIn("PR_SELF_EXPANSION", codes)

    def test_implementation_modifies_own_oracle_fails(self):
        codes, _ = self.run_check([("M", "conformance/F1-001.acceptance.py")])
        self.assertIn("PR_ORACLE_TOUCHED", codes)

    def test_implementation_modifies_other_oracle_fails(self):
        codes, _ = self.run_check([("M", "conformance/F2-001.acceptance.py")])
        self.assertIn("PR_OTHER_ORACLE", codes)

    def test_implementation_weakens_ci_fails(self):
        codes, _ = self.run_check([("M", ".github/workflows/ci.yml")])
        self.assertIn("PR_KERNEL_TOUCHED", codes)

    def test_implementation_touches_kernel_fails(self):
        codes, _ = self.run_check([("M", "scripts/governance.py")])
        self.assertIn("PR_KERNEL_TOUCHED", codes)

    def test_branch_ticket_mismatch_fails(self):
        codes, _ = self.run_check([("M", "src/f1-001.py")], branch="feat/F2-001-x")
        self.assertIn("PR_BRANCH_TICKET", codes)

    def test_wrong_base_fails(self):
        codes, _ = self.run_check([("M", "src/f1-001.py")], base="release/1.0")
        self.assertIn("PR_BASE", codes)

    def test_lockfile_change_requires_high_risk(self):
        codes, _ = self.run_check([("M", "package-lock.json")])
        self.assertIn("PR_DEPENDENCY_RISK", codes)

    def test_contract_change_mixed_with_product_fails(self):
        codes, _ = self.run_check(
            [("M", "docs/prd/PRD-F1-core.md"), ("M", "src/f1-001.py")],
            body="Ticket: D0-010\n", branch="contract/D0-010-x")
        self.assertIn("PR_CONTRACT_MIXED", codes)

    def test_governance_change_mixed_with_product_fails(self):
        codes, _ = self.run_check(
            [("M", "governance/policy.v1.json"), ("M", "src/f1-001.py")],
            body="Ticket: G0-001\n", branch="governance/G0-001-x")
        self.assertIn("PR_GOVERNANCE_MIXED", codes)

    def test_other_oracle_deleted_without_invalidation_fails(self):
        codes, _ = self.run_check([("D", "conformance/F2-001.acceptance.py")])
        self.assertIn("PR_ORACLE_DELETED", codes)

    def test_current_state_projection_edit_fails(self):
        codes, _ = self.run_check([("A", "docs/current-state.md")])
        self.assertIn("PR_CURRENT_STATE_EDIT", codes)

    def test_secret_in_added_file_fails(self):
        repo = self.copy_repo()
        secret_file = repo / "src" / "f1-001.py"
        secret_file.write_text('KEY = "AKIA' + "A" * 16 + '"\n', encoding="utf-8")
        codes, _ = self.run_check([("M", "src/f1-001.py")], root=repo)
        self.assertIn("PR_SECRET", codes)


class TestStatusManifest(GovernanceBase):
    def test_offline_status_never_claims_online(self):
        payload = h.GOV.offline_status(self.pristine)
        self.assertEqual(payload["external_state"], "NOT_CHECKED")
        self.assertFalse(payload["claims_online_readiness"])
        self.assertFalse(payload["claims_policy_authorization"])
        self.assertTrue(payload["contract_valid"])

    def test_manifest_deterministic(self):
        first, problems = h.GOV.build_manifest(self.pristine)
        second, _ = h.GOV.build_manifest(self.pristine)
        self.assertFalse(problems.items)
        self.assertEqual(first["manifest_digest"], second["manifest_digest"])
        self.assertEqual(set(first["tickets"]), {"F1-001", "F2-001", "D0-010", "G0-001"})

    def test_manifest_refused_when_contract_broken(self):
        repo = self.copy_repo()
        (repo / "governance/policy.v1.json").unlink()
        manifest, problems = h.GOV.build_manifest(repo)
        self.assertIsNone(manifest)
        self.assertTrue(problems.items)


if __name__ == "__main__":
    unittest.main()
