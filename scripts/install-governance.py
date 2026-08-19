#!/usr/bin/env python3
"""governance kit installer — Phase 4 에서 생성 저장소에 운영 커널을 이식한다.

repo-factory 의 templates/kit + templates/governance 를 대상 저장소로
materialize 한다. 표준 라이브러리만 사용한다.

사용법:
  python3 scripts/install-governance.py --config repo-factory.json --path /target/repo --dry-run
  python3 scripts/install-governance.py --config repo-factory.json --path /target/repo
  python3 scripts/install-governance.py --config repo-factory.json --path /target/repo --update

config (repo-factory.json):
{
  "repository": {"name": "owner/repo", "owner": "owner", "visibility": "private"},
  "tier": "M",
  "runtime": {"kind": "node", "lower": "20", "latest": "22"},
  "project_commands": {"full": "npm test", "build": "npm run build",
                        "focused": null, "lint": null, "typecheck": null},
  "agent_runtime": {"controller": "ctrl-a", "worker": "worker-a",
                     "reviewers": ["rev-a", "rev-b"], "merge_broker": "broker-a"}
}

규칙:
- idempotent: 같은 입력의 재실행은 변경 0.
- 기존 파일과 내용이 다르면 --update 없이는 fail-closed (덮어쓰기 diff 보고).
- symlink/path escape 차단. dry-run 은 어떤 쓰기도 하지 않는다 (GitHub 쓰기 0).
- 산출: governance kit + factory-lock + external-write-plan + install manifest(stdout).
종료 코드: 0 성공 / 1 충돌(--update 필요) / 2 사용법·입력 오류
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

FACTORY_VERSION = "3.1.0"
GOVERNANCE_SCHEMA_VERSION = 1
# 설치 시점에 검증된 action pin 정본 (tag → full commit SHA, gh api 로 실검증됨 2026-08-08)
KNOWN_ACTIONS = [
    {"uses": "actions/checkout", "commit": "b4ffde65f46336ab88eb53be808477a3936bae11",
     "resolved_from": "v4.1.1", "source_repo": "actions/checkout"},
    {"uses": "actions/upload-artifact", "commit": "5d5d22a31266ced268874388b861e4b58bb5c2f3",
     "resolved_from": "v4.3.1", "source_repo": "actions/upload-artifact"},
]
DEPENDABOT_ECOSYSTEMS = {"python": "pip", "node": "npm", "rust": "cargo", "go": "gomod"}
SKILL_ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = SKILL_ROOT / "templates" / "kit"
SCHEMA_DIR = SKILL_ROOT / "templates" / "governance"
GITIGNORE_LINES = (".autopilot/", ".governance-broker/", "*.pyc", "__pycache__/")


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def load_config(path: Path) -> dict:
    if not path.is_file():
        die(f"config 없음: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        die(f"config JSON 파싱 실패 — {error}")
    problems = []
    repo = (config.get("repository") or {})
    if "/" not in str(repo.get("name", "")):
        problems.append("repository.name 은 owner/repo")
    if not repo.get("owner"):
        problems.append("repository.owner 필수")
    if repo.get("visibility") not in ("private", "public"):
        problems.append("repository.visibility ∈ {private, public}")
    if config.get("tier") not in ("S", "M", "L"):
        problems.append("tier ∈ {S, M, L}")
    commands = config.get("project_commands") or {}
    for key in ("full", "build"):
        if not commands.get(key):
            problems.append(f"project_commands.{key} 필수")
    runtime = config.get("agent_runtime") or {}
    for key in ("controller", "worker", "merge_broker"):
        if not runtime.get(key):
            problems.append(f"agent_runtime.{key} 필수 (없으면 autonomous-ready 금지)")
    if not runtime.get("reviewers"):
        problems.append("agent_runtime.reviewers 는 1개 이상")
    if problems:
        print(f"ERROR: config 스키마 위반 {len(problems)}건", file=sys.stderr)
        for p in problems:
            print(f"  → {p}", file=sys.stderr)
        sys.exit(2)
    return config


def build_policy(config: dict) -> dict:
    template = json.loads((SCHEMA_DIR / "policy.v1.template.json").read_text(encoding="utf-8"))
    repo = config["repository"]
    template["factory_version"] = f"repo-factory@{FACTORY_VERSION}"
    template["repository"] = {"name": repo["name"], "owner": repo["owner"],
                              "visibility": repo["visibility"]}
    template["tier"] = config["tier"]
    commands = config.get("project_commands") or {}
    template["project_commands"] = {
        "focused": commands.get("focused"),
        "full": commands["full"],
        "build": commands["build"],
        "lint": commands.get("lint"),
        "typecheck": commands.get("typecheck"),
    }
    runtime = config["agent_runtime"]
    template["agent_runtime"].update({
        "controller": runtime["controller"],
        "worker": runtime["worker"],
        "reviewers": list(runtime["reviewers"]),
        "merge_broker": runtime["merge_broker"],
        "additional_registered_agents": list(runtime.get("additional_registered_agents") or []),
    })
    if isinstance(config.get("commitlore"), dict):
        template["commitlore"]["required_at_genesis"] = bool(
            config["commitlore"].get("required_at_genesis", True))
    security = config.get("security_commands") or {}
    template["security_commands"] = {
        "sast": security.get("sast"),
        "dependency_audit": security.get("dependency_audit"),
        "secret_scan": security.get("secret_scan"),
    }
    if isinstance(config.get("autonomy"), dict) and \
            "auto_revert_out_of_band" in config["autonomy"]:
        template["autonomy"]["auto_revert_out_of_band"] = bool(
            config["autonomy"]["auto_revert_out_of_band"])
    return template


def build_profile_lock(config: dict, resolved_at: str) -> dict:
    """Genesis 시점 확정 profile lock. 동적 현재 상태가 아니다 — 이후 드리프트는
    scripts/github-profile.py verify 가 잡는다."""
    github = config.get("github") or {}
    owner_type = github.get("owner_type", "User")
    visibility = config["repository"]["visibility"]
    private = visibility == "private"
    if private:
        profile = "FREE_PRIVATE_COMPENSATING"
    elif owner_type == "Organization":
        profile = "FREE_PUBLIC_ORG_NATIVE_QUEUE"
    else:
        profile = "FREE_PUBLIC_USER_NATIVE"
    return {
        "schema": "repo-governance.github-profile.lock.v1",
        "plan": github.get("plan", "free"),
        "plan_verified": bool(github.get("plan_verified", False)),
        "owner_type": owner_type,
        "repository": config["repository"]["name"],
        "visibility": visibility,
        "profile": profile,
        "native_branch_enforcement": not private,
        "native_auto_merge": not private,
        "merge_queue": profile == "FREE_PUBLIC_ORG_NATIVE_QUEUE",
        "assurance_limit": "COMPENSATING_CONTROLS_ONLY" if private else None,
        "resolved_at": resolved_at,
    }


def substitute(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def adapter_stub(adapter_id: str, roles: list[str]) -> dict:
    return {
        "schema": "repo-governance.agent-adapter.v1",
        "id": adapter_id,
        "roles": roles,
        "provider": None,
        "model": None,
        "invoke": {"execute": None, "review": None, "repair": None, "cancel": None, "health": None},
        "isolation": {"read_only": "reviewer" in roles,
                      "no_worker_transcript": True,
                      "no_chain_of_thought_storage": True},
    }


def external_write_plan(config: dict) -> dict:
    repo = config["repository"]["name"]
    return {
        "schema": "repo-governance.external-write-plan.v1",
        "repository": repo,
        "writes": [
            {"op": "repo_create", "target": repo, "visibility": config["repository"]["visibility"]},
            {"op": "branch_create", "target": "dev (from main)"},
            {"op": "default_branch", "target": "dev"},
            {"op": "branch_protection", "target": "dev",
             "required_checks": ["governance", "project-ci", "agent-review"],
             "approving_review_count": 0, "enforce_admins": True,
             "allow_force_pushes": False, "allow_deletions": False,
             "merge_methods": ["merge_commit"], "bypass_actors": []},
            {"op": "branch_protection", "target": "main",
             "required_checks": ["governance", "project-ci"],
             "approving_review_count": 0, "enforce_admins": True,
             "allow_force_pushes": False, "allow_deletions": False,
             "merge_methods": ["merge_commit"], "bypass_actors": []},
            {"op": "issue_sync", "target": "docs/tickets/**/*.md → issues (marker 기반, idempotent)"},
        ],
        "note": "이 계획의 exact scope 를 Genesis Bundle 승인이 커버한다. dry-run 은 이 중 무엇도 실행하지 않는다.",
    }


def safe_target(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve()) + "/") and target != root.resolve():
        die(f"path escape 차단: {rel}")
    for parent in [target] + list(target.parents):
        if parent == root.resolve():
            break
        if parent.is_symlink():
            die(f"symlink 경유 쓰기 차단: {parent}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true",
                        help="기존 파일과 내용이 다를 때 덮어쓰기 허용")
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    if not KIT_DIR.is_dir() or not SCHEMA_DIR.is_dir():
        die(f"kit 템플릿을 찾을 수 없다: {KIT_DIR}")
    root = args.path.expanduser().resolve()
    if not root.is_dir():
        die(f"--path 디렉터리 없음: {root}")
    config = load_config(args.config)

    runtime_cfg = config.get("runtime") or {}
    mapping = {
        "FACTORY_VERSION": FACTORY_VERSION,
        "REPO_FULL": config["repository"]["name"],
        "OWNER": config["repository"]["owner"],
        "VISIBILITY": config["repository"]["visibility"],
        "TIER": config["tier"],
        "FULL_CMD": config["project_commands"]["full"],
        "BUILD_CMD": config["project_commands"]["build"],
        "RUNTIME_KIND": str(runtime_cfg.get("kind", "node")),
        "RUNTIME_LOWER": str(runtime_cfg.get("lower", "20")),
        "RUNTIME_LATEST": str(runtime_cfg.get("latest", "22")),
        "DEPENDABOT_ECOSYSTEM": DEPENDABOT_ECOSYSTEMS.get(
            str(runtime_cfg.get("kind", "node")), "npm"),
    }

    # 산출 파일 집합을 전부 메모리에서 먼저 만든다 — 계획과 쓰기를 분리.
    outputs: dict[str, str] = {}
    for path in sorted(KIT_DIR.rglob("*")):
        if not path.is_file():
            continue
        # 킷 안의 스크립트를 한 번이라도 임포트하면 그 옆에 __pycache__/*.pyc 가 생긴다.
        # 그건 프로젝트 산출물이 아니라 실행 흔적인데, rglob 은 그것도 집어서 UTF-8 로
        # 읽으려 하고 첫 바이트에서 죽는다. GITIGNORE_LINES 가 이미 "이건 내용이 아니다"
        # 라고 알고 있었는데 walk 만 그 지식을 안 쓰고 있었다.
        if any(part == "__pycache__" for part in path.parts) or path.suffix == ".pyc":
            continue
        rel = path.relative_to(KIT_DIR).as_posix()
        outputs[rel] = substitute(path.read_text(encoding="utf-8"), mapping)
    policy = build_policy(config)
    outputs["governance/policy.v1.json"] = json.dumps(policy, ensure_ascii=False, indent=2) + "\n"
    for schema_file in sorted(SCHEMA_DIR.glob("*.schema*.json")):
        outputs[f"governance/schemas/{schema_file.name}"] = schema_file.read_text(encoding="utf-8")
    # GitHub Free 기능 매트릭스 정본 — github-profile.py 가 가리키는 파일. 생성 repo 에
    # 함께 실어 self-contained 하게 한다(dangling reference 방지).
    outputs["governance/schemas/github-free-capabilities.v1.json"] = \
        (SCHEMA_DIR / "github-free-capabilities.v1.json").read_text(encoding="utf-8")
    outputs["governance/external-write-plan.json"] = json.dumps(
        external_write_plan(config), ensure_ascii=False, indent=2) + "\n"
    import datetime as _dt
    resolved_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    profile_lock = build_profile_lock(config, resolved_at)
    profile_target = root / "governance" / "github-profile.lock.json"
    if profile_target.is_file():
        # profile lock 은 Genesis 확정값 — 재실행이 resolved_at 만 바꿔 diff 를 내지 않게 보존
        outputs["governance/github-profile.lock.json"] = profile_target.read_text(encoding="utf-8")
    else:
        outputs["governance/github-profile.lock.json"] = json.dumps(
            profile_lock, ensure_ascii=False, indent=2) + "\n"
    actions_target = root / "governance" / "actions-lock.v1.json"
    if actions_target.is_file():
        outputs["governance/actions-lock.v1.json"] = actions_target.read_text(encoding="utf-8")
    else:
        outputs["governance/actions-lock.v1.json"] = json.dumps({
            "schema": "repo-governance.actions-lock.v1",
            "actions": [dict(entry, resolved_at=resolved_at) for entry in KNOWN_ACTIONS],
        }, ensure_ascii=False, indent=2) + "\n"

    runtime = config["agent_runtime"]
    role_map = [(runtime["controller"], ["controller"]), (runtime["worker"], ["worker"]),
                (runtime["merge_broker"], ["merge_broker"])]
    role_map += [(r, ["reviewer"]) for r in runtime["reviewers"]]
    for agent_id in runtime.get("additional_registered_agents") or []:
        role_map.append((agent_id, ["specialist"]))
    merged_roles: dict[str, list[str]] = {}
    for agent_id, roles in role_map:
        merged_roles.setdefault(agent_id, [])
        merged_roles[agent_id] = sorted(set(merged_roles[agent_id]) | set(roles))
    for agent_id, roles in sorted(merged_roles.items()):
        outputs[f"governance/adapters/{agent_id}.json"] = json.dumps(
            adapter_stub(agent_id, roles), ensure_ascii=False, indent=2) + "\n"

    template_digest = "sha256:" + hashlib.sha256(
        b"".join(outputs[k].encode() for k in sorted(outputs))).hexdigest()
    outputs["governance/factory-lock.json"] = json.dumps({
        "factory": "repo-factory",
        "version": FACTORY_VERSION,
        "governance_schema": GOVERNANCE_SCHEMA_VERSION,
        "template_digest": template_digest,
    }, ensure_ascii=False, indent=2) + "\n"

    plan = {"create": [], "skip": [], "update": [], "conflict": []}
    for rel in sorted(outputs):
        target = safe_target(root, rel)
        if not target.exists():
            plan["create"].append(rel)
        elif target.read_text(encoding="utf-8") == outputs[rel]:
            plan["skip"].append(rel)
        elif args.update:
            plan["update"].append(rel)
        else:
            plan["conflict"].append(rel)

    gitignore = root / ".gitignore"
    existing_ignore = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    ignore_missing = [line for line in GITIGNORE_LINES if line not in existing_ignore.splitlines()]

    manifest = {
        "factory_version": FACTORY_VERSION,
        "template_digest": template_digest,
        "dry_run": bool(args.dry_run),
        "plan": plan,
        "gitignore_appends": ignore_missing,
    }
    if plan["conflict"]:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"ERROR: 기존 파일 {len(plan['conflict'])}건과 내용 충돌 — 검토 후 --update 로 재실행",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    for rel in plan["create"] + plan["update"]:
        target = safe_target(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(outputs[rel], encoding="utf-8")
    if ignore_missing:
        with open(gitignore, "a", encoding="utf-8") as f:
            if existing_ignore and not existing_ignore.endswith("\n"):
                f.write("\n")
            f.write("\n".join(ignore_missing) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
