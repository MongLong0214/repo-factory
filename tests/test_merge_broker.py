"""Merge Broker — role-agnostic evidence-gated merge 판정."""
from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

import helpers as h


class BrokerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "repo"
        h.make_repo(cls.repo)
        cls.intent = h.make_intent(cls.repo)
        cls.now = h.parse_now(h.LATER)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def evaluate(self, intent=None, facts="default", now=None):
        if facts == "default":
            facts = h.make_facts(intent or self.intent)
        return h.BROKER.evaluate(self.repo, intent or self.intent, facts, now or self.now)

    def variant(self, **overrides) -> dict:
        intent = dict(self.intent)
        intent.update(overrides)
        return intent


class TestAuthorization(BrokerBase):
    def test_worker_intent_merges(self):
        result = self.evaluate()
        self.assertEqual(result["decision"], "MERGED", result)
        self.assertTrue(result["policy_authorized"])
        self.assertFalse(result["requester_role_considered"])

    def test_role_agnostic_same_predicate_for_all_registered(self):
        decisions = []
        for requester in ("worker-a", "rev-a", "spec-a", "ctrl-a"):
            intent = self.variant(requester_agent_id=requester,
                                  request_id=str(uuid.uuid4()))
            result = self.evaluate(intent, h.make_facts(intent))
            decisions.append(result["decision"])
        self.assertEqual(decisions, ["MERGED"] * 4)

    def test_unregistered_requester_refused(self):
        intent = self.variant(requester_agent_id="rando")
        result = self.evaluate(intent, h.make_facts(intent))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "UNREGISTERED_REQUESTER"))

    def test_forbidden_verdict_field_refused(self):
        intent = dict(self.intent)
        intent["approved"] = True
        result = self.evaluate(intent, h.make_facts(self.intent))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "FORBIDDEN_FIELD"))

    def test_requester_supplied_checks_passed_refused(self):
        intent = dict(self.intent)
        intent["checks_passed"] = True
        result = self.evaluate(intent, h.make_facts(self.intent))
        self.assertEqual(result["reason_code"], "FORBIDDEN_FIELD")


class TestStalenessAndReplay(BrokerBase):
    def test_expired_intent_stale(self):
        result = self.evaluate(now=h.parse_now(h.EXPIRED))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("STALE", "INTENT_EXPIRED"))

    def test_head_moved_stale(self):
        facts = h.make_facts(self.intent)
        facts["pr"]["head_sha"] = "c" * 40
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("STALE", "HEAD_MOVED"))

    def test_policy_changed_stale(self):
        intent = self.variant(policy_digest="sha256:" + "0" * 64)
        result = self.evaluate(intent, h.make_facts(intent))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("STALE", "POLICY_CHANGED"))

    def test_ttl_longer_than_policy_refused(self):
        intent = self.variant(expires_at="2026-08-08T03:00:00Z")
        result = self.evaluate(intent, h.make_facts(intent))
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "TTL_TOO_LONG"))

    def test_already_merged_idempotent(self):
        facts = h.make_facts(self.intent)
        facts["pr"]["merged"] = True
        result = self.evaluate(facts=facts)
        self.assertEqual(result["decision"], "ALREADY_MERGED")

    def test_replay_returns_receipt_not_duplicate_merge(self):
        receipts = Path(self._tmp.name) / "receipts"
        facts = h.make_facts(self.intent)
        first = h.BROKER.execute(self.repo, self.intent, facts, self.now, receipts, online=False)
        second = h.BROKER.execute(self.repo, self.intent, facts, self.now, receipts, online=False)
        self.assertEqual(first["decision"], "MERGED")
        self.assertNotIn("replay", first)
        self.assertTrue(second.get("replay"))

    def test_multi_agent_intents_single_merge(self):
        receipts = Path(self._tmp.name) / "receipts-multi"
        merged = 0
        for requester in ("worker-a", "rev-a", "spec-a"):
            intent = self.variant(requester_agent_id=requester,
                                  request_id=str(uuid.uuid4()))
            result = h.BROKER.execute(self.repo, intent, h.make_facts(intent),
                                      self.now, receipts, online=False)
            if result["decision"] == "MERGED" and not result.get("replay"):
                merged += 1
        self.assertEqual(merged, 1)


