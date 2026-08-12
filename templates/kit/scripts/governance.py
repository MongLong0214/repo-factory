#!/usr/bin/env python3
"""repo-governance kernel — Technical Truth Authority 계산기.

repo-factory가 생성 저장소에 설치하는 governance kernel이다. Python 표준
라이브러리만 사용한다. 상태를 커밋하지 않고 매 실행마다 현재 사실(Git tree,
policy, ticket metadata, GitHub facts)에서 재계산한다.

명령:
  validate   — policy/ticket/kernel 무결성 전수 검사 (로컬, 네트워크 없음)
  check-pr   — 한 PR의 계약 준수 검사 (diff ownership, 자기확장 금지, 혼합 금지)
  status     — 티켓 상태 계산. --offline 은 external_state=NOT_CHECKED 로 정직하게
  render     — 사람용 projection (readiness 입력 아님)
  manifest   — Genesis manifest (digest 목록) 생성
  doctor     — 자율 운영 가능성 probe (못 본 것은 NOT_CHECKED, 미지원은 UNAVAILABLE)

종료 코드: 0 통과 / 1 검증 실패 또는 fail-closed / 2 사용법·입력 오류
GitHub 접근은 REPO_GOVERNANCE_GH 환경변수의 실행 파일(기본 gh)로만 한다.
PR comment 의 PASS 문자열, agent 이름, 자가 승인 문구는 어떤 판정에도 읽지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCHEMA_POLICY = "repo-governance.policy.v1"
SCHEMA_TICKET = "repo-governance.ticket.v1"
SCHEMA_MANIFEST = "repo-governance.genesis-manifest.v1"
GOVERNANCE_SCHEMA_VERSION = 1

RISKS = ("low", "standard", "high", "critical")
KINDS = ("implementation", "contract-change", "governance-change", "rollback")
EXECUTABLE_KINDS = ("implementation", "rollback")

TICKET_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}-\d{3,4}$")
TICKET_MARKER_RE = re.compile(
    r"<!--\s*repo-governance-ticket:v1\s*(\{.*?\})\s*-->", re.DOTALL
)
TICKET_LINE_RE = re.compile(r"^Ticket:\s*(\S+)\s*$", re.MULTILINE)
OPERATION_MARKER_RE = re.compile(
    r"<!--\s*repo-governance-operation:\s*"
    r"ticket=(?P<ticket>\S+)\s+operation=(?P<operation>\S+)\s+"
    r"base=(?P<base>\S+)\s+policy=(?P<policy>\S+)\s*-->",
    re.DOTALL,
)
BRANCH_RE = re.compile(
    r"^(?P<prefix>feat|fix|contract|governance|revert|hotfix)/"
    r"(?P<id>[A-Z][A-Z0-9]{0,7}-\d{3,4})(?:-[a-z0-9][a-z0-9-]*)?$"
)
KIND_BRANCH_PREFIXES = {
    "implementation": ("feat", "fix", "hotfix"),
    "contract-change": ("contract",),
    "governance-change": ("governance",),
    "rollback": ("revert",),
}
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)", re.MULTILINE)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CURRENT_STATE_NAME_RE = re.compile(r"(ready[-_]?set|current[-_]?state|CURRENT_SHA)", re.IGNORECASE)
SECRET_RES = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
DEPENDENCY_MANIFESTS = (
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "requirements.txt", "poetry.lock", "Pipfile.lock", "Cargo.toml",
    "Cargo.lock", "go.mod", "go.sum", "Gemfile.lock",
)

KERNEL_PREFIXES = ("governance/",)
KERNEL_FILES = frozenset({
    "scripts/governance.py",
    "scripts/autopilot.py",
    "scripts/merge-broker.py",
    "scripts/github-profile.py",
    "scripts/install-governance.py",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
})
# target repo 의 workflow 는 read-only evidence 생산자다(governance/ci/review/security/
# post-merge). 자율 루프(autopilot/merge-broker)는 로컬 controller 에서 운영자 자격증명
# 으로 돈다 — target repo 에 merge 자격증명을 두지 않기 위함(§신뢰 경계).
REQUIRED_WORKFLOWS = (
    ".github/workflows/governance.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/agent-review.yml",
    ".github/workflows/security-gate.yml",
    ".github/workflows/post-merge.yml",
)
PROFILE_NAMES = ("FREE_PUBLIC_USER_NATIVE", "FREE_PUBLIC_ORG_NATIVE_QUEUE",
                 "FREE_PRIVATE_COMPENSATING")
USES_FULL_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s#@]+)@([^\s#]+)", re.MULTILINE)
PR_TRIGGER_RE = re.compile(r"^\s*pull_request:?\s*$", re.MULTILINE)
PR_TARGET_TRIGGER_RE = re.compile(r"^\s*pull_request_target\s*:?\s*$", re.MULTILINE)
WRITE_PERMISSION_RE = re.compile(r"^\s{2,}[\w-]+:\s*write\s*$", re.MULTILINE)


# ---------------------------------------------------------------- primitives

def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_obj(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(obj)).hexdigest()


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_json_file(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_kernel_path(path: str) -> bool:
    if path in KERNEL_FILES or path in REQUIRED_WORKFLOWS:
        return True
    return any(path.startswith(p) for p in KERNEL_PREFIXES)


def valid_repo_path(p) -> bool:
    if not isinstance(p, str) or not p or p != p.strip():
        return False
    if p.startswith("/") or "\\" in p or p.startswith("~"):
        return False
    parts = p.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    # 허용 glob: trailing '/**' 하나만.
    if "*" in p and not p.endswith("/**"):
        return False
    if p.endswith("/**") and "*" in p[:-3]:
        return False
    return True


def path_matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])  # 'src/x/**' -> 'src/x/' prefix
    return pattern == path


def any_path_match(patterns, path: str) -> bool:
    return any(path_matches(p, path) for p in patterns)


class Problems:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, code: str, message: str, where: str | None = None) -> None:
        self.items.append({"code": code, "message": message, "where": where})

    def __bool__(self) -> bool:
        return bool(self.items)

    def render(self, stream=sys.stdout) -> None:
        for item in self.items:
            where = f" [{item['where']}]" if item.get("where") else ""
            print(f"FAIL {item['code']}{where} — {item['message']}", file=stream)


# ------------------------------------------------------------------- policy

POLICY_TOP_KEYS = {
    "schema", "factory_version", "mode", "repository", "tier", "branches",
    "merge", "wip", "autonomy", "checks", "risk_profiles", "project_commands",
    "security_commands", "agent_runtime", "security", "commitlore",
}


def validate_policy(policy, problems: Problems) -> None:
    where = "governance/policy.v1.json"
    if not isinstance(policy, dict):
        problems.add("POLICY_NOT_OBJECT", "policy 최상위가 객체가 아니다", where)
        return
    extra = set(policy) - POLICY_TOP_KEYS
    missing = POLICY_TOP_KEYS - set(policy)
    if extra:
        problems.add("POLICY_UNKNOWN_KEY", f"허용되지 않은 키: {sorted(extra)}", where)
    if missing:
        problems.add("POLICY_MISSING_KEY", f"누락 키: {sorted(missing)}", where)
        return
    if policy["schema"] != SCHEMA_POLICY:
        problems.add("POLICY_SCHEMA", f"schema 는 {SCHEMA_POLICY} 여야 한다", where)
    if not re.fullmatch(r"repo-factory@\d+\.\d+\.\d+", str(policy["factory_version"])):
        problems.add("POLICY_FACTORY_VERSION", "factory_version 형식은 repo-factory@x.y.z", where)
    if policy["mode"] != "single_owner_policy_delegated_autonomy":
        problems.add("POLICY_MODE", "mode 는 single_owner_policy_delegated_autonomy 하나뿐이다", where)
    repo = policy.get("repository") or {}
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(repo.get("name", ""))):
        problems.add("POLICY_REPO_NAME", "repository.name 은 owner/repo 형식", where)
    if repo.get("visibility") not in ("private", "public"):
        problems.add("POLICY_VISIBILITY", "repository.visibility ∈ {private, public}", where)
    if policy.get("tier") not in ("S", "M", "L"):
        problems.add("POLICY_TIER", "tier ∈ {S, M, L}", where)
    branches = policy.get("branches") or {}
    if not branches.get("integration") or not branches.get("production"):
        problems.add("POLICY_BRANCHES", "branches.integration/production 필수", where)

    merge = policy.get("merge") or {}
    if merge.get("method") != "merge_commit":
        problems.add("POLICY_MERGE_METHOD", "merge.method 는 merge_commit 만 허용 (squash/rebase 비활성)", where)
    if merge.get("authorization_model") != "evidence_gated_role_agnostic":
        problems.add("POLICY_AUTHZ_MODEL", "merge.authorization_model 은 evidence_gated_role_agnostic", where)
    if merge.get("requesters") != "any_registered_agent":
        problems.add("POLICY_REQUESTERS", "merge.requesters 는 any_registered_agent (역할 고정 금지)", where)
    if merge.get("requesters") == "any_registered_agent" and merge.get("direct_agent_merge_credentials") is not False:
        problems.add("POLICY_RAW_CREDENTIAL", "requesters=any_registered_agent ⇒ direct_agent_merge_credentials=false 필수", where)
    if merge.get("human_approval_required") is not False:
        problems.add("POLICY_HUMAN_APPROVAL", "routine human approval 은 이 모드에 없다 (human_approval_required=false)", where)
    if merge.get("executor") not in (
        "github_auto_merge_or_neutral_merge_broker", "github_native_auto_merge",
        "github_merge_queue", "neutral_merge_broker",
    ):
        problems.add("POLICY_EXECUTOR", "merge executor 는 native auto-merge/queue 또는 neutral broker 만", where)
    if merge.get("recheck_at_execution") is not True:
        problems.add("POLICY_RECHECK", "merge.recheck_at_execution=true 필수", where)
    if merge.get("single_writer") is not True:
        problems.add("POLICY_SINGLE_WRITER", "merge.single_writer=true 필수", where)
    ttl = merge.get("intent_ttl_seconds")
    if not isinstance(ttl, int) or not (60 <= ttl <= 86400):
        problems.add("POLICY_INTENT_TTL", "intent_ttl_seconds ∈ [60, 86400]", where)

    wip = policy.get("wip") or {}
    for key in ("max_active_tickets", "max_active_prs"):
        val = wip.get(key)
        if not isinstance(val, int) or not (1 <= val <= 32):
            problems.add("POLICY_WIP", f"wip.{key} ∈ [1, 32]", where)
    if wip.get("reject_overlapping_ownership") is not True:
        problems.add("POLICY_WIP_OVERLAP", "wip.reject_overlapping_ownership=true 필수", where)

    autonomy = policy.get("autonomy") or {}
    auto_start = autonomy.get("auto_start") or []
    auto_merge = autonomy.get("auto_merge") or []
    bad = [r for r in list(auto_start) + list(auto_merge) if r not in RISKS]
    if bad:
        problems.add("POLICY_RISK_ENUM", f"알 수 없는 risk: {bad}", where)
    if not set(auto_merge) <= set(auto_start):
        problems.add("POLICY_AUTOMERGE_SUBSET", "autonomy.auto_merge 는 auto_start 의 부분집합이어야 한다", where)
    if autonomy.get("routine_human_required") is not False:
        problems.add("POLICY_ROUTINE_HUMAN", "autonomy.routine_human_required=false 필수", where)
    if autonomy.get("high_auto_merge_requires_predelegation") is not True:
        problems.add("POLICY_HIGH_PREDELEGATION", "high auto-merge 는 predelegation 필수 선언이어야 한다", where)
    critical_default = autonomy.get("critical_default")
    profiles = policy.get("risk_profiles") or {}
    if critical_default not in ("halt", "predelegated"):
        problems.add("POLICY_CRITICAL_DEFAULT", "critical_default ∈ {halt, predelegated}", where)
    elif critical_default == "predelegated":
        crit = profiles.get("critical") or {}
        if crit.get("predelegated") is not True or not crit.get("rollback"):
            problems.add(
                "POLICY_CRITICAL_PROFILE",
                "critical_default=predelegated 는 risk_profiles.critical.predelegated=true + rollback 명시가 필요하다",
                where,
            )
    if "critical" in auto_merge and critical_default != "predelegated":
        problems.add("POLICY_CRITICAL_AUTOMERGE", "critical auto-merge 는 predelegated critical profile 없이는 금지", where)
    if not isinstance(autonomy.get("auto_revert_out_of_band"), bool):
        problems.add("POLICY_OOB_REVERT", "autonomy.auto_revert_out_of_band 는 boolean 필수", where)
    for key, lo, hi in (
        ("max_repair_rounds", 1, 10), ("max_transient_retries", 1, 10),
        ("max_ticket_wall_minutes", 5, 1440),
    ):
        val = autonomy.get(key)
        if not isinstance(val, int) or not (lo <= val <= hi):
            problems.add("POLICY_BUDGET", f"autonomy.{key} ∈ [{lo}, {hi}]", where)

    security_commands = policy.get("security_commands")
    if not isinstance(security_commands, dict) or \
            set(security_commands) != {"sast", "dependency_audit", "secret_scan"} or \
            any(v is not None and (not isinstance(v, str) or not v)
                for v in security_commands.values()):
        problems.add("POLICY_SECURITY_COMMANDS",
                     "security_commands 는 {sast, dependency_audit, secret_scan} (string|null) 정확히", where)

    checks = policy.get("checks") or {}
    names = [checks.get(k) for k in ("governance", "project_ci", "review", "post_merge")]
    if not all(isinstance(n, str) and n for n in names):
        problems.add("POLICY_CHECKS", "checks.governance/project_ci/review/post_merge 필수", where)
    elif len(set(names)) != len(names):
        problems.add("POLICY_CHECK_UNIQUE", "check 이름은 유일해야 한다", where)

    missing_profiles = [r for r in RISKS if r not in profiles]
    if missing_profiles:
        problems.add("POLICY_RISK_PROFILES", f"risk_profiles 누락: {missing_profiles}", where)

    commands = policy.get("project_commands") or {}
    for key in ("full", "build"):
        if not isinstance(commands.get(key), str) or not commands.get(key) or commands[key].startswith("<"):
            problems.add("POLICY_PROJECT_COMMANDS", f"project_commands.{key} 는 실제 명령이어야 한다", where)

    runtime = policy.get("agent_runtime") or {}
    for key in ("controller", "worker", "merge_broker"):
        val = runtime.get(key)
        if not isinstance(val, str) or not val or val.startswith("<"):
            problems.add("POLICY_AGENT_RUNTIME", f"agent_runtime.{key} 는 실제 adapter id 여야 한다 (없으면 autonomous-ready 금지)", where)
    reviewers = runtime.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers or any(
        not isinstance(r, str) or not r or r.startswith("<") for r in reviewers
    ):
        problems.add("POLICY_REVIEWERS", "agent_runtime.reviewers 는 실제 adapter id 1개 이상", where)
    if runtime.get("merge_requesters") != "any_registered_agent":
        problems.add("POLICY_RUNTIME_REQUESTERS", "agent_runtime.merge_requesters 는 any_registered_agent", where)
    for key in ("supports_execute", "supports_review", "supports_repair", "supports_merge_request"):
        if runtime.get(key) is not True:
            problems.add("POLICY_RUNTIME_CAPABILITY", f"agent_runtime.{key}=true 필수 (capability/자율 정책 정합)", where)

    security = policy.get("security") or {}
    if security.get("runtime_identity") != "local_controller":
        problems.add("POLICY_RUNTIME_IDENTITY",
                     "security.runtime_identity 는 local_controller (merge 자격증명은 운영자 "
                     "컨트롤러에만, target repo secret 에 두지 않음)", where)
    if security.get("allow_personal_access_token") is not False:
        problems.add("POLICY_PAT", "security.allow_personal_access_token=false 필수", where)
    if security.get("pin_actions_by_full_sha") is not True:
        problems.add("POLICY_SHA_PIN", "security.pin_actions_by_full_sha=true 필수", where)
    for key in ("worker_can_modify_governance", "worker_can_modify_oracles"):
        if security.get(key) is not False:
            problems.add("POLICY_WORKER_BOUNDARY", f"security.{key}=false 필수", where)

    commitlore = policy.get("commitlore") or {}
    if commitlore.get("operational_authority") is not False:
        problems.add("POLICY_COMMITLORE", "commitlore.operational_authority=false (projection/기록일 뿐)", where)


def registered_agents(policy) -> set[str]:
    runtime = policy.get("agent_runtime") or {}
    agents = {runtime.get("controller"), runtime.get("worker"), runtime.get("merge_broker")}
    agents.update(runtime.get("reviewers") or [])
    agents.update(runtime.get("additional_registered_agents") or [])
    return {a for a in agents if isinstance(a, str) and a}


# ------------------------------------------------------------------ tickets

TICKET_TOP_KEYS = {
    "schema", "id", "title", "kind", "risk", "predelegated", "milestone",
    "dependencies", "adr_refs", "prd_ref", "owned_paths", "coordinated_paths",
    "oracle_paths", "acceptance", "commands", "budgets", "invalidates", "supersedes",
}
TICKET_REQUIRED_KEYS = TICKET_TOP_KEYS - {"milestone"}


def parse_ticket_file(path: Path, problems: Problems):
    where = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        problems.add("TICKET_UNREADABLE", str(error), where)
        return None
    markers = TICKET_MARKER_RE.findall(text)
    if len(markers) != 1:
        problems.add("TICKET_MARKER_COUNT", f"repo-governance-ticket:v1 marker 가 {len(markers)}개 — 한 파일 = 한 티켓", where)
        return None
    try:
        meta = json.loads(markers[0])
    except json.JSONDecodeError as error:
        problems.add("TICKET_JSON", f"metadata JSON 파싱 실패 — {error}", where)
        return None
    return {"path": path, "meta": meta, "body": text}


def validate_ticket_meta(meta, where: str, problems: Problems) -> None:
    if not isinstance(meta, dict):
        problems.add("TICKET_NOT_OBJECT", "metadata 가 객체가 아니다", where)
        return
    extra = set(meta) - TICKET_TOP_KEYS
    missing = TICKET_REQUIRED_KEYS - set(meta)
    if extra:
        problems.add("TICKET_UNKNOWN_KEY", f"허용되지 않은 키: {sorted(extra)}", where)
    if missing:
        problems.add("TICKET_MISSING_KEY", f"누락 키: {sorted(missing)}", where)
        return
    if meta["schema"] != SCHEMA_TICKET:
        problems.add("TICKET_SCHEMA", f"schema 는 {SCHEMA_TICKET}", where)
    if not TICKET_ID_RE.fullmatch(str(meta["id"])):
        problems.add("TICKET_ID", f"id 형식 위반: {meta['id']!r}", where)
    if meta["kind"] not in KINDS:
        problems.add("TICKET_KIND", f"kind ∈ {KINDS}", where)
    if meta["risk"] not in RISKS:
        problems.add("TICKET_RISK", f"risk ∈ {RISKS}", where)
    if not isinstance(meta["predelegated"], bool):
        problems.add("TICKET_PREDELEGATED", "predelegated 는 boolean", where)

    for key in ("owned_paths", "oracle_paths"):
        paths = meta.get(key)
        if not isinstance(paths, list):
            problems.add("TICKET_PATHS_TYPE", f"{key} 는 배열", where)
            continue
        for p in paths:
            if not valid_repo_path(p):
                problems.add("TICKET_PATH", f"{key} 경로 위반(absolute/../glob 규칙): {p!r}", where)
    coordinated = meta.get("coordinated_paths")
    if not isinstance(coordinated, list):
        problems.add("TICKET_PATHS_TYPE", "coordinated_paths 는 배열", where)
    else:
        for entry in coordinated:
            if not isinstance(entry, dict) or not valid_repo_path(entry.get("path", "")) \
                    or not entry.get("reason") or not entry.get("symbols"):
                problems.add("TICKET_COORDINATED", f"coordinated path 는 {{path, reason, symbols}} 필요: {entry!r}", where)

    if meta["kind"] in EXECUTABLE_KINDS and not meta.get("owned_paths"):
        problems.add("TICKET_OWNED_EMPTY", f"executable ticket({meta['kind']})의 owned_paths 비어 있음 금지", where)
    if meta["kind"] == "rollback" and not meta.get("invalidates"):
        problems.add("TICKET_ROLLBACK_INVALIDATES", "rollback 티켓은 invalidates 필수", where)
    if meta["kind"] == "implementation":
        if not meta.get("adr_refs"):
            problems.add("TICKET_ADR_REF", "implementation 티켓은 adr_refs ≥ 1", where)
        if not meta.get("prd_ref"):
            problems.add("TICKET_PRD_REF", "implementation 티켓은 prd_ref 필수", where)

    owned = [p for p in (meta.get("owned_paths") or []) if isinstance(p, str)]
    oracle = [p for p in (meta.get("oracle_paths") or []) if isinstance(p, str)]
    overlap = [o for o in oracle if any_path_match(owned, o)] + \
              [o for o in owned if any_path_match(oracle, o)]
    if overlap:
        problems.add("TICKET_ORACLE_IN_OWNED", f"oracle_paths 와 owned_paths 분리 위반: {sorted(set(overlap))}", where)
    kernel_owned = [p for p in owned if is_kernel_path(p)]
    if kernel_owned and meta["kind"] != "governance-change":
        problems.add("TICKET_KERNEL_OWNED", f"governance kernel 경로는 governance-change 티켓만 소유 가능: {kernel_owned}", where)
    if meta["kind"] == "governance-change" and meta["risk"] != "critical":
        problems.add("TICKET_GOVERNANCE_RISK", "governance-change 는 risk=critical", where)

    acceptance = meta.get("acceptance")
    if not isinstance(acceptance, list):
        problems.add("TICKET_ACCEPTANCE_TYPE", "acceptance 는 배열", where)
    else:
        if meta["kind"] == "implementation" and not acceptance:
            problems.add("TICKET_ACCEPTANCE_EMPTY", "implementation 티켓은 최소 1개 acceptance oracle 필요 (rollback 은 post-merge CI 가 판정)", where)
        for ac in acceptance:
            if not isinstance(ac, dict) or not ac.get("id") or not ac.get("test_path"):
                problems.add("TICKET_AC_SHAPE", f"acceptance 는 {{id, test_path, cases}}: {ac!r}", where)
                continue
            if not ac.get("cases"):
                problems.add("TICKET_AC_NO_CASE", f"acceptance {ac['id']} 에 named case 없음", where)
            if oracle and not any_path_match(oracle, ac["test_path"]):
                problems.add("TICKET_AC_NOT_ORACLE", f"acceptance test_path 는 oracle_paths 안에 있어야 한다: {ac['test_path']}", where)

    budgets = meta.get("budgets")
    if not isinstance(budgets, dict) or not isinstance(budgets.get("repair_rounds"), int) \
            or not isinstance(budgets.get("wall_minutes"), int):
        problems.add("TICKET_BUDGETS", "budgets.repair_rounds/wall_minutes 정수 필수", where)

    commands = meta.get("commands")
    if not isinstance(commands, dict) or "full" not in commands or "manual" not in commands:
        problems.add("TICKET_COMMANDS", "commands 에 focused/full/build/lint/typecheck/manual 키 필요", where)


def load_tickets(root: Path, problems: Problems) -> dict[str, dict]:
    tickets: dict[str, dict] = {}
    tickets_dir = root / "docs" / "tickets"
    if not tickets_dir.is_dir():
        return tickets
    for path in sorted(tickets_dir.rglob("*.md")):
        parsed = parse_ticket_file(path, problems)
        if parsed is None:
            continue
        meta = parsed["meta"]
        validate_ticket_meta(meta, str(path), problems)
        tid = meta.get("id")
        if isinstance(tid, str):
            if tid in tickets:
                problems.add("TICKET_DUPLICATE_ID", f"티켓 ID 중복: {tid} ({path} vs {tickets[tid]['path']})", str(path))
            else:
                tickets[tid] = parsed
    return tickets


def validate_ticket_graph(root: Path, tickets: dict[str, dict], problems: Problems) -> None:
    ids = set(tickets)
    for tid, ticket in tickets.items():
        meta = ticket["meta"]
        where = str(ticket["path"])
        for dep in meta.get("dependencies") or []:
            if dep not in ids:
                problems.add("TICKET_DEP_MISSING", f"{tid} 의존성 {dep} 이 존재하지 않는다", where)
        for ref_list in ("invalidates", "supersedes"):
            for ref in meta.get(ref_list) or []:
                if ref not in ids:
                    problems.add("TICKET_REF_MISSING", f"{tid}.{ref_list} 가 없는 티켓 {ref} 를 가리킨다", where)
        for adr in meta.get("adr_refs") or []:
            if not list((root / "docs" / "adr").glob(f"{adr}*.md")):
                problems.add("TICKET_ADR_FILE", f"{tid} 의 {adr} 에 해당하는 docs/adr 파일 없음", where)
        prd = meta.get("prd_ref")
        if prd and not list((root / "docs" / "prd").glob(f"{prd}*.md")):
            problems.add("TICKET_PRD_FILE", f"{tid} 의 {prd} 에 해당하는 docs/prd 파일 없음", where)

    # DAG: 사이클 검출 (색칠 DFS)
    color: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = 1
        for dep in tickets[node]["meta"].get("dependencies") or []:
            if dep not in tickets:
                continue
            if color.get(dep) == 1:
                problems.add("TICKET_DAG_CYCLE", f"의존성 사이클: {' → '.join(stack + [node, dep])}")
                continue
            if color.get(dep, 0) == 0:
                visit(dep, stack + [node])
        color[node] = 2

    for tid in tickets:
        if color.get(tid, 0) == 0:
            visit(tid, [])

    # 관계로 연결되지 않은 활성 티켓 간 ownership overlap 금지
    invalidated = {ref for t in tickets.values() for ref in t["meta"].get("invalidates") or []}
    superseded = {ref for t in tickets.values() for ref in t["meta"].get("supersedes") or []}
    terminal = invalidated | superseded
    linked: set[tuple[str, str]] = set()
    for tid, ticket in tickets.items():
        for ref in (ticket["meta"].get("invalidates") or []) + (ticket["meta"].get("supersedes") or []):
            linked.add((tid, ref))
            linked.add((ref, tid))
    active = [tid for tid in tickets if tid not in terminal]
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if (a, b) in linked:
                continue
            owned_a = tickets[a]["meta"].get("owned_paths") or []
            owned_b = tickets[b]["meta"].get("owned_paths") or []
            clash = [
                (pa, pb) for pa in owned_a for pb in owned_b
                if isinstance(pa, str) and isinstance(pb, str)
                and (path_matches(pa, pb.rstrip("*").rstrip("/")) or path_matches(pb, pa.rstrip("*").rstrip("/"))
                     or pa == pb)
            ]
            if clash:
                problems.add("TICKET_OWNERSHIP_OVERLAP", f"{a} 와 {b} 의 owned_paths 겹침: {clash[:3]}")


def validate_oracles(root: Path, tickets: dict[str, dict], problems: Problems) -> None:
    for tid, ticket in tickets.items():
        meta = ticket["meta"]
        if meta.get("kind") != "implementation":
            continue
        needs_existing = meta.get("risk") in ("high", "critical")
        for ac in meta.get("acceptance") or []:
            if not isinstance(ac, dict) or not ac.get("test_path"):
                continue
            test_file = root / ac["test_path"]
            if not test_file.is_file():
                if needs_existing:
                    problems.add("ORACLE_MISSING", f"{tid}: high/critical 은 구현 시작 전 oracle 존재 필수 — {ac['test_path']}", str(ticket["path"]))
                continue
            content = test_file.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                problems.add("ORACLE_EMPTY", f"{tid}: oracle 파일이 비어 있다(zero-test lane) — {ac['test_path']}")
                continue
            for case in ac.get("cases") or []:
                if case not in content:
                    problems.add("ORACLE_CASE_MISSING", f"{tid}: named case {case!r} 가 {ac['test_path']} 에 없다")


# --------------------------------------------------------------- repo-level

def scan_policy_drift(root: Path, problems: Problems) -> None:
    canonical = root / "governance" / "policy.v1.json"
    for path in root.rglob("*.json"):
        if ".git" in path.parts or path == canonical:
            continue
        if "schemas" in path.parts or "node_modules" in path.parts:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f'"{SCHEMA_POLICY}"' in text:
            problems.add("POLICY_DUPLICATE", f"policy SSOT 밖의 policy 사본: {path.relative_to(root)}")


def scan_current_state_artifacts(root: Path, problems: Problems) -> None:
    for path in root.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".autopilot/", ".governance-broker/", "governance/.")):
            continue  # runtime scratch — gitignore 대상, 커밋 여부는 git 검사에서
        if CURRENT_STATE_NAME_RE.search(path.name):
            problems.add("CURRENT_STATE_COMMITTED", f"current-state projection 커밋 금지: {rel}")
        if path.name.startswith("genesis-approval"):
            problems.add("PRIVATE_METADATA_COMMITTED", f"private approval receipt 는 repo에 커밋하지 않는다: {rel}")


def load_actions_lock(root: Path, problems: Problems) -> dict[str, str]:
    """governance/actions-lock.v1.json → {action_repo: 40-hex sha}. lock 에 없는
    외부 action 은 실패다."""
    lock_path = root / "governance" / "actions-lock.v1.json"
    if not lock_path.is_file():
        problems.add("ACTIONS_LOCK_MISSING", "governance/actions-lock.v1.json 없음")
        return {}
    try:
        lock = load_json_file(lock_path)
    except (OSError, json.JSONDecodeError) as error:
        problems.add("ACTIONS_LOCK_INVALID", f"actions-lock 파싱 실패 — {error}")
        return {}
    if lock.get("schema") != "repo-governance.actions-lock.v1" or \
            not isinstance(lock.get("actions"), list):
        problems.add("ACTIONS_LOCK_INVALID", "schema=repo-governance.actions-lock.v1 + actions[] 필수")
        return {}
    index: dict[str, str] = {}
    for entry in lock["actions"]:
        if not isinstance(entry, dict) or not entry.get("uses") or not entry.get("commit"):
            problems.add("ACTIONS_LOCK_INVALID", f"lock entry 는 {{uses, commit, resolved_from}}: {entry!r}")
            continue
        if not FULL_SHA_RE.fullmatch(str(entry["commit"])):
            problems.add("ACTIONS_LOCK_INVALID", f"lock commit 이 full SHA 가 아니다: {entry['uses']}")
            continue
        index[entry["uses"]] = entry["commit"]
    return index


def scan_workflow_pins(root: Path, problems: Problems) -> None:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        problems.add("WORKFLOWS_MISSING", ".github/workflows 없음")
        return
    lock_index = load_actions_lock(root, problems)
    for wf in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if PR_TARGET_TRIGGER_RE.search(text):
            problems.add("WORKFLOW_PR_TARGET",
                         f"{wf.name}: pull_request_target 도입 금지 (privileged context 에서 candidate code)")
        if "permissions:" not in text:
            problems.add("WORKFLOW_PERMISSIONS_MISSING",
                         f"{wf.name}: 명시적 최소 permissions 선언 없음")
        elif PR_TRIGGER_RE.search(text) and WRITE_PERMISSION_RE.search(text):
            problems.add("WORKFLOW_PR_WRITE",
                         f"{wf.name}: pull_request 트리거 workflow 에 write permission 금지")
        for uses in USES_RE.findall(text):
            if uses.startswith("./"):
                if ".." in uses:
                    problems.add("ACTION_LOCAL_ESCAPE", f"{wf.name}: local action path escape — {uses}")
                continue
            if uses.startswith("docker://"):
                problems.add("ACTION_DOCKER", f"{wf.name}: docker action 은 lock 대상 아님 — 금지: {uses}")
                continue
            if "@" not in uses:
                problems.add("ACTION_UNPINNED", f"{wf.name}: uses 에 ref 없음 — {uses}")
                continue
            action_repo, ref = uses.rsplit("@", 1)
            if not FULL_SHA_RE.fullmatch(ref):
                problems.add("ACTION_NOT_SHA", f"{wf.name}: full commit SHA pin 아님 — {uses}")
                continue
            locked = lock_index.get(action_repo)
            if locked is None:
                problems.add("ACTION_NOT_IN_LOCK",
                             f"{wf.name}: actions-lock 에 없는 외부 action — {action_repo}")
            elif locked != ref:
                problems.add("ACTION_LOCK_DRIFT",
                             f"{wf.name}: {action_repo} lock {locked[:8]} ≠ workflow {ref[:8]}")
    for required in REQUIRED_WORKFLOWS:
        if not (root / required).is_file():
            problems.add("WORKFLOW_REQUIRED_MISSING", f"필수 workflow 없음: {required}")


def load_profile_lock(root: Path, problems: Problems, policy) -> dict | None:
    """governance/github-profile.lock.json — Genesis 시점 확정 profile. 동적 상태 아님."""
    lock_path = root / "governance" / "github-profile.lock.json"
    if not lock_path.is_file():
        problems.add("PROFILE_LOCK_MISSING", "governance/github-profile.lock.json 없음 — profile 미확정 상태로 운영 금지")
        return None
    try:
        lock = load_json_file(lock_path)
    except (OSError, json.JSONDecodeError) as error:
        problems.add("PROFILE_LOCK_INVALID", f"profile lock 파싱 실패 — {error}")
        return None
    if lock.get("schema") != "repo-governance.github-profile.lock.v1":
        problems.add("PROFILE_LOCK_INVALID", "schema ≠ repo-governance.github-profile.lock.v1")
        return None
    profile = lock.get("profile")
    visibility = lock.get("visibility")
    if profile not in PROFILE_NAMES:
        problems.add("PROFILE_LOCK_INVALID", f"알 수 없는 profile: {profile}")
        return lock
    if visibility == "private":
        if profile != "FREE_PRIVATE_COMPENSATING" or lock.get("native_branch_enforcement") \
                or lock.get("native_auto_merge") or lock.get("merge_queue"):
            problems.add("PROFILE_NATIVE_CLAIM",
                         "private Free 에서 native enforcement 주장 금지 — public/private 보증 혼동")
    elif visibility == "public":
        if lock.get("owner_type") == "User" and (profile != "FREE_PUBLIC_USER_NATIVE"
                                                 or lock.get("merge_queue")):
            problems.add("PROFILE_LOCK_INVALID", "User+public ⇒ FREE_PUBLIC_USER_NATIVE, merge_queue=false")
    else:
        problems.add("PROFILE_LOCK_INVALID", f"visibility={visibility!r}")
    if policy:
        policy_visibility = (policy.get("repository") or {}).get("visibility")
        policy_repo = (policy.get("repository") or {}).get("name")
        if policy_visibility and visibility and policy_visibility != visibility:
            problems.add("PROFILE_VISIBILITY_DRIFT",
                         f"policy visibility {policy_visibility} ≠ profile lock {visibility}")
        if policy_repo and lock.get("repository") and policy_repo != lock["repository"]:
            problems.add("PROFILE_REPO_DRIFT",
                         f"policy repo {policy_repo} ≠ profile lock {lock['repository']}")
    return lock


def scan_factory_lock(root: Path, problems: Problems) -> None:
    lock_path = root / "governance" / "factory-lock.json"
    if not lock_path.is_file():
        problems.add("FACTORY_LOCK_MISSING", "governance/factory-lock.json 없음")
        return
    try:
        lock = load_json_file(lock_path)
    except (OSError, json.JSONDecodeError) as error:
        problems.add("FACTORY_LOCK_INVALID", f"factory-lock 파싱 실패 — {error}")
        return
    if lock.get("governance_schema") != GOVERNANCE_SCHEMA_VERSION:
        problems.add(
            "FACTORY_VERSION_INCOMPATIBLE",
            f"governance_schema {lock.get('governance_schema')} ≠ {GOVERNANCE_SCHEMA_VERSION} — repo-factory upgrade --plan 으로만 이행한다",
        )


def scan_adapters(root: Path, policy, problems: Problems) -> None:
    runtime = policy.get("agent_runtime") or {}
    wanted = registered_agents(policy)
    adapters_dir = root / "governance" / "adapters"
    for adapter_id in sorted(wanted):
        path = adapters_dir / f"{adapter_id}.json"
        if not path.is_file():
            problems.add("ADAPTER_MISSING", f"governance/adapters/{adapter_id}.json 없음 (agent_runtime 이 참조)")
            continue
        try:
            adapter = load_json_file(path)
        except (OSError, json.JSONDecodeError) as error:
            problems.add("ADAPTER_INVALID", f"{path.name} 파싱 실패 — {error}")
            continue
        if adapter.get("schema") != "repo-governance.agent-adapter.v1" or adapter.get("id") != adapter_id:
            problems.add("ADAPTER_SCHEMA", f"{path.name}: schema/id 불일치")
    del runtime


def load_policy(root: Path, problems: Problems):
    policy_path = root / "governance" / "policy.v1.json"
    if not policy_path.is_file():
        problems.add("POLICY_MISSING", "governance/policy.v1.json 없음 — policy SSOT 부재")
        return None
    try:
        policy = load_json_file(policy_path)
    except (OSError, json.JSONDecodeError) as error:
        problems.add("POLICY_UNREADABLE", f"policy 파싱 실패 — {error}")
        return None
    validate_policy(policy, problems)
    return policy


def validate_repo(root: Path) -> tuple[Problems, dict]:
    problems = Problems()
    policy = load_policy(root, problems)
    tickets = load_tickets(root, problems)
    if tickets:
        validate_ticket_graph(root, tickets, problems)
        validate_oracles(root, tickets, problems)
    scan_policy_drift(root, problems)
    scan_current_state_artifacts(root, problems)
    scan_workflow_pins(root, problems)
    scan_factory_lock(root, problems)
    profile_lock = load_profile_lock(root, problems, policy)
    if policy:
        scan_adapters(root, policy, problems)
    context = {
        "policy": policy,
        "policy_digest": digest_obj(policy) if policy else None,
        "tickets": tickets,
        "profile": profile_lock,
    }
    return problems, context


# ----------------------------------------------------------------- check-pr

def git_output(root: Path, args: list[str]) -> tuple[int, str]:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip()


def changed_files_from_git(root: Path, base: str, head: str, problems: Problems) -> list[tuple[str, str]]:
    code, out = git_output(root, ["diff", "--name-status", f"{base}...{head}"])
    if code:
        # CI checkout 은 base 를 remote-tracking ref 로만 가진다
        code, out = git_output(root, ["diff", "--name-status", f"origin/{base}...{head}"])
    if code:
        problems.add("GIT_DIFF_FAILED", f"git diff {base}...{head} 실패 — 사실을 못 보면 통과 아님")
        return []
    changed = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0][:1]
            path = parts[-1]
            changed.append((status, path))
    return changed


def product_code_paths(paths: list[str], tickets: dict[str, dict]) -> list[str]:
    """docs/·kernel·oracle 밖 = 제품 코드로 본다."""
    oracle_all = [p for t in tickets.values() for p in t["meta"].get("oracle_paths") or []]
    out = []
    for p in paths:
        if p.startswith("docs/") or is_kernel_path(p):
            continue
        if any_path_match(oracle_all, p):
            continue
        out.append(p)
    return out


def check_pr(root: Path, *, body: str, branch: str | None, base_ref: str | None,
             changed: list[tuple[str, str]], event_head: str | None = None,
             checkout_head: str | None = None, online: bool = False) -> tuple[Problems, list[dict]]:
    problems = Problems()
    not_checked: list[dict] = []
    repo_problems, context = validate_repo(root)
    # PR 검사는 저장소 계약이 깨져 있으면 어차피 실패다 — 그대로 합친다.
    problems.items.extend(repo_problems.items)
    policy = context["policy"]
    tickets = context["tickets"]

    ticket_lines = TICKET_LINE_RE.findall(body or "")
    if len(ticket_lines) != 1:
        problems.add("PR_TICKET_LINE", f"PR body 의 'Ticket:' 줄이 정확히 1개여야 한다 (현재 {len(ticket_lines)}개)")
        return problems, not_checked
    ticket_id = ticket_lines[0]
    ticket = tickets.get(ticket_id)
    if ticket is None:
        problems.add("PR_TICKET_UNKNOWN", f"티켓 {ticket_id} 가 docs/tickets 에 없다")
        return problems, not_checked
    meta = ticket["meta"]
    kind = meta.get("kind")
    risk = meta.get("risk")

    if event_head and checkout_head and event_head != checkout_head:
        problems.add("PR_HEAD_MISMATCH", f"event head {event_head[:12]} ≠ checkout HEAD {checkout_head[:12]}")

    if policy:
        integration = (policy.get("branches") or {}).get("integration")
        production = (policy.get("branches") or {}).get("production")
        allowed_bases = {integration}
        if kind == "implementation":
            allowed_bases.add(production)  # hotfix
        if base_ref and base_ref not in allowed_bases:
            problems.add("PR_BASE", f"base {base_ref!r} 허용 안 됨 (integration={integration})")

    if branch:
        match = BRANCH_RE.fullmatch(branch)
        if not match:
            problems.add("PR_BRANCH_NAME", f"stable branch naming 위반: {branch!r} (예: feat/{ticket_id}-slug)")
        else:
            if match.group("id") != ticket_id:
                problems.add("PR_BRANCH_TICKET", f"branch 의 티켓 {match.group('id')} ≠ Ticket: {ticket_id}")
            if kind in KIND_BRANCH_PREFIXES and match.group("prefix") not in KIND_BRANCH_PREFIXES[kind]:
                problems.add("PR_BRANCH_KIND", f"kind={kind} 는 {KIND_BRANCH_PREFIXES[kind]} prefix 만 허용")

    changed_paths = [p for _, p in changed]
    deleted_paths = [p for s, p in changed if s == "D"]
    own_ticket_rel = ticket["path"].resolve()
    try:
        own_ticket_rel = own_ticket_rel.relative_to(root.resolve()).as_posix()
    except ValueError:
        own_ticket_rel = ticket["path"].as_posix()

    owned = meta.get("owned_paths") or []
    coordinated = [c["path"] for c in meta.get("coordinated_paths") or [] if isinstance(c, dict) and "path" in c]
    oracle_own = meta.get("oracle_paths") or []
    oracle_others = {
        other_id: other["meta"].get("oracle_paths") or []
        for other_id, other in tickets.items() if other_id != ticket_id
    }
    invalidated_by_this = set(meta.get("invalidates") or []) | set(meta.get("supersedes") or [])

    # Contract self-expansion 방지
    self_contract = [own_ticket_rel]
    for adr in meta.get("adr_refs") or []:
        self_contract += [p.relative_to(root).as_posix() for p in (root / "docs" / "adr").glob(f"{adr}*.md")]
    if meta.get("prd_ref"):
        self_contract += [p.relative_to(root).as_posix() for p in (root / "docs" / "prd").glob(f"{meta['prd_ref']}*.md")]

    if kind == "implementation":
        for path in changed_paths:
            if path in self_contract:
                problems.add("PR_SELF_EXPANSION", f"구현 PR이 자기 계약을 수정: {path}")
            elif is_kernel_path(path) or path.startswith(".github/workflows/"):
                problems.add("PR_KERNEL_TOUCHED", f"구현 PR이 governance kernel/workflow 수정: {path} (governance-change 티켓 필요)")
            elif any_path_match(oracle_own, path):
                problems.add("PR_ORACLE_TOUCHED", f"구현 PR이 자기 acceptance oracle 수정: {path} (contract-change 필요)")
            elif not (any_path_match(owned, path) or any_path_match(coordinated, path)):
                other_oracle_owner = next(
                    (oid for oid, opaths in oracle_others.items() if any_path_match(opaths, path)), None
                )
                if other_oracle_owner:
                    problems.add("PR_OTHER_ORACLE", f"다른 티켓({other_oracle_owner})의 oracle 수정: {path}")
                else:
                    problems.add("PR_UNOWNED_DIFF", f"owned/coordinated 밖의 diff: {path}")
    elif kind == "contract-change":
        product = product_code_paths(changed_paths, tickets)
        if product:
            problems.add("PR_CONTRACT_MIXED", f"contract-change 에 제품 코드 혼합 금지: {product[:5]}")
        for path in changed_paths:
            if is_kernel_path(path) and not path.startswith("docs/"):
                problems.add("PR_CONTRACT_KERNEL", f"contract-change 가 governance kernel 수정: {path} (governance-change 필요)")
    elif kind == "governance-change":
        non_kernel = [
            p for p in changed_paths
            if not (is_kernel_path(p) or p.startswith(".github/") or p == own_ticket_rel
                    or p.startswith("docs/adr/") or p.startswith("AGENTS.md"))
        ]
        if non_kernel:
            problems.add("PR_GOVERNANCE_MIXED", f"governance-change 에 제품 코드 혼합 금지: {non_kernel[:5]}")
    elif kind == "rollback":
        if not meta.get("invalidates"):
            problems.add("PR_REVERT_NO_INVALIDATES", "revert/rollback PR에 invalidates 없음")
        allowed = list(owned)
        for target in meta.get("invalidates") or []:
            target_meta = tickets.get(target, {}).get("meta", {})
            allowed += (target_meta.get("owned_paths") or []) + (target_meta.get("oracle_paths") or [])
        for path in changed_paths:
            if path == own_ticket_rel or path.startswith("docs/tickets/"):
                continue
            if not any_path_match(allowed, path):
                problems.add("PR_ROLLBACK_SCOPE", f"rollback 범위 밖 diff: {path}")

    # Revert 형태인데 invalidates 없는 경우 (제목/본문 휴리스틱)
    if kind != "rollback" and re.search(r"(^|\s)[Rr]evert(s|ed)?\b", body.splitlines()[0] if body else ""):
        if not meta.get("invalidates"):
            problems.add("PR_REVERT_NO_INVALIDATES", "Revert 형태 PR인데 티켓에 invalidates 가 없다")

    # 다른 티켓 oracle 삭제 → invalidation/supersession 필요
    for path in deleted_paths:
        for other_id, opaths in oracle_others.items():
            if any_path_match(opaths, path) and other_id not in invalidated_by_this:
                problems.add("PR_ORACLE_DELETED", f"{other_id} 의 oracle {path} 삭제에 invalidates/supersedes 없음")

    # dependency/lockfile 변경은 최소 high
    dep_changes = [p for p in changed_paths if Path(p).name in DEPENDENCY_MANIFESTS]
    if dep_changes and risk not in ("high", "critical"):
        problems.add("PR_DEPENDENCY_RISK", f"dependency/lockfile 변경은 최소 high risk: {dep_changes} (risk={risk})")

    # current-state projection 편집 금지
    for path in changed_paths:
        if CURRENT_STATE_NAME_RE.search(Path(path).name):
            problems.add("PR_CURRENT_STATE_EDIT", f"current-state projection 커밋/편집 금지: {path}")

    # secrets scan (added/modified 파일 내용)
    for status, path in changed:
        if status == "D":
            continue
        target = root / path
        if not target.is_file() or target.stat().st_size > 2_000_000:
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in SECRET_RES:
            if pattern.search(content):
                problems.add("PR_SECRET", f"secret 패턴 검출: {path}")
                break

    # non-vacuous: executable 티켓의 oracle 파일과 case가 head tree에 존재
    if kind in EXECUTABLE_KINDS:
        for ac in meta.get("acceptance") or []:
            if not isinstance(ac, dict) or not ac.get("test_path"):
                continue
            test_file = root / ac["test_path"]
            if not test_file.is_file():
                problems.add("PR_ORACLE_ABSENT", f"acceptance oracle 이 head tree 에 없다: {ac['test_path']}")
                continue
            content = test_file.read_text(encoding="utf-8", errors="replace")
            missing = [c for c in ac.get("cases") or [] if c not in content]
            if missing:
                problems.add("PR_CASE_ABSENT", f"named case 누락 {missing} — {ac['test_path']}")

    # online 전용 사실은 못 봤으면 못 봤다고 말한다
    if not online:
        not_checked.append({"check": "dependencies_verified", "state": "NOT_CHECKED", "reason": "online facts 필요"})
        not_checked.append({"check": "base_current", "state": "NOT_CHECKED", "reason": "online facts 필요"})
        not_checked.append({"check": "check_creator_identity", "state": "NOT_CHECKED", "reason": "online facts 필요"})
    return problems, not_checked


# ------------------------------------------------------------------- status

def offline_status(root: Path) -> dict:
    problems, context = validate_repo(root)
    tickets = context["tickets"]
    invalidated = {ref for t in tickets.values() for ref in t["meta"].get("invalidates") or []}
    superseded = {ref for t in tickets.values() for ref in t["meta"].get("supersedes") or []}
    ticket_view = {}
    for tid, ticket in tickets.items():
        meta = ticket["meta"]
        declared = "invalidated" if tid in invalidated else "superseded" if tid in superseded else "planned"
        ticket_view[tid] = {
            "kind": meta.get("kind"),
            "risk": meta.get("risk"),
            "dependencies": meta.get("dependencies") or [],
            "declared_state": declared,
        }
    return {
        "schema": "repo-governance.status.v1",
        "mode": "offline",
        "external_state": "NOT_CHECKED",
        "claims_online_readiness": False,
        "claims_policy_authorization": False,
        "contract_valid": not bool(problems),
        "contract_problems": problems.items,
        "policy_digest": context["policy_digest"],
        "tickets": ticket_view,
    }


class FactsUnavailable(Exception):
    pass


def gh_bin() -> str:
    return os.environ.get("REPO_GOVERNANCE_GH", "gh")


def gh_json(args: list[str]):
    result = subprocess.run([gh_bin(), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise FactsUnavailable(f"gh {' '.join(args[:3])}… 실패({result.returncode}): {result.stderr.strip()[:400]}")
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise FactsUnavailable(f"gh 응답이 JSON 이 아니다 — {error}") from error


def collect_facts(policy) -> dict:
    """GitHub current facts. partial/incomplete 응답은 FactsUnavailable 로 fail-closed."""
    repo = (policy.get("repository") or {}).get("name")
    branches = policy.get("branches") or {}
    facts: dict = {"repository": repo}
    facts["repo"] = gh_json(["api", f"repos/{repo}"])
    facts["pulls"] = gh_json([
        "api", f"repos/{repo}/pulls?state=all&per_page=100", "--paginate",
    ]) or []
    if isinstance(facts["pulls"], dict):
        facts["pulls"] = [facts["pulls"]]
    # 공장은 ruleset 을 만든다 — /rules/branches 는 classic protection + ruleset 을 모두
    # 반영하는 effective view 다. classic /protection API 는 ruleset-only repo 에서 404.
    facts["protection"] = {}
    for branch in {branches.get("integration"), branches.get("production")} - {None}:
        try:
            rules = gh_json(["api", f"repos/{repo}/rules/branches/{branch}"]) or []
            types = {r.get("type") for r in rules}
            facts["protection"][branch] = {"types": sorted(t for t in types if t),
                                           "enforced": {"pull_request", "required_status_checks"} <= types}
        except FactsUnavailable:
            facts["protection"][branch] = None
    facts["check_runs"] = {}
    return facts


def check_runs_for(repo: str, sha: str) -> list[dict]:
    data = gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100", "--paginate"])
    if isinstance(data, dict):
        return data.get("check_runs") or []
    runs: list[dict] = []
    for page in data or []:
        runs.extend(page.get("check_runs") or [])
    return runs


def online_status(root: Path) -> tuple[dict, int]:
    problems, context = validate_repo(root)
    policy = context["policy"]
    if policy is None or problems:
        return {
            "schema": "repo-governance.status.v1", "mode": "online",
            "technical_state": "unknown", "policy_authorized": False,
            "claims_human_approval": False,
            "reason": "contract invalid — 계약이 깨진 상태에서 online 판정은 하지 않는다",
            "contract_problems": problems.items,
        }, 1
    try:
        facts = collect_facts(policy)
    except FactsUnavailable as error:
        return {
            "schema": "repo-governance.status.v1", "mode": "online",
            "technical_state": "unknown", "policy_authorized": False,
            "claims_human_approval": False, "reason": str(error),
        }, 1

    repo = facts["repository"]
    checks_cfg = policy["checks"]
    tickets = context["tickets"]
    ticket_states: dict[str, dict] = {}
    for tid, ticket in tickets.items():
        prs = [
            pr for pr in facts["pulls"]
            if TICKET_LINE_RE.findall(pr.get("body") or "") == [tid]
        ]
        open_prs = [pr for pr in prs if pr.get("state") == "open"]
        merged_prs = [pr for pr in prs if pr.get("merged_at")]
        state = "planned"
        detail: dict = {}
        if len(open_prs) > 1:
            state, detail = "blocked", {"reason_code": "MULTIPLE_OPEN_PRS", "prs": [p["number"] for p in open_prs]}
        elif open_prs:
            pr = open_prs[0]
            head_sha = (pr.get("head") or {}).get("sha")
            try:
                runs = check_runs_for(repo, head_sha)
            except FactsUnavailable as error:
                state, detail = "unknown", {"reason_code": "FACTS_UNAVAILABLE", "error": str(error)}
                ticket_states[tid] = {"technical_state": state, **detail}
                continue
            by_name = {r.get("name"): r for r in runs}
            required = [checks_cfg["governance"], checks_cfg["project_ci"], checks_cfg["review"]]
            missing = [n for n in required if n not in by_name]
            failed = [n for n in required if by_name.get(n, {}).get("conclusion") not in (None, "success")]
            pending = [n for n in required if n in by_name and by_name[n].get("conclusion") is None]
            if failed:
                state, detail = "repair", {"reason_code": "CHECKS_FAILED", "failed": failed}
            elif missing or pending:
                state, detail = "ci", {"reason_code": "CHECKS_PENDING", "pending": missing + pending}
            else:
                state, detail = "merge_ready", {"head_sha": head_sha, "pr": pr["number"]}
        elif merged_prs:
            # 머지 후 전이. post-merge.yml 은 dev push(= exact merge SHA)에서 이미
            # governance validate + full CI 를 돌리고 있었지만 아무도 그 결과를 읽지
            # 않아 상태가 post_merge 에 영구히 머물렀다. compute_ready 는 verified 만
            # 의존성 완료로 세므로 루트 티켓 이후 그래프가 한 걸음도 나가지 못했다.
            #
            # 상태를 따로 저장하지 않는다. 이 함수는 전부 GitHub facts 에서 유도하고,
            # merge SHA 의 check run 이 그 증거다.
            merged = merged_prs[-1]
            merge_sha = merged.get("merge_commit_sha")
            post_merge_name = checks_cfg.get("post_merge")
            if not merge_sha or not post_merge_name:
                state, detail = "post_merge", {
                    "reason_code": "POST_MERGE_NOT_EVALUATED", "pr": merged["number"],
                    "detail": "merge_commit_sha 또는 checks.post_merge 가 없다"}
            else:
                try:
                    merge_runs = check_runs_for(repo, merge_sha)
                except FactsUnavailable as error:
                    # 확인하지 못한 것을 통과로 표시하지 않는다.
                    ticket_states[tid] = {
                        "technical_state": "unknown", "reason_code": "FACTS_UNAVAILABLE",
                        "error": str(error), "merge_sha": merge_sha}
                    continue
                run = next((r for r in merge_runs if r.get("name") == post_merge_name), None)
                conclusion = (run or {}).get("conclusion")
                if run is None:
                    state, detail = "post_merge", {
                        "reason_code": "POST_MERGE_RUN_MISSING",
                        "pr": merged["number"], "merge_sha": merge_sha}
                elif conclusion is None:
                    state, detail = "post_merge", {
                        "reason_code": "POST_MERGE_PENDING",
                        "pr": merged["number"], "merge_sha": merge_sha}
                elif conclusion == "success":
                    state, detail = "verified", {
                        "pr": merged["number"], "merge_sha": merge_sha,
                        "evidence": f"{post_merge_name}@{merge_sha}"}
                else:
                    state, detail = "repair", {
                        "reason_code": "POST_MERGE_FAILED", "pr": merged["number"],
                        "merge_sha": merge_sha, "conclusion": conclusion}
        ticket_states[tid] = {"technical_state": state, **detail}

    invalidated = {ref for t in tickets.values() for ref in t["meta"].get("invalidates") or []}
    for tid in ticket_states:
        if tid in invalidated:
            ticket_states[tid]["invalidated_declared"] = True

    merge_ready = [tid for tid, s in ticket_states.items() if s["technical_state"] == "merge_ready"]
    authorized = {}
    for tid in merge_ready:
        meta = tickets[tid]["meta"]
        risk = meta.get("risk")
        conditions = {
            "risk_in_auto_merge": risk in (policy["autonomy"].get("auto_merge") or []),
            "predelegated_ok": risk != "high" or bool(meta.get("predelegated")),
            "critical_ok": risk != "critical" or policy["autonomy"].get("critical_default") == "predelegated",
        }
        authorized[tid] = {"policy_authorized": all(conditions.values()), "conditions": conditions}
    overall = "merge_ready" if merge_ready else "idle"
    return {
        "schema": "repo-governance.status.v1", "mode": "online",
        "technical_state": overall,
        "policy_authorized": any(a["policy_authorized"] for a in authorized.values()),
        "claims_human_approval": False,
        "policy_digest": context["policy_digest"],
        "tickets": ticket_states,
        "authorization": authorized,
    }, 0


# ----------------------------------------------------------------- manifest

def build_manifest(root: Path) -> tuple[dict | None, Problems]:
    problems, context = validate_repo(root)
    if problems:
        return None, problems
    tickets = context["tickets"]
    oracle_inventory = sorted({
        p for t in tickets.values() for p in t["meta"].get("oracle_paths") or []
    })
    workflows = {}
    for rel in REQUIRED_WORKFLOWS:
        path = root / rel
        if path.is_file():
            workflows[rel] = digest_bytes(path.read_bytes())
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "policy_digest": context["policy_digest"],
        "tickets": {tid: digest_obj(t["meta"]) for tid, t in sorted(tickets.items())},
        "oracle_inventory": oracle_inventory,
        "adr": sorted(p.name for p in (root / "docs" / "adr").glob("ADR-*.md")),
        "prd": sorted(p.name for p in (root / "docs" / "prd").glob("PRD-*.md")),
        "workflows": workflows,
    }
    manifest["manifest_digest"] = digest_obj({k: v for k, v in manifest.items() if k != "manifest_digest"})
    return manifest, problems


# ------------------------------------------------------------- security-scan

FORBIDDEN_FILE_NAMES = re.compile(r"^(id_rsa|id_ed25519|id_ecdsa)(\.|$)|\.(pem|p12|pfx)$")
PRIVATE_PATH_RE = re.compile(r"/(Users|home)/[A-Za-z0-9_.-]+/")
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml",
                 ".md", ".txt", ".toml", ".cfg", ".ini", ".sh", ".env", ".sql"}


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".git/", ".autopilot/", ".governance-broker/", "node_modules/")) \
                or "/__pycache__/" in rel or "/node_modules/" in rel:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env":
            continue
        if path.stat().st_size > 1_000_000:
            continue
        yield rel, path


def run_security_command(root: Path, command: str) -> tuple[str, str]:
    import shlex
    try:
        argv = shlex.split(command)
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                                timeout=600, check=False)
    except FileNotFoundError:
        return "UNAVAILABLE_BLOCKING", f"실행 파일 없음: {command} — success 로 반환하지 않는다"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "UNAVAILABLE_BLOCKING", f"{command} 실행 실패 — {error}"
    if result.returncode:
        return "FAIL", (result.stdout + result.stderr).strip()[-300:]
    return "PASS", "exit 0"


def security_scan(root: Path) -> tuple[list[dict], bool]:
    """profile 인지 security gate. UNAVAILABLE_BLOCKING 을 success 로 반환하지 않는다."""
    items: list[dict] = []

    def add(name: str, status: str, detail: str, cls: str = "shape") -> None:
        items.append({"name": name, "class": cls, "status": status, "detail": detail})

    wf_problems = Problems()
    scan_workflow_pins(root, wf_problems)
    codes = {i["code"] for i in wf_problems.items}
    pin_bad = sorted(c for c in codes if c.startswith(("ACTION", "WORKFLOW_REQUIRED")))
    add("workflow_action_sha_pin_lock", "FAIL" if pin_bad else "PASS",
        "; ".join(pin_bad) or "전 외부 action full-SHA + actions-lock 일치", "conservation")
    perm_bad = sorted(c for c in codes if c in ("WORKFLOW_PERMISSIONS_MISSING", "WORKFLOW_PR_WRITE"))
    add("workflow_permissions_audit", "FAIL" if perm_bad else "PASS",
        "; ".join(perm_bad) or "명시적 최소 권한 + PR workflow write 0", "conservation")
    add("pull_request_target_ban", "FAIL" if "WORKFLOW_PR_TARGET" in codes else "PASS",
        "pull_request_target 검출" if "WORKFLOW_PR_TARGET" in codes else "0건", "conservation")

    secret_hits, forbidden, private_paths = [], [], []
    for rel, path in iter_text_files(root):
        name = Path(rel).name
        if FORBIDDEN_FILE_NAMES.search(name) or (
                name == ".env" and not rel.endswith((".env.example", ".env.template"))):
            forbidden.append(rel)
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in SECRET_RES:
            if pattern.search(content):
                secret_hits.append(rel)
                break
        if rel.startswith("docs/") or rel.endswith(".md"):
            continue  # 문서의 예시 경로는 사람이 본다 — 코드/설정만 기계 차단
        if PRIVATE_PATH_RE.search(content):
            private_paths.append(rel)
    add("secret_like_diff_scan", "FAIL" if secret_hits else "PASS",
        f"검출: {secret_hits[:3]}" if secret_hits else "0건", "conservation")
    add("forbidden_file_scan", "FAIL" if forbidden else "PASS",
        f"검출: {forbidden[:3]}" if forbidden else "0건", "conservation")
    add("private_metadata_scan", "FAIL" if private_paths else "PASS",
        f"absolute local path: {private_paths[:3]}" if private_paths else "0건", "conservation")

    if (root / "package.json").is_file() and not any(
            (root / lf).is_file() for lf in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")):
        add("lockfile_consistency", "FAIL", "package.json 있음 + lockfile 없음", "conservation")
    else:
        add("lockfile_consistency", "PASS", "manifest↔lockfile 일관", "conservation")

    problems = Problems()
    policy = load_policy(root, problems)
    profile_lock = load_profile_lock(root, Problems(), policy)
    profile = (profile_lock or {}).get("profile")
    commands = (policy or {}).get("security_commands") or {}
    for lane in ("secret_scan", "sast", "dependency_audit"):
        command = commands.get(lane)
        if command:
            status, detail = run_security_command(root, command)
            add(f"custom_{lane}", status, detail, "compensating-control")
        elif profile == "FREE_PRIVATE_COMPENSATING":
            add(f"custom_{lane}", "NOT_APPLICABLE",
                "custom scanner 미구성 — 있는 것으로 가정하지 않으며 native 동등 주장 금지. "
                "high/critical 은 sast·dependency_audit 없이 auto-merge 금지(broker 강제)",
                "compensating-control")
        else:
            add(f"native_{lane}", "NOT_CHECKED",
                "public native lane (CodeQL/dependency review/secret scanning)은 원격 검증 대상",
                "native-enforcement")
    ok = not any(i["status"] in ("FAIL", "UNAVAILABLE_BLOCKING") for i in items)
    return items, ok


# ------------------------------------------------------------------- doctor

def doctor(root: Path, online: bool) -> tuple[list[dict], bool]:
    problems, context = validate_repo(root)
    results: list[dict] = [{
        "probe": "contract",
        "state": "PASS" if not problems else "FAIL",
        "detail": f"{len(problems.items)} problems",
    }]
    policy = context["policy"]
    if policy:
        adapters_dir = root / "governance" / "adapters"
        for adapter_id in sorted(registered_agents(policy)):
            path = adapters_dir / f"{adapter_id}.json"
            state = "PASS" if path.is_file() else "FAIL"
            results.append({"probe": f"adapter:{adapter_id}", "state": state, "detail": str(path)})
    if not online:
        for probe in ("local_controller_auth", "branch_protection", "auto_merge_capability",
                      "merge_queue_capability", "scheduler", "issue_write"):
            results.append({"probe": probe, "state": "NOT_CHECKED", "detail": "--online 필요"})
    else:
        try:
            repo_facts = collect_facts(policy) if policy else None
        except FactsUnavailable as error:
            results.append({"probe": "github_api", "state": "FAIL", "detail": str(error)})
            repo_facts = None
        if repo_facts:
            repo_info = repo_facts.get("repo") or {}
            profile_lock = load_profile_lock(root, Problems(), policy) or {}
            private = profile_lock.get("profile") == "FREE_PRIVATE_COMPENSATING"
            results.append({
                "probe": "auto_merge_capability",
                "state": "NOT_APPLICABLE" if private else
                         ("PASS" if repo_info.get("allow_auto_merge") else "UNAVAILABLE"),
                "detail": "private Free — native auto-merge 없음(보완 통제)" if private
                          else f"allow_auto_merge={repo_info.get('allow_auto_merge')}",
            })
            results.append({
                "probe": "merge_commit_allowed",
                "state": "PASS" if repo_info.get("allow_merge_commit") else "FAIL",
                "detail": f"allow_merge_commit={repo_info.get('allow_merge_commit')}",
            })
            for branch, protection in (repo_facts.get("protection") or {}).items():
                enforced = bool(protection and protection.get("enforced"))
                if private:
                    state, detail = "NOT_APPLICABLE", "private Free — ruleset/protection 없음(보완 통제로 대체)"
                elif enforced:
                    state, detail = "PASS", f"ruleset: {protection.get('types')}"
                else:
                    state = "FAIL"
                    detail = ("미설정 또는 조회 불가" if protection is None
                              else f"pull_request+required_status_checks 누락: {protection.get('types')}")
                results.append({"probe": f"branch_protection:{branch}", "state": state, "detail": detail})
            results.append({"probe": "merge_queue_capability", "state": "NOT_CHECKED",
                            "detail": "ruleset API probe 미구현 — queue 없으면 broker 직렬 merge fallback"})
    failed = any(r["state"] == "FAIL" for r in results)
    autonomous_ready = not failed and not problems and all(
        r["state"] == "PASS" for r in results if r["probe"].startswith("adapter:")
    )
    results.append({
        "probe": "autonomous_ready",
        "state": "PASS" if (autonomous_ready and online) else ("FAIL" if failed else "NOT_CHECKED"),
        "detail": "online probe 없이 autonomous-ready 를 주장하지 않는다" if not online else "",
    })
    return results, failed


# --------------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--root", type=Path, default=Path("."))
    p_validate.add_argument("--json", action="store_true", dest="as_json")

    p_check = sub.add_parser("check-pr")
    p_check.add_argument("--root", type=Path, default=Path("."))
    p_check.add_argument("--event", type=Path)
    p_check.add_argument("--body-file", type=Path)
    p_check.add_argument("--branch")
    p_check.add_argument("--base")
    p_check.add_argument("--head")
    p_check.add_argument("--changed-files", type=Path, help="테스트/오프라인용: 'STATUS<TAB>path' 줄 목록")
    p_check.add_argument("--online", action="store_true")
    p_check.add_argument("--json", action="store_true", dest="as_json")

    p_status = sub.add_parser("status")
    p_status.add_argument("--root", type=Path, default=Path("."))
    mode = p_status.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--online", action="store_true")
    p_status.add_argument("--json", action="store_true", dest="as_json")

    p_render = sub.add_parser("render")
    p_render.add_argument("--root", type=Path, default=Path("."))
    p_render.add_argument("--format", choices=("markdown",), default="markdown")

    p_manifest = sub.add_parser("manifest")
    p_manifest.add_argument("--root", type=Path, default=Path("."))
    p_manifest.add_argument("--output", type=Path)

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("--root", type=Path, default=Path("."))
    p_doctor.add_argument("--online", action="store_true")

    p_security = sub.add_parser("security-scan")
    p_security.add_argument("--root", type=Path, default=Path("."))
    p_security.add_argument("--json", action="store_true", dest="as_json")

    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: --root 디렉터리 없음: {root}", file=sys.stderr)
        return 2

    if args.command == "validate":
        problems, context = validate_repo(root)
        if args.as_json:
            print(json.dumps({
                "ok": not bool(problems),
                "policy_digest": context["policy_digest"],
                "ticket_count": len(context["tickets"]),
                "problems": problems.items,
            }, ensure_ascii=False))
        else:
            problems.render()
            print(("PASS" if not problems else "FAIL")
                  + f" — tickets={len(context['tickets'])} policy_digest={context['policy_digest']}")
        return 0 if not problems else 1

    if args.command == "check-pr":
        body, branch, base_ref, event_head = "", args.branch, args.base, args.head
        if args.event:
            try:
                event = load_json_file(args.event)
            except (OSError, json.JSONDecodeError) as error:
                print(f"ERROR: event 파일 파싱 실패 — {error}", file=sys.stderr)
                return 2
            pr = event.get("pull_request") or {}
            body = pr.get("body") or ""
            branch = branch or (pr.get("head") or {}).get("ref")
            base_ref = base_ref or (pr.get("base") or {}).get("ref")
            event_head = event_head or (pr.get("head") or {}).get("sha")
        if args.body_file:
            body = args.body_file.read_text(encoding="utf-8")
        if not body:
            print("ERROR: --event 또는 --body-file 로 PR body 를 제공해야 한다", file=sys.stderr)
            return 2

        checkout_head = None
        code, out = git_output(root, ["rev-parse", "HEAD"])
        if not code:
            checkout_head = out
        problems_holder = Problems()
        if args.changed_files:
            changed = []
            for line in args.changed_files.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                changed.append((parts[0], parts[-1]) if len(parts) > 1 else ("M", parts[0]))
        else:
            if not base_ref or not event_head:
                print("ERROR: git diff 를 계산하려면 --base 와 --head(또는 --event)가 필요하다", file=sys.stderr)
                return 2
            changed = changed_files_from_git(root, base_ref, event_head, problems_holder)

        problems, not_checked = check_pr(
            root, body=body, branch=branch, base_ref=base_ref, changed=changed,
            event_head=event_head, checkout_head=checkout_head if args.event else None,
            online=args.online,
        )
        problems.items = problems_holder.items + problems.items
        if args.as_json:
            print(json.dumps({"ok": not bool(problems), "problems": problems.items,
                              "not_checked": not_checked}, ensure_ascii=False))
        else:
            problems.render()
            for item in not_checked:
                print(f"NOT_CHECKED | {item['check']} — {item['reason']}")
            print("PASS" if not problems else "FAIL")
        return 0 if not problems else 1

    if args.command == "status":
        if args.offline:
            payload = offline_status(root)
            print(json.dumps(payload, ensure_ascii=False) if args.as_json
                  else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["contract_valid"] else 1
        payload, code = online_status(root)
        print(json.dumps(payload, ensure_ascii=False) if args.as_json
              else json.dumps(payload, ensure_ascii=False, indent=2))
        return code

    if args.command == "render":
        problems, context = validate_repo(root)
        print("| ticket | kind | risk | deps | owned | oracle |")
        print("|---|---|---|---|---|---|")
        for tid, ticket in sorted(context["tickets"].items()):
            meta = ticket["meta"]
            print(f"| {tid} | {meta.get('kind')} | {meta.get('risk')} | "
                  f"{','.join(meta.get('dependencies') or []) or '—'} | "
                  f"{len(meta.get('owned_paths') or [])} | {len(meta.get('oracle_paths') or [])} |")
        print(f"\n(projection only — readiness 입력 아님. contract {'OK' if not problems else 'BROKEN'})")
        return 0

    if args.command == "manifest":
        manifest, problems = build_manifest(root)
        if manifest is None:
            problems.render(sys.stderr)
            print("ERROR: contract 가 깨진 상태에서는 manifest 를 만들지 않는다", file=sys.stderr)
            return 1
        text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
            print(f"manifest → {args.output} ({manifest['manifest_digest']})")
        else:
            print(text)
        return 0

    if args.command == "doctor":
        results, failed = doctor(root, args.online)
        for r in results:
            print(f"{r['state']:<12} | {r['probe']} | {r.get('detail', '')}")
        return 1 if failed else 0

    if args.command == "security-scan":
        items, ok = security_scan(root)
        if args.as_json:
            print(json.dumps({"ok": ok, "items": items}, ensure_ascii=False))
        else:
            for item in items:
                print(f"{item['status']:<20} | {item['name']} | {item['detail']}")
            print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
