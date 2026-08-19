#!/usr/bin/env python3
"""RepoFactoryResult 조립 — 저장소 사실만 담고, 활성화는 주장하지 않는다 (PRD §13.4).

이 문서의 수신자는 Agent Control Plane 이고, **판정 권위는 거기 있다**
(`src/bootstrap/repo-factory-result.ts` — `parseRepoFactoryResult`). 여기서 하는 검사는
그 판정의 복제가 아니라 **전제**다: 넘기기 전에 걸러내면 실패가 handoff 가 아니라
조립 자리에서 이름과 함께 난다.

수신자가 거부하는 네 가지 (읽어서 옮긴 것, 2026-08-19):

  활성화 사실 주장     primaryCto·buzz·doctor·blindReview·ceoConfirm·projectActive 등 12개 키
  스키마 불일치        `.strict()` — 모르는 키는 통과하지 않는다
  미검증 영수증        `verified` 가 false 이거나 `rereadAt` 이 null
  중복 operationId     영수증마다 유일해야 한다

세 번째가 이 계층의 이유다. 쓰기가 있었다는 것과 그 쓰기가 확인됐다는 것은 다르고,
확인되지 않은 영수증은 아무것의 증거도 아니다(§16.2).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

__all__ = ["ResultError", "FORBIDDEN_CLAIMS", "build_result", "RESULT_SCHEMA_ID"]

RESULT_SCHEMA_ID = "repo-factory.result.v2"

# 수신자의 `FORBIDDEN_CLAIMS` 를 그대로 옮긴 것. 이름이 아니라 존재로 거부되므로,
# 값이 무엇이든 이 키가 있으면 결과가 거절된다.
FORBIDDEN_CLAIMS = (
    "primaryCtoAssignment", "primaryCto", "buzzConnection", "buzz",
    "doctorPass", "doctor", "blindReviewPass", "blindReview",
    "ceoFinalConfirm", "ceoConfirm", "projectActive", "activity",
)


class ResultError(ValueError):
    """결과가 넘길 수 있는 상태가 아니다. 무엇이 왜인지 함께 보고한다."""


def build_result(
    *,
    run_id: str,
    plan: Dict[str, Any],
    plan_digest: str,
    repositories: List[Dict[str, Any]],
    receipts: List[Dict[str, Any]],
    bootstrap_verification: List[Dict[str, Any]],
    ci_evidence: List[Dict[str, Any]] = None,
    unresolved_gaps: List[str] = None,
) -> Dict[str, Any]:
    if not receipts:
        raise ResultError("a result with no external write receipt describes no bootstrap")
    if not bootstrap_verification:
        raise ResultError("a result with no bootstrap verification asserts a repository nobody ran")

    unverified = [r["resourceIdentity"] for r in receipts if not r.get("verified") or not r.get("rereadAt")]
    if unverified:
        raise ResultError(f"external write receipts are missing post-write re-read verification: {unverified}")

    seen: Dict[str, int] = {}
    for receipt in receipts:
        seen[receipt["operationId"]] = seen.get(receipt["operationId"], 0) + 1
    repeated = sorted(op for op, count in seen.items() if count > 1)
    if repeated:
        raise ResultError(f"external write receipt operation ids must be unique: {repeated}")

    # PASS 만 실을 수 있다. 실패한 검증은 여기 들어갈 자리가 없고, 그것이 정직한 모양이다 —
    # 남은 문제는 unresolvedGaps 로 말하거나, 결과 자체가 나오지 않는다.
    not_pass = [v["commandId"] for v in bootstrap_verification if v.get("status") != "PASS"]
    if not_pass:
        raise ResultError(f"bootstrap verification may only carry PASS: {not_pass}")

    result = {
        "schema": RESULT_SCHEMA_ID,
        "runId": run_id,
        "bootstrapOperationId": plan["bootstrapOperationId"],
        "planDigest": plan_digest,
        "projectManifestDigest": plan["projectManifestDigest"],
        "repositories": [
            {
                "role": repo["role"],
                "identity": repo["identity"],
                # §11 — 로컬 바인딩은 제안일 뿐이고 저장소에 커밋되지 않는다.
                "proposedCheckoutPath": repo.get("proposedCheckoutPath"),
                "defaultBranch": repo["defaultBranch"],
                "createdBranches": list(repo.get("createdBranches", [])),
            }
            for repo in repositories
        ],
        "externalWriteReceipts": list(receipts),
        "bootstrapVerification": list(bootstrap_verification),
        "ciEvidence": list(ci_evidence or []),
        "unresolvedGaps": list(unresolved_gaps or []),
    }

    # 활성화 사실을 막는 검사가 여기 있었다. 지웠다 — 발동할 수 없었기 때문이다.
    #
    # `result` 는 명명된 인자에서 필드 단위로 조립되므로 호출자가 최상위 키를 넣을 경로가
    # 없다. 그래서 그 검사는 어떤 입력으로도 실패하지 않았고, 있으나 없으나 같았다.
    # 반증할 수 없는 가드는 안전이 아니라 안전해 보이는 것이다.
    #
    # 실제 강제는 받는 쪽에 있고 거기서는 도달 가능하다 — `parseRepoFactoryResult` 가 키의
    # **존재**로 거부하며, 그 거부는 `tests/test_acp_contract.py` 의
    # `test_the_control_plane_still_refuses_a_result_that_claims_activation` 이 확인한다.
    # `FORBIDDEN_CLAIMS` 는 남는다: 그 목록이 받는 쪽과 같은지를 별도 테스트가 대조한다.
    return result


def main(argv: List[str] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Assemble a RepoFactoryResult from an applied plan.")
    parser.add_argument("--input", required=True, help="JSON with runId, plan, planDigest, repositories, receipts, bootstrapVerification")
    args = parser.parse_args(argv)
    payload = json.loads(open(args.input, encoding="utf-8").read())
    try:
        result = build_result(
            run_id=payload["runId"], plan=payload["plan"], plan_digest=payload["planDigest"],
            repositories=payload["repositories"], receipts=payload["receipts"],
            bootstrap_verification=payload["bootstrapVerification"],
            ci_evidence=payload.get("ciEvidence"), unresolved_gaps=payload.get("unresolvedGaps"),
        )
    except (ResultError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
