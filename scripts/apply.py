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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from canonical import digest

__all__ = [
    "ApplyError", "GitHubPort", "ReceiptLedger", "apply_plan",
    "RESOURCE_COLLISION", "PLAN_INTENT_CHANGED", "REREAD_MISMATCH", "PHASE_OUT_OF_ORDER",
    "UNKNOWN_PHASE", "UNSUPPORTED_INTENT", "OWNER_AUTHORIZATION_REQUIRED", "RESUMED_RESOURCE_ABSENT",
    "LEDGER_CORRUPT", "RESUMED_RESOURCE_DRIFTED", "AUTHORIZATION_MISSING",
    "AUTHORIZATION_INSUFFICIENT", "AUTHORIZATION_SPENT", "AUTHORITY_RANK",
    "authorized_plan_receipt",
    "PHASES",
    "PUBLISH_OPERATION",
]

PHASES = ("before-files", "after-files")
# OWNER 가 HERMES 를 덮는다. 반대는 아니다.
AUTHORITY_RANK = {"HERMES": 0, "OWNER": 1}
PUBLISH_OPERATION = "publish:{identity}"

RESOURCE_COLLISION = "RESOURCE_COLLISION"
PLAN_INTENT_CHANGED = "PLAN_INTENT_CHANGED"
REREAD_MISMATCH = "REREAD_MISMATCH"
# OPERATION_NOT_IN_PLAN is gone with the `specs` argument it guarded. It refused a creation
# parameter naming an operation the plan did not contain — a real hole while the effect lived
# outside the plan, and unreachable now that the only effect an operation can have is the one
# written inside it. A refusal no input can reach is not protection; it is an answer of "yes"
# to "is this checked?".
AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
AUTHORIZATION_INSUFFICIENT = "AUTHORIZATION_INSUFFICIENT"
AUTHORIZATION_SPENT = "AUTHORIZATION_SPENT"
LEDGER_CORRUPT = "LEDGER_CORRUPT"
RESUMED_RESOURCE_DRIFTED = "RESUMED_RESOURCE_DRIFTED"
PHASE_OUT_OF_ORDER = "PHASE_OUT_OF_ORDER"
UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
UNKNOWN_PHASE = "UNKNOWN_PHASE"
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

    def update(self, resource_type: str, identity: str, spec: Dict[str, Any]) -> None:
        """있는 것을 바꾸기만 한다. 바뀌었는지는 호출자가 다시 읽어 판단한다."""


