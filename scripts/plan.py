#!/usr/bin/env python3
"""bootstrap_plan — 승인된 요청을 결정적 Plan 으로 컴파일한다 (PRD §22.1).

외부 쓰기 전에 전체 Plan 을 볼 수 있어야 한다(§7 Phase E). 그래서 이 단계는
아무것도 만들지 않고, 무엇을 만들 것인지와 그중 무엇이 Owner 결정인지만 낸다.

산출물 넷:
  BootstrapPlanCore        정확한 의도. 두 번 컴파일하면 같은 digest
  EnvironmentObservation   변하는 관측. Plan 은 id 로만 참조한다 (§8.2)
  Plan Diff Summary        사람이 승인 전에 읽는 요약
  Human Gate Classification  HERMES 가 승인할 수 있는가, Owner 여야 하는가 (§7 Phase F)

Plan 과 Observation 을 나누는 이유는 한 문장이다 — 관측이 digest 에 섞이면 같은
의도가 매번 다른 digest 를 갖고, 승인이 승인한 것을 다시 가리키지 못한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical import digest  # noqa: E402
from materialize import CI_PATH, SKELETONS, artifact_coverage, materialize  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
PROFILES = SKILL / "profiles"
SCHEMAS = SKILL / "schemas"

MANIFEST_PATH = ".agent-control-plane/project.json"

# 요청이 소유자를 말하지 않으면 이 값을 쓴다. 상수로 두되 한 곳에만 둔다 — 네 곳에
# 흩어져 있던 동안에는 다른 계정으로 만들려는 요청이 조용히 이 계정으로 갔다.
DEFAULT_REMOTE_OWNER = "MongLong0214"

# 생성 저장소의 네이티브 보호 규칙 이름. Plan 이 정하고 영수증이 그것으로 리소스를 찾는다 —
# id 는 GitHub 이 배정하므로 만들기 전에 우리가 쥘 수 있는 손잡이는 이름뿐이다.
RULESET_NAME = "acp-managed-branches"

# 보호할 브랜치. `dev` 는 통합 지점이고 `main` 은 릴리스 지점이다 — 둘 다 직접 푸시로
# 제어평면을 우회할 수 있는 자리다.
RULESET_REFS: Tuple[str, ...] = ("refs/heads/main", "refs/heads/dev")

# 저장소가 요구하는 체크. Plan 이 싣는 워크플로의 job 이름과 같아야 하고, 그래서 ruleset 은
# 파일 뒤에 만든다 — 이 체크를 요구하는 규칙이 워크플로보다 먼저 있으면 그 워크플로를 실어
# 나르는 바로 그 푸시가 거부된다.
RULESET_REQUIRED_CHECK = "project-ci"

# 그 체크를 **누가** 보고해야 하는가. context 만 요구하면 그 이름으로 check-run 을 만드는
# 어떤 앱이든 규칙을 충족시킨다 — 저장소에 설치된 다른 GitHub App 이 `project-ci` 라는
# 이름으로 success 를 하나 올리면, 워크플로가 돌지 않아도 머지가 열린다. 요구하는 것이
# "이 이름의 통과" 가 아니라 "이 워크플로의 통과" 이므로 보고자를 같이 못 박는다.
#
# 15368 은 GitHub Actions 앱의 전역 고정 id 다(github.com 전체에서 같다). 이 저장소의
# 자기 ruleset 을 걸면서 실측했다: `commits/main/check-runs` 가 `app.id=15368` 을 보고한다.
RULESET_CHECK_REPORTER_APP_ID = 15368

# 통합 지점이 기본 브랜치다. `main` 은 릴리스 이력이고 일상 작업은 `dev` 로 간다.
DEFAULT_BRANCH = "dev"


def security_desired_state() -> Dict[str, Any]:
    """저장소가 가져야 할 보안 자세. public 저장소에서는 둘 다 기본 on 이므로 이 Operation 이
    하는 일은 켜는 것이 아니라 **꺼져 있으면 잡는 것**이다 — 그리고 그것이 요점이다.

    genesis push **앞**에 둔다. push protection 은 자격증명이 착지하는 것을 막는 것이고,
    파일이 올라간 뒤에 켜면 그 첫 푸시는 보호받지 못한 채 지나간다. 순서가 곧 보증이다."""
    return {"secretScanning": "enabled", "pushProtection": "enabled"}


def ruleset_desired_state() -> Dict[str, Any]:
    """승인이 승인하는 것은 이름이 아니라 이 몸통이다.

    한동안 Plan 은 ruleset 의 **이름만** 실었고 실제 보호 강도 — enforcement, 어떤 ref 에
    걸리는지, 어떤 체크를 요구하는지, 누가 우회할 수 있는지 — 는 Plan 밖 인자에서 왔다.
    그러면 승인된 digest 는 "acp-managed-branches 라는 ruleset 을 만든다" 까지만 말하고,
    그것이 `disabled` 로 만들어져도 같은 digest 다."""
    return {
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": list(RULESET_REFS), "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_status_checks",
             "parameters": {"strict_required_status_checks_policy": True,
                            "required_status_checks": [
                                {"context": RULESET_REQUIRED_CHECK,
                                 "integration_id": RULESET_CHECK_REPORTER_APP_ID}]}},
        ],
        # 비어 있음이 이 계획의 진술이다. 생략하면 "우회자를 정하지 않았다" 가 되고, 그 자리는
        # Plan 밖에서 채워질 수 있다.
        "bypass_actors": [],
    }

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def _schema(name: str) -> Dict[str, Any]:
    """우리가 싣고 다니는 스키마. 한 번 읽고 재사용한다."""
    if name not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[name] = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[name]


def _plan_schema() -> Dict[str, Any]:
    return _schema("bootstrap-plan.schema.json")


def validate_request(request: Dict[str, Any]) -> None:
    """입력도 출력과 같은 문으로 들어와야 한다.

    컴파일러가 자기 출력만 검증하고 입력은 안 하면, 스키마가 금지하는 요청이 통과해서
    Plan 이 된다. `remoteOwner` 가 정확히 그렇게 지냈다 — 컴파일러가 읽고 테스트가 넣는
    필드인데 `additionalProperties: false` 인 요청 스키마 어디에도 선언돼 있지 않았고,
    프로덕션 경로가 요청을 검증하지 않아서 아무도 몰랐다."""
    invalid = sorted(Draft202012Validator(_schema("bootstrap-request.schema.json")).iter_errors(request), key=str)
    if invalid:
        raise PlanError(
            "the request does not satisfy schemas/bootstrap-request.schema.json: "
            + "; ".join(f"{'.'.join(str(x) for x in e.path) or '<root>'}: {e.message}" for e in invalid[:5])
        )
    # 이 공장이 만드는 저장소는 ruleset 으로 보호된다. private 저장소의 ruleset 은 GitHub
    # Pro 이상을 요구하고, 이 배포는 Pro 를 쓰지 않는다 — 그러면 private Plan 은 컴파일은
    # 되지만 `after-files` 에서 반드시 죽는 Plan 이다. 실측: `create-ruleset` 이 HTTP 403 으로
    # 거부되고, 저장소·genesis 커밋·기본 브랜치까지만 영수증이 남았다(#39).
    #
    # 만들 수 없는 것을 계획하지 않는다. 계획하고 나중에 죽는 것보다, 계획하지 않고 지금
    # 이유를 말하는 편이 낫다 — 그 사이에 원격 저장소 하나가 실재하게 되기 때문이다.
    if request.get("visibility") != "public":
        raise PlanError(
            f"this factory creates public repositories: visibility {request.get('visibility')!r} "
            f"is not one it can finish. Every profile plans a branch ruleset, and rulesets on "
            f"private repositories require GitHub Pro; a private plan compiles and then fails at "
            f"after-files with a repository already created."
        )


def content_digest(text: str) -> str:
    """파일은 구조가 아니라 바이트다. 정규화 없이 그대로 센다."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