class TestEvidencePredicates(BrokerBase):
    def test_pending_check_deferred(self):
        facts = h.make_facts(self.intent)
        facts["checks"] = [c for c in facts["checks"] if c["name"] != "agent-review"]
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("DEFERRED", "CHECKS_PENDING"))

    def test_failed_check_refused(self):
        facts = h.make_facts(self.intent)
        facts["checks"][1]["conclusion"] = "failure"
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "CHECKS_FAILED"))

    def test_untrusted_check_creator_refused(self):
        facts = h.make_facts(self.intent)
        facts["checks"][0]["app"] = "evil-app"
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "CHECK_CREATOR_IDENTITY")

    def test_review_quorum_deferred(self):
        facts = h.make_facts(self.intent)
        facts["reviews"] = []
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("DEFERRED", "REVIEW_QUORUM"))

    def test_stale_review_head_not_counted(self):
        facts = h.make_facts(self.intent)
        facts["reviews"][0]["head_sha"] = "d" * 40
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "REVIEW_QUORUM")

    def test_reviewer_conflict_refused(self):
        facts = h.make_facts(self.intent)
        facts["reviews"] = [
            {"reviewer": "rev-a", "verdict": "PASS", "head_sha": h.HEAD, "findings": []},
            {"reviewer": "rev-b", "verdict": "BLOCK", "head_sha": h.HEAD, "findings": []},
        ]
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "REVIEWER_CONFLICT")

    def test_unresolved_p0_refused(self):
        facts = h.make_facts(self.intent)
        facts["reviews"][0]["findings"] = [{"severity": "P0", "resolved": False}]
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "UNRESOLVED_P0_P1")

    def test_worker_self_intent_does_not_skip_review(self):
        # worker 가 자기 PR에 intent 를 냈다는 사실이 review 를 대체하지 않는다
        facts = h.make_facts(self.intent)
        facts["reviews"] = []
        intent = self.variant(requester_agent_id="worker-a")
        result = self.evaluate(intent, facts)
        self.assertEqual(result["reason_code"], "REVIEW_QUORUM")

    def test_base_not_current_without_queue_deferred(self):
        facts = h.make_facts(self.intent)
        facts["base_branch_tip"] = "c" * 40
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("DEFERRED", "BASE_NOT_CURRENT"))

    def test_private_base_not_current_ignores_queue_flag(self):
        # queue_available 플래그가 있어도 private/개인 public 은 queue 를 쓰지 않는다
        # (merge queue 는 org 전용). allow_auto_merge 를 queue 로 오인하지 않는다.
        facts = h.make_facts(self.intent)
        facts["base_branch_tip"] = "c" * 40
        facts["queue_available"] = True
        result = self.evaluate(facts=facts)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("DEFERRED", "BASE_NOT_CURRENT"))

    def test_multiple_prs_refused(self):
        facts = h.make_facts(self.intent)
        facts["open_prs_for_ticket"] = [7, 9]
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "MULTIPLE_PRS")

    def test_operation_marker_mismatch_refused(self):
        facts = h.make_facts(self.intent)
        facts["pr"]["body"] = facts["pr"]["body"].replace(
            self.intent["operation_id"], "sha256:" + "9" * 64)
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "OPERATION_MISMATCH")

    def test_facts_unavailable_unknown(self):
        result = self.evaluate(facts=None)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("UNKNOWN", "FACTS_UNAVAILABLE"))

    def test_private_protection_not_a_gate(self):
        # private Free 에는 branch protection 이 없다 — None 이어도 merge 를 막지 않는다
        # (게이트는 external broker + exact-sha + marker + OOB audit). 회귀: 이 profile 에서
        # PROTECTION_NOT_CHECKED 로 막으면 실제 private repo 가 영영 merge 못 한다.
        facts = h.make_facts(self.intent)
        facts["branch_protection_ok"] = None
        result = self.evaluate(facts=facts)
        self.assertEqual(result["decision"], "MERGED", result)

    def test_budget_exceeded_refused(self):
        facts = h.make_facts(self.intent)
        facts["budget_exceeded"] = True
        result = self.evaluate(facts=facts)
        self.assertEqual(result["reason_code"], "BUDGET_EXCEEDED")

    def test_deps_not_checked_unknown(self):
        repo2 = Path(self._tmp.name) / "repo-deps"
        h.make_repo(repo2)
        h.write_ticket(repo2, h.ticket_meta("F2-001", title="Ticket highwork",
                                            risk="high", predelegated=True,
                                            dependencies=["F1-001"]))
        intent = h.make_intent(repo2, ticket="F2-001", pr=9)
        facts = h.make_facts(intent)
        facts["verified_source"] = "NOT_CHECKED"
        result = h.BROKER.evaluate(repo2, intent, facts, self.now)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("UNKNOWN", "DEPS_NOT_CHECKED"))
        facts["verified_source"] = "checked"
        facts["verified_tickets"] = []
        result = h.BROKER.evaluate(repo2, intent, facts, self.now)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("DEFERRED", "DEPS_UNVERIFIED"))
        facts["verified_tickets"] = ["F1-001"]
        quorum2 = h.make_facts(intent)
        facts["reviews"] = [
            {"reviewer": "rev-a", "verdict": "PASS", "head_sha": h.HEAD, "findings": []},
            {"reviewer": "rev-b", "verdict": "PASS", "head_sha": h.HEAD, "findings": []},
        ]
        del quorum2
        result = h.BROKER.evaluate(repo2, intent, facts, self.now)
        self.assertEqual(result["decision"], "MERGED")

    def test_high_not_predelegated_refused(self):
        repo3 = Path(self._tmp.name) / "repo-high"
        h.make_repo(repo3)
        h.write_ticket(repo3, h.ticket_meta("F2-001", title="Ticket highwork",
                                            risk="high", predelegated=False))
        intent = h.make_intent(repo3, ticket="F2-001", pr=11)
        result = h.BROKER.evaluate(repo3, intent, h.make_facts(intent), self.now)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("REFUSED", "NOT_PREDELEGATED"))


class TestPublicProtectionGate(unittest.TestCase):
    """public native profile 에서는 branch protection 이 필수 게이트다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name) / "pub"
        h.make_repo(cls.repo, h.public_config())
        cls.intent = h.make_intent(cls.repo)
        cls.now = h.parse_now(h.LATER)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def evaluate(self, **overrides):
        facts = h.make_facts(self.intent, **overrides)
        return h.BROKER.evaluate(self.repo, self.intent, facts, self.now)

    def test_public_protection_unknown_blocks_merge(self):
        result = self.evaluate(branch_protection_ok=None)
        self.assertEqual((result["decision"], result["reason_code"]),
                         ("UNKNOWN", "PROTECTION_NOT_CHECKED"))

    def test_public_protection_missing_refused(self):
        result = self.evaluate(branch_protection_ok=False)
        self.assertEqual(result["reason_code"], "PROTECTION_MISSING")

    def test_public_protection_present_merges(self):
        result = self.evaluate(branch_protection_ok=True)
        self.assertEqual(result["decision"], "MERGED", result)


if __name__ == "__main__":
    unittest.main()
