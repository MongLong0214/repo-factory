"""머지 후 verified 전이.

이 전이가 없던 동안 online_status 는 머지된 티켓을 영원히 post_merge 로만 표시했고
(reason_code 가 POST_MERGE_NOT_EVALUATED 였다), compute_ready 는 verified 만
의존성 완료로 세므로 루트 티켓 이후 그래프가 한 걸음도 진행하지 못했다.

증거는 merge SHA 의 post-merge check run 이다. 상태를 별도 저장소에 적지 않는다.
"""
from __future__ import annotations

import unittest
from unittest import mock

from pathlib import Path

import helpers as h

gov = h.load_module("governance", h.KIT / "scripts" / "governance.py")


def ticket_state(runs, *, merged=True, merge_sha="mergesha1", post_merge="post-merge",
                 raises=False):
    """online_status 의 머지 분기만 떼어내 재현한다."""
    checks_cfg = {"governance": "governance", "project_ci": "project-ci",
                  "review": "agent-review"}
    if post_merge is not None:
        checks_cfg["post_merge"] = post_merge
    pr = {"number": 7, "state": "closed", "merged_at": "2026-01-01T00:00:00Z",
          "body": "Ticket: T-01", "merge_commit_sha": merge_sha}
    facts = {"repository": "o/r", "pulls": [pr] if merged else []}
    context = {"tickets": {"T-01": {"meta": {}, "path": "T-01.md"}},
               "policy": {"checks": checks_cfg},
               "policy_digest": "sha256:test"}

    def fake_check_runs(repo, sha):
        if raises:
            raise gov.FactsUnavailable("gh 실패")
        return runs

    with mock.patch.object(gov, "validate_repo", return_value=(gov.Problems(), context)), \
         mock.patch.object(gov, "collect_facts", return_value=facts), \
         mock.patch.object(gov, "check_runs_for", side_effect=fake_check_runs):
        payload, code = gov.online_status(Path("."))
    return payload, code


class VerifiedTransitionTest(unittest.TestCase):
    def test_success_becomes_verified_with_evidence(self):
        payload, _ = ticket_state([{"name": "post-merge", "conclusion": "success"}])
        s = payload["tickets"]["T-01"]
        self.assertEqual(s["technical_state"], "verified")
        self.assertEqual(s["merge_sha"], "mergesha1")
        # 근거 없는 verified 는 만들지 않는다
        self.assertEqual(s["evidence"], "post-merge@mergesha1")

    def test_failure_becomes_repair_not_verified(self):
        payload, _ = ticket_state([{"name": "post-merge", "conclusion": "failure"}])
        s = payload["tickets"]["T-01"]
        self.assertEqual(s["technical_state"], "repair")
        self.assertEqual(s["reason_code"], "POST_MERGE_FAILED")

    def test_pending_stays_post_merge(self):
        payload, _ = ticket_state([{"name": "post-merge", "conclusion": None}])
        s = payload["tickets"]["T-01"]
        self.assertEqual(s["technical_state"], "post_merge")
        self.assertEqual(s["reason_code"], "POST_MERGE_PENDING")

    def test_missing_run_is_not_verified(self):
        # 실행 자체가 없으면 통과로 세탁하지 않는다
        payload, _ = ticket_state([{"name": "project-ci", "conclusion": "success"}])
        s = payload["tickets"]["T-01"]
        self.assertEqual(s["technical_state"], "post_merge")
        self.assertEqual(s["reason_code"], "POST_MERGE_RUN_MISSING")

    def test_facts_unavailable_is_unknown_not_verified(self):
        payload, _ = ticket_state([], raises=True)
        s = payload["tickets"]["T-01"]
        self.assertEqual(s["technical_state"], "unknown")
        self.assertEqual(s["reason_code"], "FACTS_UNAVAILABLE")

    def test_missing_merge_sha_does_not_verify(self):
        payload, _ = ticket_state([{"name": "post-merge", "conclusion": "success"}],
                                  merge_sha=None)
        self.assertEqual(payload["tickets"]["T-01"]["technical_state"], "post_merge")

    def test_missing_policy_check_name_does_not_verify(self):
        payload, _ = ticket_state([{"name": "post-merge", "conclusion": "success"}],
                                  post_merge=None)
        self.assertEqual(payload["tickets"]["T-01"]["technical_state"], "post_merge")

    def test_other_conclusions_are_repair(self):
        for c in ("cancelled", "timed_out", "action_required", "neutral"):
            with self.subTest(conclusion=c):
                payload, _ = ticket_state([{"name": "post-merge", "conclusion": c}])
                self.assertEqual(payload["tickets"]["T-01"]["technical_state"], "repair")


if __name__ == "__main__":
    unittest.main()