# §9.2 를 그대로 옮긴 것. Parent 는 선언되며, 제어평면의 GitHub kernel 이 PR 생성과
# merge 에서 그 선언을 검증한다. 여기서 추론하면 검증할 대상이 사라진다.
BRANCH_CONTRACTS: Tuple[Dict[str, Any], ...] = (
    {"pattern": "feature/<feature-id>-<slug>", "requiredBase": ["dev"], "allowedTargets": ["dev"],
     "updateStrategy": "none", "mergeStrategy": "merge_commit", "purpose": "shared feature integration"},
    {"pattern": "task/<ticket-id>-<slug>", "requiredBase": ["feature/*", "dev", "release/*"],
     "allowedTargets": ["feature/*", "dev", "release/*"],
     "updateStrategy": "rebase_before_review", "mergeStrategy": "merge_commit", "purpose": "atomic implementation"},
    {"pattern": "fix/<ticket-id>-<slug>", "requiredBase": ["dev", "release/*"],
     "allowedTargets": ["dev", "release/*"],
     "updateStrategy": "rebase_before_review", "mergeStrategy": "merge_commit", "purpose": "ordinary defect fix"},
    {"pattern": "release/<semver>", "requiredBase": ["dev"], "allowedTargets": ["main", "dev"],
     "updateStrategy": "none", "mergeStrategy": "merge_commit", "purpose": "stabilisation and release"},
    {"pattern": "hotfix/<ticket-id>-<slug>", "requiredBase": ["main"],
     "allowedTargets": ["main", "dev", "release/*"],
     "updateStrategy": "none", "mergeStrategy": "merge_commit", "purpose": "urgent production fix"},
)