class ReceiptLedger:
    """검증된 영수증만 담는 durable 원장. 재개는 이 파일에서만 읽는다."""

    REQUIRED_FIELDS = ("bootstrapOperationId", "requestDigest", "operationId", "resourceType",
                       "resourceIdentity", "afterStateDigest", "createdAt", "rereadAt", "verified")

    def __init__(self, path: Path):
        self.path = path
        self._rows: Dict[str, Dict[str, Any]] = {}
        if path.is_file():
            for row in json.loads(path.read_text(encoding="utf-8")):
                # 중복은 마지막 행이 이기는 게 아니라 거부다. 이기게 두면 같은 operationId 로
                # 다른 resource 를 가리키는 행을 덧붙이는 것만으로 재개가 다른 것을 재개한다.
                if row.get("operationId") in self._rows:
                    raise ApplyError(LEDGER_CORRUPT,
                                     f"the ledger has two rows for {row.get('operationId')!r}; "
                                     f"a resume cannot tell which one it is resuming",
                                     [], {"operationId": row.get("operationId")})
                missing = [field for field in self.REQUIRED_FIELDS if field not in row]
                if missing:
                    raise ApplyError(LEDGER_CORRUPT,
                                     f"a ledger row is missing {missing}; a receipt that does not "
                                     f"say what it verified cannot be read as proof that it did",
                                     [], {"operationId": row.get("operationId"), "missing": missing})
                self._rows[row["operationId"]] = row

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        return self._rows.get(operation_id)

    def record(self, receipt: Dict[str, Any]) -> None:
        self._rows[receipt["operationId"]] = receipt
        # 다음 Operation 전에 쓴다. 프로세스가 여기서 죽어도 재개 지점이 남는다.
        #
        # 원자적으로 쓴다. 제자리 쓰기 도중에 죽으면 잘린 JSON 이 남고, 다음 실행은 원장을
        # 아예 못 읽는다 — 재개 지점을 지키려고 만든 파일이 재개를 막는다.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(list(self._rows.values()), ensure_ascii=False, indent=2)
        scratch = self.path.with_name(self.path.name + ".partial")
        # 0600 으로 연다. 원장은 어떤 계정에 무엇을 만들었는지의 기록이고, 그것을 world-readable
        # 로 두는 것은 이 파일이 답하는 질문에 어울리지 않는다.
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, self.path)
        # 디렉토리도 fsync 한다. `os.replace` 는 이름 바꾸기고, 그 이름이 디스크에 닿았는지는
        # 디렉토리 엔트리가 flush 됐는지의 문제다 — 파일 내용만 fsync 하면 크래시 뒤에
        # 내용은 있는데 그 이름으로는 없는 상태가 가능하다.
        directory = os.open(str(self.path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def all(self) -> List[Dict[str, Any]]:
        return list(self._rows.values())


def _receipt(plan: Dict[str, Any], operation: Dict[str, Any], *, preexisting: bool,
             before: Optional[Dict[str, Any]], after: Dict[str, Any],
             created_at: str, reread_at: str) -> Dict[str, Any]:
    return {
        "bootstrapOperationId": plan["bootstrapOperationId"],
        "requestDigest": plan["requestDigest"],
        "operationId": operation["operationId"],
        "resourceType": operation["resourceType"],
        "resourceIdentity": operation["resourceIdentity"],
        "preexisting": preexisting,
        "beforeStateDigest": digest(before, volatile="allow") if before is not None else None,
        "afterStateDigest": digest(after, volatile="allow"),
        "createdAt": created_at,
        "rereadAt": reread_at,
        "verified": True,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_gap(desired: Any, observed: Any, path: str = "") -> List[str]:
    """승인된 상태가 관측된 상태 **안에** 있는지 본다.

    같은지가 아니라 안에 있는지다. GitHub 은 우리가 안 적은 필드를 채워서 돌려주고
    (`bypass_actors` 의 기본값, 규칙 파라미터의 기본값), 완전 일치를 요구하면 옳게 만들어진
    리소스가 매번 불일치로 걸린다. Plan 이 말한 것만 대조하고, 말하지 않은 것은 판단하지
    않는다 — 판단하지 않는다는 사실이 Plan 에 그대로 남아 있다.

    규칙 목록은 순서가 아니라 `type` 으로 맞춘다. 같은 규칙 집합을 다른 순서로 돌려주는 것은
    같은 보호이고, 그것을 위반으로 부르면 통과할 수 없는 검사가 된다."""
    gaps: List[str] = []
    if isinstance(desired, dict):
        if not isinstance(observed, dict):
            return [f"{path or '<root>'}: expected an object, observed {type(observed).__name__}"]
        for key in sorted(desired):
            here = f"{path}.{key}" if path else key
            if key not in observed:
                gaps.append(f"{here}: absent from the re-read")
                continue
            gaps.extend(_state_gap(desired[key], observed[key], here))
        return gaps
    if isinstance(desired, list):
        if not isinstance(observed, list):
            return [f"{path or '<root>'}: expected a list, observed {type(observed).__name__}"]
        keyed = all(isinstance(item, dict) and "type" in item for item in desired)
        if keyed:
            by_type = {item.get("type"): item for item in observed if isinstance(item, dict)}
            for item in desired:
                kind = item["type"]
                if kind not in by_type:
                    gaps.append(f"{path}[type={kind}]: absent from the re-read")
                    continue
                gaps.extend(_state_gap(item, by_type[kind], f"{path}[type={kind}]"))
            return gaps
        if desired != observed:
            gaps.append(f"{path or '<root>'}: approved {desired!r}, observed {observed!r}")
        return gaps
    if desired != observed:
        gaps.append(f"{path or '<root>'}: approved {desired!r}, observed {observed!r}")
    return gaps


def authorized_plan_receipt(plan: Dict[str, Any], *, authority: str, actor: str,
                            approved_at: str, session_id: str = None,
                            binding_generation: int = None,
                            source_receipt: str = None) -> Dict[str, Any]:
    """승인자가 만드는 문서. Plan 을 고치면 `planDigest` 가 더 이상 그 Plan 을 안 가리킨다."""
    if authority not in AUTHORITY_RANK:
        raise ApplyError(AUTHORIZATION_INSUFFICIENT, f"unknown authority {authority!r}", [], {})
    receipt = {
        "schema": "repo-factory.authorized-plan-receipt.v1",
        "planDigest": digest(plan),
        "bootstrapOperationId": plan["bootstrapOperationId"],
        "authority": authority,
        "approvedBy": {"actor": actor},
        "approvedAt": approved_at,
    }
    if session_id is not None:
        receipt["approvedBy"]["sessionId"] = session_id
    if binding_generation is not None:
        receipt["approvedBy"]["bindingGeneration"] = binding_generation
    if source_receipt is not None:
        receipt["sourceReceipt"] = source_receipt
    return receipt


def _check_authorization(plan: Dict[str, Any], receipt: Optional[Dict[str, Any]],
                         ledger: ReceiptLedger) -> None:
    """승인은 Plan 안의 문자열이 아니라 Plan 을 가리키는 별개의 문서다.

    `authorization: "OWNER"` 가 Plan 안에 있으면, 그 값을 고치고 다시 digest 한 Plan 은
    스스로를 승인한 것과 구별되지 않는다. 승인자가 만드는 영수증은 **어떤 digest 를**
    승인했는지를 말하므로, Plan 을 고치면 그 승인이 더 이상 이 Plan 을 가리키지 않는다.

    서명은 아니다. 이 파일을 쓸 수 있는 사람은 승인을 주장할 수 있다. 이것이 사는 것은
    주장이 **별개의 아티팩트**가 되고, 행위자·시각·묶인 digest 를 갖고, 읽는 쪽이 눈앞의
    Plan 과 대조할 수 있다는 것이다."""
    required = plan.get("authorization", "OWNER")
    if receipt is None:
        raise ApplyError(AUTHORIZATION_MISSING,
                         f"this plan needs a {required} approval receipt and none was supplied; "
                         f"a plan asserting its own authority is not an approval",
                         ledger.all(), {"required": required})
    stated = digest(plan)
    if receipt.get("planDigest") != stated:
        raise ApplyError(AUTHORIZATION_MISSING,
                         f"the approval receipt covers {receipt.get('planDigest')} and this plan "
                         f"digests to {stated}",
                         ledger.all(), {"approved": receipt.get("planDigest"), "plan": stated})
    if receipt.get("bootstrapOperationId") != plan["bootstrapOperationId"]:
        raise ApplyError(AUTHORIZATION_MISSING,
                         "the approval receipt was issued for a different bootstrap operation",
                         ledger.all(), {"operationId": receipt.get("bootstrapOperationId")})
    held = AUTHORITY_RANK.get(str(receipt.get("authority")), -1)
    if held < AUTHORITY_RANK.get(required, 1):
        raise ApplyError(AUTHORIZATION_INSUFFICIENT,
                         f"this plan needs {required} and the receipt carries "
                         f"{receipt.get('authority')!r}",
                         ledger.all(), {"required": required, "held": receipt.get("authority")})
    if receipt.get("revoked") or receipt.get("supersededBy"):
        raise ApplyError(AUTHORIZATION_SPENT,
                         "the approval receipt has been revoked or superseded",
                         ledger.all(), {"supersededBy": receipt.get("supersededBy"),
                                        "revoked": bool(receipt.get("revoked"))})
    if not receipt.get("approvedAt") or not (receipt.get("approvedBy") or {}).get("actor"):
        raise ApplyError(AUTHORIZATION_MISSING,
                         "the approval receipt does not say who approved it, or when",
                         ledger.all(), {})


def apply_plan(plan: Dict[str, Any], port: GitHubPort, ledger: ReceiptLedger,
               *, authorization: Dict[str, Any] = None, clock=None,
               phase: str = "before-files") -> Dict[str, Any]:
    """Plan 의 Operation 을 순서대로 수행하고 영수증 목록을 돌려준다.

    생성 파라미터는 Operation 의 `desiredState` 다. 한때 별도 `specs` 인자로 들어왔는데,
    그러면 승인된 Plan digest 와 실제로 실행되는 effect 가 서로 다른 객체가 된다 — private
    으로 승인된 Plan 이 public 저장소를 만들어도 digest 는 같았다(§16.1)."""
    # 시계는 주입 가능하지만 영수증마다 다시 읽힌다. 호출자가 값 하나를 건네고 그것이
    # createdAt 과 rereadAt 양쪽에 박히면, 일어나지 않은 시각의 재조회를 주장하게 된다.
    now = clock or _utc_now
    # 승인 게이트가 보는 값과 실제로 실행될 값이 같은 사실을 말하는지 본다. 저장소의
    # 노출은 두 자리에 적힌다 — `repositories[].visibility` 를 게이트가 읽고 Operation 의
    # `desiredState.private` 가 실행된다. 두 자리가 어긋나면 게이트가 승인한 것과 만들어질
    # 것이 다르고, 어느 쪽도 그 사실을 모른다.
    by_identity = {r["identity"]: r for r in plan.get("repositories", [])}
    for operation in plan["githubOperations"]:
        if operation["resourceType"] != "repository":
            continue
        repository = by_identity.get(operation["resourceIdentity"])
        if repository is None:
            continue
        approved_private = repository.get("visibility") != "public"
        if operation.get("desiredState", {}).get("private") != approved_private:
            raise ApplyError(PLAN_INTENT_CHANGED,
                             f"{operation['resourceIdentity']} is approved as "
                             f"{repository.get('visibility')!r} and its operation would create it "
                             f"private={operation.get('desiredState', {}).get('private')!r}",
                             ledger.all(), {"operationId": operation["operationId"]})

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

    # 원격을 읽기 전에 권한을 본다. 읽는 것도 부작용이 있는 호출이고, 무엇보다 승인 없이
    # 시작한 실행은 어디까지 갔든 승인 없이 간 것이다.
    _check_authorization(plan, authorization, ledger)

    # 모르는 단계는 빈 목록이 아니라 거부다. 오타 하나가 staged 를 0개로 만들고, 0개는
    # 전부 끝났다는 뜻으로 읽혀서 `completed: true` 가 나온다 — 아무것도 안 하고 완료를
    # 보고하는 가장 조용한 경로다.
    if phase not in PHASES:
        raise ApplyError(UNKNOWN_PHASE, f"{phase!r} is not a phase this plan has: {list(PHASES)}",
                         ledger.all(), {"phase": phase})

    # `after-files` 는 앞 단계가 실제로 끝났을 때만 열린다. 순서는 주석이 아니라 상태다 —
    # `project-ci` 를 요구하는 ruleset 이 그 워크플로를 실어 나르는 커밋보다 먼저 있으면,
    # 저장소에 내용을 넣는 바로 그 푸시를 저장소가 거부한다.
    if phase == "after-files":
        earlier = [op for op in plan["githubOperations"] if op.get("phase", "before-files") == "before-files"]
        unfinished = sorted(op["operationId"] for op in earlier if ledger.get(op["operationId"]) is None)
        if unfinished:
            raise ApplyError(PHASE_OUT_OF_ORDER,
                             f"before-files has not finished: {unfinished}",
                             ledger.all(), {"phase": phase, "waitingOn": unfinished})
        targets = sorted({op["resourceIdentity"].split("#", 1)[0] for op in plan["githubOperations"]
                          if op.get("phase") == "after-files"})
        unpublished = [identity for identity in targets
                       if ledger.get(PUBLISH_OPERATION.format(identity=identity)) is None]
        if unpublished:
            raise ApplyError(PHASE_OUT_OF_ORDER,
                             f"the genesis commit has not been published to {unpublished}; a ruleset "
                             f"requiring a workflow cannot exist before the commit that carries it",
                             ledger.all(), {"phase": phase, "waitingOn": unpublished})

    applied: List[Dict[str, Any]] = []
    staged = [op for op in plan["githubOperations"] if op.get("phase", "before-files") == phase]
    for operation in staged:
        operation_id = operation["operationId"]
        prior = ledger.get(operation_id)
        if prior is not None:
            # 영수증이 이 Operation 의 것인지를 네 가지로 본다. `requestDigest` 만 보면
            # 같은 승인 아래의 **다른** Operation 영수증이 이 자리를 채울 수 있다.
            mismatched = [
                field for field, expected in (
                    ("requestDigest", plan["requestDigest"]),
                    ("bootstrapOperationId", plan["bootstrapOperationId"]),
                    ("resourceType", operation["resourceType"]),
                    ("resourceIdentity", operation["resourceIdentity"]),
                )
                if prior.get(field) != expected
            ]
            if mismatched:
                raise ApplyError(PLAN_INTENT_CHANGED,
                                 f"{operation_id} has a receipt that disagrees with the plan on "
                                 f"{mismatched}",
                                 applied, {"operationId": operation_id, "fields": mismatched})
            if not prior.get("verified") or not prior.get("rereadAt"):
                raise ApplyError(PLAN_INTENT_CHANGED,
                                 f"{operation_id} has a receipt that never claimed a verified "
                                 f"post-write re-read",
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
            # 있다는 것과 그때 그대로라는 것은 다르다. 그 사이 누가 ruleset 을 `disabled` 로
            # 바꿔놨어도 존재 검사만으로는 재개가 통과한다.
            if digest(still_there, volatile="allow") != prior["afterStateDigest"]:
                raise ApplyError(RESUMED_RESOURCE_DRIFTED,
                                 f"{operation['resourceIdentity']} is present but no longer in the "
                                 f"state its receipt recorded",
                                 applied, {"operationId": operation_id})
            applied.append(prior)
            continue

        intent = operation.get("intent", "create")
        if intent not in ("create", "update"):
            raise ApplyError(UNSUPPORTED_INTENT,
                             f"{operation_id} asks for intent {intent!r}, which this applier does "
                             f"not perform; an intent nobody implements is not a plan that ran",
                             applied, {"operationId": operation_id, "intent": intent})

        observed = port.observe(operation["resourceType"], operation["resourceIdentity"])
        if intent == "create" and observed is not None:
            # 이름은 같은데 이 Bootstrap 의 영수증이 없다. 우리 것이라고 추정하지 않는다.
            raise ApplyError(RESOURCE_COLLISION,
                             f"{operation['resourceIdentity']} already exists and carries no receipt "
                             f"from this bootstrap operation",
                             applied,
                             {"operationId": operation_id, "resourceIdentity": operation["resourceIdentity"]})
        if intent == "update" and observed is None:
            # `update` 는 있는 것을 바꾼다. 없는데 만들지 않는다 — 없으면 무엇을 바꾸는지에
            # 대한 승인이 아니라 무엇을 만드는지에 대한 승인이 필요하고, 그건 다른 결정이다.
            raise ApplyError(RESUMED_RESOURCE_ABSENT,
                             f"{operation['resourceIdentity']} is absent, and an update is not a "
                             f"licence to create it",
                             applied, {"operationId": operation_id})

        # 두 시각을 따로 읽는다. 하나로 쓰면 영수증이 "이 시각에 다시 읽었다" 고 말하는데
        # 그 시각에 재조회는 아직 일어나지 않았다 — §16.2 가 요구하는 것은 재조회가 있었다는
        # 사실이고, 영수증은 그것이 언제였는지를 말해야 한다.
        created_at = now()
        if intent == "create":
            port.create(operation["resourceType"], operation["resourceIdentity"], operation["desiredState"])
        else:
            port.update(operation["resourceType"], operation["resourceIdentity"], operation["desiredState"])

        # §16.2 — 다시 읽는다. 명령이 성공했다는 것과 원격이 기대대로라는 것은 다르다.
        after = port.observe(operation["resourceType"], operation["resourceIdentity"])
        if after is None:
            raise ApplyError(REREAD_MISMATCH,
                             f"{operation['resourceIdentity']} is absent when re-read after a successful "
                             f"{intent}",
                             applied, {"operationId": operation_id})

        # §16.2 는 "다시 읽었다" 가 아니라 "기대한 것이 거기 있다" 를 요구한다. 존재만 확인하면
        # `disabled` 로 만들어진 ruleset 과 `active` 로 만들어진 ruleset 이 같은 통과를 받는다.
        gaps = _state_gap(operation["desiredState"], after)
        if gaps:
            raise ApplyError(REREAD_MISMATCH,
                             f"{operation['resourceIdentity']} was created but does not match the "
                             f"approved state: {'; '.join(gaps[:5])}",
                             applied, {"operationId": operation_id, "gaps": gaps})

        receipt = _receipt(plan, operation, preexisting=intent == "update", before=observed, after=after,
                           created_at=created_at, reread_at=now())
        ledger.record(receipt)
        applied.append(receipt)

    return {"receipts": applied, "completed": len(applied) == len(staged), "phase": phase}


def _plan_core(document: Dict[str, Any]) -> Dict[str, Any]:
    """컴파일러 출력 전체를 받든 planCore 만 받든 같은 것을 가리키게 한다."""
    return document["planCore"] if "planCore" in document else document


def main(argv: List[str] = None) -> int:
    """단계 하나를 실행하고 영수증 원장을 남긴다.

    `apply` 와 `publish` 가 라이브러리 전용으로 남아 있는 동안, 공개된 성공 경로는
    파이썬 호출뿐이었다 — 문서가 설명하는 파이프라인의 가운데를 명령으로는 돌릴 수
    없었다."""
    import argparse

    parser = argparse.ArgumentParser(description="Apply one phase of an approved plan's external writes.")
    parser.add_argument("--plan", required=True, type=Path, help="compiler output, or a plan core")
    parser.add_argument("--ledger", required=True, type=Path, help="receipt ledger path; reused on resume")
    parser.add_argument("--phase", default="before-files", choices=["before-files", "after-files"])
    parser.add_argument("--authorization", type=Path, default=None,
                        help="the approval receipt covering this plan, from scripts/authorize.py; "
                             "a plan cannot approve itself, so without this no write is attempted")
    parser.add_argument("--gh", default="gh", help="the gh executable to invoke")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the operations this phase would perform and write nothing")
    args = parser.parse_args(argv)

    plan = _plan_core(json.loads(args.plan.read_text(encoding="utf-8")))
    staged = [op for op in plan["githubOperations"] if op.get("phase", "before-files") == args.phase]
    if args.dry_run:
        print(json.dumps({"phase": args.phase, "wouldApply": staged}, ensure_ascii=False, indent=2))
        return 0

    # 승인은 Plan 밖의 문서다. 이 인자가 없으면 `apply_plan` 이 어떤 원격 읽기보다 먼저
    # 거부한다 — 한동안 CLI 가 이것을 아예 안 넘겨서, 문서가 적어둔 실행 경로가 항상
    # AUTHORIZATION_MISSING 으로 죽었다. 통과하던 것은 `--dry-run` 뿐이었고, 그 경로는
    # 승인 게이트에 닿기 전에 반환한다.
    authorization = None
    if args.authorization is not None:
        try:
            authorization = json.loads(args.authorization.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"cannot read the approval receipt: {error}", file=sys.stderr)
            return 2

    from github_port import GhCliPort  # 지연 import — dry-run 은 gh 를 요구하지 않는다

    try:
        outcome = apply_plan(plan, GhCliPort(gh=args.gh), ReceiptLedger(args.ledger),
                             authorization=authorization, phase=args.phase)
    except ApplyError as error:
        print(json.dumps({"error": error.code, "message": str(error), "evidence": error.evidence,
                          "receipts": error.receipts}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
