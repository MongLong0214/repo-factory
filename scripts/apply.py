#!/usr/bin/env python3
"""bootstrap_apply — 승인된 Plan 만 수행하고, 한 일마다 영수증을 남긴다 (PRD §16).

이 모듈이 지키는 것은 셋이다.

  Plan-before-Apply   승인된 Plan digest 밖의 Operation 은 실행하지 않는다 (§16.1)
  Post-write Reread   쓰고 나서 GitHub 에서 다시 읽어 기대와 대조한다 (§16.2)
  Provenance 멱등성    이름이 아니라 출처로 판단한다 (§16.3)

세 번째가 이 파일의 이유다. 같은 이름의 저장소가 이미 있다는 사실은 "우리가 아까
만들다 만 것" 과 "남의 것" 을 구분하지 못한다. 이름으로 resume 하면 두 번째 경우에
남의 저장소 위에 쓴다. 그래서 판단은 원장(ledger)이 그 Operation 에 대해 검증된
영수증을 갖고 있는가로만 한다 — 없으면 `RESOURCE_COLLISION` 이고, 아무것도 바꾸지
않는다.

Exit 0 은 완료의 증거가 아니다(§16.2). 명령이 성공했는데 원격 상태가 기대와 다른
경우가 이 계층이 존재하는 이유다.

여러 저장소를 만들 때 가짜 원자성을 주장하지 않는다(§16.4). 일부 성공하면 완료된
것·실패한 Operation·안전한 재개 지점을 그대로 보고한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from canonical import digest

__all__ = [
    "ApplyError", "GitHubPort", "ReceiptLedger", "apply_plan",
    "RESOURCE_COLLISION", "PLAN_INTENT_CHANGED", "REREAD_MISMATCH", "OPERATION_NOT_IN_PLAN",
    "OWNER_AUTHORIZATION_REQUIRED", "RESUMED_RESOURCE_ABSENT",
]

RESOURCE_COLLISION = "RESOURCE_COLLISION"
PLAN_INTENT_CHANGED = "PLAN_INTENT_CHANGED"
REREAD_MISMATCH = "REREAD_MISMATCH"
OPERATION_NOT_IN_PLAN = "OPERATION_NOT_IN_PLAN"
OWNER_AUTHORIZATION_REQUIRED = "OWNER_AUTHORIZATION_REQUIRED"
RESUMED_RESOURCE_ABSENT = "RESUMED_RESOURCE_ABSENT"


class ApplyError(RuntimeError):
    """중단 사유. `code` 는 안정 문자열이고, `receipts` 는 그때까지 검증된 것들이다."""

    def __init__(self, code: str, message: str, receipts: List[Dict[str, Any]], evidence: Dict[str, Any] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.receipts = receipts
        self.evidence = evidence or {}


class GitHubPort(Protocol):
    """외부 쓰기 표면. 실제 구현과 테스트 대역이 같은 계약을 만족한다."""

    def observe(self, resource_type: str, identity: str) -> Optional[Dict[str, Any]]:
        """없으면 None. 이것이 preexisting 과 reread 양쪽의 눈이다."""

    def create(self, resource_type: str, identity: str, spec: Dict[str, Any]) -> None:
        """만들기만 한다. 만들어졌는지는 호출자가 다시 읽어 판단한다."""


class ReceiptLedger:
    """검증된 영수증만 담는 durable 원장. 재개는 이 파일에서만 읽는다."""

    def __init__(self, path: Path):
        self.path = path
        self._rows: Dict[str, Dict[str, Any]] = {}
        if path.is_file():
            for row in json.loads(path.read_text(encoding="utf-8")):
                self._rows[row["operationId"]] = row

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        return self._rows.get(operation_id)

    def record(self, receipt: Dict[str, Any]) -> None:
        self._rows[receipt["operationId"]] = receipt
        # 다음 Operation 전에 쓴다. 프로세스가 여기서 죽어도 재개 지점이 남는다.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(list(self._rows.values()), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def all(self) -> List[Dict[str, Any]]:
        return list(self._rows.values())


def _receipt(plan: Dict[str, Any], operation: Dict[str, Any], *, preexisting: bool,
             before: Optional[Dict[str, Any]], after: Dict[str, Any], clock: str) -> Dict[str, Any]:
    return {
        "bootstrapOperationId": plan["bootstrapOperationId"],
        "requestDigest": plan["requestDigest"],
        "operationId": operation["operationId"],
        "resourceType": operation["resourceType"],
        "resourceIdentity": operation["resourceIdentity"],
        "preexisting": preexisting,
        "beforeStateDigest": digest(before, volatile="allow") if before is not None else None,
        "afterStateDigest": digest(after, volatile="allow"),
        "createdAt": clock,
        "rereadAt": clock,
        "verified": True,
    }


def apply_plan(plan: Dict[str, Any], port: GitHubPort, ledger: ReceiptLedger,
               *, specs: Dict[str, Dict[str, Any]] = None, clock: str = "1970-01-01T00:00:00Z") -> Dict[str, Any]:
    """Plan 의 Operation 을 순서대로 수행하고 영수증 목록을 돌려준다.

    `specs` 는 operationId → 생성 파라미터. Plan 에 없는 operationId 를 담고 있으면
    거부한다 — Plan 밖의 쓰기가 spec 을 통해 새어드는 경로가 그것이다(§16.1)."""
    specs = specs or {}
    planned = {op["operationId"]: op for op in plan["githubOperations"]}
    stray = sorted(set(specs).difference(planned))
    if stray:
        raise ApplyError(OPERATION_NOT_IN_PLAN,
                         f"spec supplied for operations the approved plan does not contain: {stray}",
                         ledger.all(), {"operations": stray})

    # RF-S25 — Hermes 가 승인한 Plan 이라도 Public 노출은 Owner 결정이다. 컴파일러가
    # 이미 authorization 을 OWNER 로 올리지만, 여기서 다시 본다. 계획을 만든 코드와
    # 계획을 실행하는 코드가 같은 가정을 공유하면 그 가정이 틀렸을 때 아무도 안 막는다.
    if plan.get("authorization") == "HERMES":
        public = sorted(r["identity"] for r in plan.get("repositories", [])
                        if r.get("visibility") == "public")
        if public:
            raise ApplyError(OWNER_AUTHORIZATION_REQUIRED,
                             "a Hermes-authorised plan may not create public repositories",
                             ledger.all(), {"repositories": public})

    applied: List[Dict[str, Any]] = []
    for operation in plan["githubOperations"]:
        operation_id = operation["operationId"]
        prior = ledger.get(operation_id)
        if prior is not None:
            if prior["requestDigest"] != plan["requestDigest"]:
                raise ApplyError(PLAN_INTENT_CHANGED,
                                 f"{operation_id} was applied under a different approved intent",
                                 applied, {"operationId": operation_id})
            # §16.3 은 같은 **Resource** 도 요구한다. 영수증은 과거에 썼다는 증거이지
            # 지금 있다는 증거가 아니다 — 그 사이 지워졌을 수 있고, 다시 읽지 않으면
            # 사라진 저장소를 완료로 보고한다.
            still_there = port.observe(operation["resourceType"], operation["resourceIdentity"])
            if still_there is None:
                raise ApplyError(RESUMED_RESOURCE_ABSENT,
                                 f"{operation['resourceIdentity']} has a verified receipt but is "
                                 f"absent from the remote; the ledger and the world disagree",
                                 applied, {"operationId": operation_id})
            applied.append(prior)
            continue

        observed = port.observe(operation["resourceType"], operation["resourceIdentity"])
        if observed is not None:
            # 이름은 같은데 이 Bootstrap 의 영수증이 없다. 우리 것이라고 추정하지 않는다.
            raise ApplyError(RESOURCE_COLLISION,
                             f"{operation['resourceIdentity']} already exists and carries no receipt "
                             f"from this bootstrap operation",
                             applied,
                             {"operationId": operation_id, "resourceIdentity": operation["resourceIdentity"]})

        port.create(operation["resourceType"], operation["resourceIdentity"], specs.get(operation_id, {}))

        # §16.2 — 다시 읽는다. 명령이 성공했다는 것과 원격이 기대대로라는 것은 다르다.
        after = port.observe(operation["resourceType"], operation["resourceIdentity"])
        if after is None:
            raise ApplyError(REREAD_MISMATCH,
                             f"{operation['resourceIdentity']} is absent when re-read after a successful create",
                             applied, {"operationId": operation_id})

        receipt = _receipt(plan, operation, preexisting=False, before=None, after=after, clock=clock)
        ledger.record(receipt)
        applied.append(receipt)

    return {"receipts": applied, "completed": len(applied) == len(plan["githubOperations"])}