# §7 Phase F. 되돌리기 쉬운 Private setup 은 Hermes 권한이고, 아래는 아니다.
OWNER_GATES = {
    "public-exposure": "a repository would be created public, or an existing one made public",
    "paid-plan-change": "the plan changes a paid plan or incurs a recurring cost",
    "destructive-replacement": "an existing resource would be replaced rather than created",
    "irreversible-naming": "a package or public name would be published and cannot be reclaimed",
}


class PlanError(ValueError):
    """요청이 Plan 이 될 수 없다. 무엇이 왜인지 함께 보고한다."""


def load_profile(name: str) -> Dict[str, Any]:
    path = PROFILES / f"{name.lower()}.json"
    if not path.is_file():
        raise PlanError(f"unknown bootstrap profile: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def selected_artifacts(profile: Dict[str, Any], requested_optional: List[str]) -> List[str]:
    """Profile 이 요구하는 것 + 요청된 선택 항목 중 그 Profile 이 허용하는 것.

    허용하지 않는 선택 항목은 조용히 버리지 않고 거부한다. §6.1 이 금지하는 것은
    형식 충족용 문서 생성이고, 그 반대편에는 요청됐는데 사라진 산출물이 있다."""
    allowed = set(profile["optional"])
    unknown = [item for item in requested_optional if item not in allowed]
    if unknown:
        raise PlanError(
            f"{profile['bootstrapProfile']} does not offer these optional artifacts: {sorted(unknown)}"
        )
    # 순서는 안정적이어야 한다 — 이 목록이 digest 에 들어간다.
    return list(profile["required"]) + sorted(set(requested_optional))


def classify_human_gate(request: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    if request.get("visibility") == "public":
        reasons.append("public-exposure")
    for fact in request.get("humanGateFacts", []):
        for gate in OWNER_GATES:
            if gate in fact:
                reasons.append(gate)
    reasons = sorted(set(reasons))
    return {
        "authorization": "OWNER" if reasons else "HERMES",
        "reasons": [{"gate": r, "why": OWNER_GATES[r]} for r in reasons],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def remote_owner(request: Dict[str, Any]) -> str:
    """요청이 정하고, 없으면 배포 기본값. 어느 쪽인지가 Plan 에 남는다.

    `origin` 은 요청이 **어디서 왔는지**고 소유자는 **어디로 가는지**다. 한때 두 자리를 다
    읽었는데, 출처 객체에서 대상을 읽는 것은 이름이 적힌 자리와 강제되는 자리를 뒤섞는 그
    모양이라 한 자리로 줄였다."""
    owner = request.get("remoteOwner")
    return str(owner) if owner else DEFAULT_REMOTE_OWNER


def project_manifest(
    request: Dict[str, Any],
    verification_commands: List[Dict[str, Any]],
    *,
    stack: str = None,
    commitlore_mode: str = None,
) -> Dict[str, Any]:
    """§10.3 의 모양. 절대경로·세션·채널·비밀이 들어갈 자리가 없다 (§10.2)."""
    return {
        "schema": "agent-control-plane.project.v2",
        "projectId": request["repositories"][0]["name"],
        "repositories": [
            {"role": repo["role"], "remote": f"github:{remote_owner(request)}/{repo['name']}",
             "manifestRoot": "."}
            for repo in request["repositories"]
        ],
        "branchProfile": {
            "longLived": ["main", "dev"],
            "defaultBranch": "dev",
            "updateStrategy": "rebase_before_review",
            "mergeStrategy": "merge_commit",
            "releaseTagPolicy": "semver",
        },
        "verificationProfiles": {
            "simple": [c["id"] for c in verification_commands if c.get("tier") in (None, "simple")],
            "standard": [c["id"] for c in verification_commands if c.get("tier") in (None, "simple", "standard")],
            "guarded": [c["id"] for c in verification_commands],
        },
        "verificationCommands": [
            {k: v for k, v in c.items() if k != "tier"} for c in verification_commands
        ],
        # 워크플로를 선언하지 않으면 제어평면이 그 저장소의 project-ci 를 귀속시키지 못하고,
        # 체크는 `unapproved` 로 읽혀 post-merge 검증이 통과하지 않는다.
        #
        # digest 는 여기서 채울 수 없다 — 승인은 활성화 시점의 제어평면 몫이다. 그래서
        # `approvedDigest: null` 에 `unapprovedFirstActivation: true` 를 함께 낸다. 스키마가
        # 이 둘을 한 상태씩만 표현하게 강제하므로 null 단독은 파싱되지도 않는다: 예전에는
        # null 하나가 "아직 승인 안 됨" 과 "아무거나 좋다" 를 동시에 뜻해서, 활성화는
        # 통과하는데 머지는 영영 못 하는 manifest 가 나왔다.
        "ciWorkflows": (
            [
                {
                    "path": CI_PATH,
                    "checkName": "project-ci",
                    "repositoryRole": "primary",
                    "approvedDigest": None,
                    "unapprovedFirstActivation": True,
                }
            ]
            if stack is not None
            else []
        ),
        # §18 의 프로파일 기본값. `onFailure`(WARN/REVISE/BLOCK)는 여기 자리가 없다 —
        # 그것은 부트스트랩이 실패를 어떻게 다루는지에 대한 공장의 규칙이고, 생성된
        # 저장소가 지고 다닐 계약이 아니다.
        "commitlore": {"mode": commitlore_mode or "preferred"},
    }


def compile_plan(
    request: Dict[str, Any],
    verification_commands: List[Dict[str, Any]],
    *,
    requested_optional: List[str] = None,
    operation_id: str,
    stack: str = None,
    ci_values: Dict[str, str] = None,
    environment: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """`operation_id` 는 호출자가 대야 한다.

    §16.3 의 재개는 Operation 정체성에 걸려 있다. 여기서 매번 새 uuid 를 만들면 같은
    의도로 다시 부른 재시도가 **다른 Operation** 이 되고, 원장은 그것을 옳게 못 알아본다 —
    기본값이 그 실수를 쉽게 만든다."""
    if not operation_id or not operation_id.strip():
        raise PlanError("bootstrapOperationId must be supplied; a fresh one per call turns a retry into a new operation")

    validate_request(request)
    profile = load_profile(request["bootstrapProfile"])
    artifacts = selected_artifacts(profile, requested_optional or [])
    gate = classify_human_gate(request)

    # 요청이 스택을 말했으면 그것이 정본이다. 인자와 어긋난 채로 진행하면 요청서에 적힌
    # 값이 아무 효과도 없이 남고, 두 곳이 다른 말을 하는데 아무도 안 막는다.
    declared = {repo.get("stack") for repo in request["repositories"] if repo.get("stack")}
    if len(declared) > 1:
        raise PlanError(f"repositories declare more than one stack: {sorted(declared)}")
    if declared:
        requested = declared.pop()
        if stack is not None and stack != requested:
            raise PlanError(f"request declares stack {requested!r} but {stack!r} was supplied")
        stack = requested
    owner = remote_owner(request)
    manifest = project_manifest(request, verification_commands, stack=stack,
                                commitlore_mode=profile["commitlore"]["default"])
    manifest_digest = digest(manifest)
    verification_digest = digest(verification_commands)

    # 렌더링은 Plan 시점이다. Apply 가 렌더하면 Plan 의 contentDigest 는 아직 존재하지
    # 않는 바이트를 가리키고, 승인은 무엇을 승인했는지 말할 수 없게 된다.
    files = materialize(manifest, seed=request["seed"], stack=stack,
                        ci_values=ci_values, artifacts=artifacts, remote_owner=owner)
    gaps: List[str] = []

    # 프로파일이 요구한 산출물이 실제로 만들어졌는지 본다. 이 검사가 없으면 `required`
    # 목록은 이름의 나열이고, 요구한 것이 없는 채로 Plan 이 완성된 것처럼 보인다.
    # 계획한 보안 통제가 `security-command` 를 충족시킨다. Plan 이 계획하지 않으면 충족되지
    # 않는다 — 이 목록을 상수로 두면 검사가 자기 대상을 안 쥐게 된다.
    security_controls = [f"enable-secret-scanning:{r['name']}" for r in request["repositories"]]
    _, uncovered = artifact_coverage(artifacts, files, manifest, security_controls)
    if uncovered:
        gaps.append(f"selected artifacts were not produced: {sorted(uncovered)}")
    if stack is None:
        # §14.1 — 모르는 스택을 Node 로 조용히 대체하지 않는다. 못 만든 것은 못 만들었다고 적는다.
        gaps.append("stack-specific CI was not rendered: no stack was resolved for this request")
    elif stack not in SKELETONS:
        # 워크플로는 있는데 실행할 프로젝트가 없다. 첫 CI 가 빨간 것은 프로젝트의 문제가
        # 아니라 공장이 절반만 만들었다는 뜻이므로, 그렇게 적는다.
        gaps.append(f"no project skeleton exists for stack {stack!r}; CI will run against an empty tree")

    core = {
        "schema": "repo-factory.bootstrap-plan.v2",
        "bootstrapOperationId": operation_id or str(uuid.uuid4()),
        # §8.3 은 "Timestamp 만 변경 → PlanCore Digest 불변" 을 요구한다. 요청은
        # `origin.requestedAt` 을 정당하게 갖고 있으므로, 그 의도만 digest 한다.
        "requestDigest": digest(request, volatile="strip"),
        "bootstrapProfile": request["bootstrapProfile"],
        "authorization": gate["authorization"],
        "repositories": [
            {"role": r["role"], "identity": f"github:{owner}/{r['name']}",
             "visibility": request["visibility"]}
            for r in request["repositories"]
        ],
        "files": [
            {"repositoryRole": "primary", "path": path,
             "contentDigest": content_digest(files[path]), "mode": "100644"}
            for path in sorted(files)
        ],
        "githubOperations": [
            {"operationId": f"create-repository:{r['name']}", "resourceType": "repository",
             "intent": "create", "resourceIdentity": f"github:{owner}/{r['name']}",
             "phase": "before-files",
             # 관측 어휘로 적는다. 이 값은 사람이 읽으라고 있는 게 아니라 쓰기 뒤의 재조회와
             # 대조하라고 있는 것이고, 대조하는 쪽 어휘로 적혀 있지 않으면 그 사이에 번역이
             # 하나 끼어든다 — 번역은 두 값이 다른데 같아 보이게 만들 수 있는 자리다.
             "desiredState": {"private": request["visibility"] != "public"}}
            for r in request["repositories"]
        ] + [
            {"operationId": f"enable-secret-scanning:{r['name']}", "resourceType": "setting",
             "intent": "update", "resourceIdentity": f"github:{owner}/{r['name']}#secret-scanning",
             # 파일보다 앞이다. push protection 이 genesis push 뒤에 서면 그 푸시는 보호
             # 밖에서 지나간다.
             "phase": "before-files",
             "desiredState": security_desired_state()}
            for r in request["repositories"]
        ] + [
            # §9.4 의 저장소 쪽 방어선. 제어평면이 최종 권위지만, 네이티브 규칙이 없으면
            # 그 권위를 우회하는 직접 푸시를 저장소가 스스로 막지 못한다.
            #
            # 파일 뒤에 만든다. project-ci 를 요구하는 ruleset 이 그 워크플로를 실어 나르는
            # 커밋보다 먼저 존재하면, 저장소에 내용을 넣는 바로 그 푸시를 거부한다.
            # 기본 브랜치도 외부 쓰기다. push 순서로만 다루면 원격이 실제로 무엇인지 아무도
            # 다시 읽지 않고, Result 는 호출자가 준 값을 그대로 싣는다 — 원격이 `main` 인데
            # `dev` 라고 주장하는 Result 가 그렇게 나온다.
            #
            # 파일 뒤다. `dev` 가 원격에 없으면 바꿀 대상이 없다.
            {"operationId": f"set-default-branch:{r['name']}", "resourceType": "setting",
             "intent": "update", "resourceIdentity": f"github:{owner}/{r['name']}#default-branch",
             "phase": "after-files", "desiredState": {"defaultBranch": DEFAULT_BRANCH}}
            for r in request["repositories"]
        ] + [
            {"operationId": f"create-ruleset:{r['name']}", "resourceType": "ruleset",
             "intent": "create", "resourceIdentity": f"github:{owner}/{r['name']}#{RULESET_NAME}",
             "phase": "after-files",
             "desiredState": ruleset_desired_state()}
            for r in request["repositories"]
        ],
        "branchContracts": [dict(c) for c in BRANCH_CONTRACTS],
        "verificationContractDigest": verification_digest,
        "projectManifestDigest": manifest_digest,
    }
    if environment is not None:
        # 참조만 한다. 관측 바이트는 Plan 에 들어가지 않고, id 는 사실만 세므로 같은
        # 사실을 다시 관측해도 Plan digest 는 움직이지 않는다.
        core["environmentSnapshotId"] = environment_snapshot_id(environment)
    # 우리가 싣고 다니는 스키마로 우리 산출물을 검사한다. 없으면 잘못된 Plan 이 apply 까지
    # 가서 거기서 죽고, 스키마를 가진 경계는 그냥 지나친다.
    invalid = sorted(Draft202012Validator(_plan_schema()).iter_errors(core), key=str)
    if invalid:
        raise PlanError(
            "the compiled plan does not satisfy schemas/bootstrap-plan.schema.json: "
            + "; ".join(f"{'.'.join(str(x) for x in e.path)}: {e.message}" for e in invalid[:5])
        )

    return {"planCore": core, "artifacts": artifacts, "humanGate": gate,
            "projectManifest": manifest, "files": files, "unresolvedGaps": gaps}


def diff_summary(compiled: Dict[str, Any]) -> Dict[str, Any]:
    core = compiled["planCore"]
    return {
        "planDigest": digest(core),
        "authorization": core["authorization"],
        "repositories": [r["identity"] for r in core["repositories"]],
        "artifacts": compiled["artifacts"],
        "githubOperations": [o["operationId"] for o in core["githubOperations"]],
        "ownerGates": [r["gate"] for r in compiled["humanGate"]["reasons"]],
    }


def main(argv: List[str] = None) -> int:
    """공개 CLI 는 라이브러리가 만들 수 있는 Plan 을 만들 수 있어야 한다.

    한동안 그러지 못했다 — `--ci-values` 가 없어서 스택을 말한 요청은 CI 를 렌더할 값을
    받을 길이 없었고, 그 결과 CLI 로는 gap 없는 Plan 이 나오지 않았다. 성공 경로가
    파이썬 직접 호출뿐이면 공개된 것은 제품이 아니라 그 제품의 일부다."""
    parser = argparse.ArgumentParser(description="Compile an approved BootstrapRequest into a deterministic plan.")
    parser.add_argument("--request", required=True, type=Path, help="BootstrapRequest JSON")
    parser.add_argument("--verification", required=True, type=Path, help="VerificationCommand list JSON")
    parser.add_argument("--optional", nargs="*", default=[], help="optional artifacts to include")
    parser.add_argument("--operation-id", required=True,
                        help="the bootstrap operation id; a retry must reuse it (PRD §16.3)")
    parser.add_argument("--stack", default=None,
                        help="toolchain when the request does not name one; a conflict with the request is refused")
    parser.add_argument("--ci-values", type=Path, default=None,
                        help="JSON object of CI template values; without it a stack-bearing request leaves a gap")
    parser.add_argument("--environment", type=Path, default=None,
                        help="an EnvironmentObservation to bind; its snapshot id enters the plan, its bytes do not")
    parser.add_argument("--observe", action="store_true",
                        help="observe the environment locally (no remote reads) and bind that observation")
    args = parser.parse_args(argv)

    request = json.loads(args.request.read_text(encoding="utf-8"))
    commands = json.loads(args.verification.read_text(encoding="utf-8"))
    ci_values = json.loads(args.ci_values.read_text(encoding="utf-8")) if args.ci_values else None
    if args.environment and args.observe:
        print(json.dumps({"error": "--environment and --observe both supply the observation; pass one"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        environment = None
        if args.environment:
            environment = json.loads(args.environment.read_text(encoding="utf-8"))
        elif args.observe:
            # 포트 없이 관측한다. 원격 이름은 "확인하지 않았다" 로 남고, 그 사실이 그대로
            # 적힌다 — CLI 가 네트워크를 여는 것은 이 명령의 약속이 아니다.
            environment = observe_environment(request)
        compiled = compile_plan(request, commands, requested_optional=args.optional,
                                operation_id=args.operation_id, stack=args.stack,
                                ci_values=ci_values, environment=environment)
    except (PlanError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    output = {**compiled, "diffSummary": diff_summary(compiled)}
    if environment is not None:
        output["environmentObservation"] = environment
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


ENVIRONMENT_SCHEMA_ID = "repo-factory.environment-observation.v1"


def observe_environment(
    request: Dict[str, Any],
    *,
    port=None,
    runtime_versions: Dict[str, str] = None,
    clock=None,
) -> Dict[str, Any]:
    """§8.2 의 변동 가능한 관측. Plan 과 나란히 서고 Plan 안에 들어가지 않는다.

    Plan 은 "무엇을 만들 것인가" 이고 이것은 "지금 무엇이 참인가" 다. 둘을 한 문서에 두면
    같은 의도를 두 번 컴파일한 결과가 서로 다른 digest 를 갖고, 승인이 승인한 것을 다시
    가리키지 못한다.

    `port` 가 있으면 원격 이름 가용성을 실제로 읽는다. 없으면 못 봤다고 적는다 —
    `None` 은 "비어 있다" 가 아니라 "확인하지 않았다" 이고, 그 둘을 같은 값으로 적으면
    관측되지 않은 것이 관측된 것처럼 읽힌다.
    """
    owner = remote_owner(request)
    repositories = []
    for repo in request["repositories"]:
        identity = f"github:{owner}/{repo['name']}"
        available = None
        if port is not None:
            try:
                available = port.observe("repository", identity) is None
            except Exception as error:  # noqa: BLE001 - the reason is recorded, not swallowed
                repositories.append({"identity": identity, "remoteNameAvailable": None,
                                     "notObserved": str(error)[:160]})
                continue
        repositories.append({"identity": identity, "remoteNameAvailable": available})

    return {
        "schema": ENVIRONMENT_SCHEMA_ID,
        "observedAt": (clock or _utc_now)(),
        "remoteOwner": owner,
        "repositories": repositories,
        "runtime": dict(runtime_versions or {}),
    }


def environment_snapshot_id(observation: Dict[str, Any]) -> str:
    """관측의 **사실**만 센다.

    `observedAt` 을 포함하면 같은 사실을 두 번 관측한 것이 서로 다른 id 가 되고, Plan 이
    그 id 를 참조하므로 Plan digest 까지 흔들린다 — §8.3 이 금지하는 바로 그것이다.
    """
    return digest(observation, volatile="strip")


if __name__ == "__main__":
    raise SystemExit(main())
