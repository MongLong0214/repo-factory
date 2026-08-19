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

_PLAN_SCHEMA_CACHE: Dict[str, Any] = {}


def _plan_schema() -> Dict[str, Any]:
    """우리가 싣고 다니는 스키마. 한 번 읽고 재사용한다."""
    if not _PLAN_SCHEMA_CACHE:
        _PLAN_SCHEMA_CACHE.update(
            json.loads((SCHEMAS / "bootstrap-plan.schema.json").read_text(encoding="utf-8"))
        )
    return _PLAN_SCHEMA_CACHE


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


def remote_owner(request: Dict[str, Any]) -> str:
    """요청이 정하고, 없으면 배포 기본값. 어느 쪽인지가 Plan 에 남는다."""
    owner = (request.get("origin") or {}).get("remoteOwner") or request.get("remoteOwner")
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
) -> Dict[str, Any]:
    """`operation_id` 는 호출자가 대야 한다.

    §16.3 의 재개는 Operation 정체성에 걸려 있다. 여기서 매번 새 uuid 를 만들면 같은
    의도로 다시 부른 재시도가 **다른 Operation** 이 되고, 원장은 그것을 옳게 못 알아본다 —
    기본값이 그 실수를 쉽게 만든다."""
    if not operation_id or not operation_id.strip():
        raise PlanError("bootstrapOperationId must be supplied; a fresh one per call turns a retry into a new operation")

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
    _, uncovered = artifact_coverage(artifacts, files, manifest)
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
             "intent": "create", "resourceIdentity": f"github:{owner}/{r['name']}"}
            for r in request["repositories"]
        ],
        "branchContracts": [dict(c) for c in BRANCH_CONTRACTS],
        "verificationContractDigest": verification_digest,
        "projectManifestDigest": manifest_digest,
    }
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
    parser = argparse.ArgumentParser(description="Compile an approved BootstrapRequest into a deterministic plan.")
    parser.add_argument("--request", required=True, type=Path, help="BootstrapRequest JSON")
    parser.add_argument("--verification", required=True, type=Path, help="VerificationCommand list JSON")
    parser.add_argument("--optional", nargs="*", default=[], help="optional artifacts to include")
    parser.add_argument("--operation-id", required=True,
                        help="the bootstrap operation id; a retry must reuse it (PRD §16.3)")
    args = parser.parse_args(argv)

    request = json.loads(args.request.read_text(encoding="utf-8"))
    commands = json.loads(args.verification.read_text(encoding="utf-8"))
    try:
        compiled = compile_plan(request, commands, requested_optional=args.optional,
                                operation_id=args.operation_id)
    except (PlanError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({**compiled, "diffSummary": diff_summary(compiled)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
