#!/usr/bin/env python3
"""Phase 완료 판정 v4 — 산출물 + 운영 커널 + GitHub Free profile 을 기계로 검사한다.

repo-factory 는 Phase 0~4만 다룬다. v4 는 GitHub Free 의 public/private 기능
차이를 보증 수준에 그대로 반영한다: public 은 native 강제력을 검증하고, private
는 보완 통제를 검증하며, **두 경우의 보증 수준을 절대 같은 것으로 표시하지 않는다.**

불변식("못 본 것은 못 봤다고 말한다"): 티어가 요구하지 않는 산출물은 SKIP,
--offline 으로 못 본 원격 상태는 NOT_CHECKED, NOT_APPLICABLE 을 PASS 로 표시하지
않는다. gh CLI 부재/인증 실패는 사유와 함께 FAIL 이다.

Assurance ladder (검증하지 않은 상위 수준을 주장하지 않는다):
  DESIGN_ONLY                         게이트 실패
  LOCAL_VERIFIED                      로컬 게이트 전부 PASS
  FREE_PRIVATE_COMPENSATING_VERIFIED  + private canary evidence (native 주장 없음 —
                                        COMPENSATING_CONTROLS_ONLY, 9_9 발급 불가)
  FREE_PUBLIC_NATIVE_VERIFIED         + public canary evidence (active ruleset ·
                                        no bypass · native auto-merge · App-bound merge-gate)
  FREE_PUBLIC_ORG_QUEUE_VERIFIED      + org merge queue evidence
  MULTI_REPO_DOGFOOD_VERIFIED         + dogfood.json (3 repo · 30+ lifecycle · 사고 0)
  9_9_CANDIDATE                       public native + dogfood + drift 0

사용법:
  python3 phase-gate.py 4 --repo owner/name --path /checkout --tier {S,M,L} \
      [--expected-plan free] [--offline] [--json]

`--tier` 기본값은 L. 종료 코드: 0 통과 / 1 사용법 오류 / 2 게이트 실패
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

FEATURE_PRD = re.compile(r"^PRD-(F\d+)-.+\.md$")
FEATURE_TICKET = re.compile(r"^([A-Z][A-Z0-9]{0,7}-\d{3,4})-.+\.md$|^(F\d+)-.+\.md$")
CRITICAL_PATH = re.compile(
    r"^#{1,6}\s+.*(?:critical[ -]path|크리티컬\s*패스)",
    re.IGNORECASE | re.MULTILINE,
)
TIER_ORDER = {"S": 0, "M": 1, "L": 2}
KIT_FILES = (
    "governance/policy.v1.json",
    "governance/factory-lock.json",
    "governance/github-profile.lock.json",
    "governance/actions-lock.v1.json",
    "scripts/governance.py",
    "scripts/autopilot.py",
    "scripts/merge-broker.py",
    "scripts/github-profile.py",
    ".github/workflows/governance.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/agent-review.yml",
    ".github/workflows/security-gate.yml",
    ".github/workflows/post-merge.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
)
PROFILE_LEVELS = {
    "FREE_PUBLIC_USER_NATIVE": "FREE_PUBLIC_NATIVE_VERIFIED",
    "FREE_PUBLIC_ORG_NATIVE_QUEUE": "FREE_PUBLIC_ORG_QUEUE_VERIFIED",
    "FREE_PRIVATE_COMPENSATING": "FREE_PRIVATE_COMPENSATING_VERIFIED",
}


@dataclass(frozen=True)
class Check:
    __slots__ = ("name", "passed", "message")

    name: str
    passed: bool
    message: str


def tier_requires(tier: str, minimum: str) -> bool:
    return TIER_ORDER[tier] >= TIER_ORDER[minimum]


def run_gh(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def run_cmd(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return 127, f"{args[0]}: 실행 파일 없음"
    return result.returncode, (result.stdout + result.stderr).strip()


# -------------------------------------------------------- 산출물 (v2 유지)

def local_checks(path: Path, tier: str) -> tuple[list[Check], list[Check], list[Path]]:
    adr_files = sorted((path / "docs/adr").glob("ADR-*.md"))
    prd_files = sorted((path / "docs/prd").glob("PRD-*.md"))
    ticket_files = sorted(
        ticket for ticket in (path / "docs/tickets").glob("*.md")
        if FEATURE_TICKET.fullmatch(ticket.name)
    )

    prd_features = {
        match.group(1) for prd in prd_files if (match := FEATURE_PRD.fullmatch(prd.name))
    }
    malformed_prds = [prd.name for prd in prd_files if not FEATURE_PRD.fullmatch(prd.name)]
    ticket_features = {t.name.split("-")[0] for t in ticket_files}
    missing_features = sorted(f for f in prd_features if f not in ticket_features)
    ticket_problems = []
    if malformed_prds:
        ticket_problems.append("feature ID 없는 PRD: " + ", ".join(malformed_prds))
    if missing_features:
        ticket_problems.append("티켓 없는 feature: " + ", ".join(missing_features))

    critical_path_files = [
        doc.relative_to(path).as_posix()
        for doc in (path / "docs").rglob("*.md")
        if doc.is_file() and CRITICAL_PATH.search(doc.read_text(encoding="utf-8"))
    ]
    readme_exists = (path / "README.md").is_file()
    contributing_exists = (path / "CONTRIBUTING.md").is_file()
    agents_exists = (path / "AGENTS.md").is_file()
    workflows_dir = path / ".github/workflows"
    ci_workflows = (
        sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
        if workflows_dir.is_dir() else []
    )

    checks = [
        Check("ADR", bool(adr_files), f"{len(adr_files)}개"),
        Check("README.md", readme_exists, "존재" if readme_exists else "없음"),
        Check("AGENTS.md", agents_exists, "존재" if agents_exists else "없음"),
    ]
    skipped: list[Check] = []

    def gate(name: str, minimum: str, passed: bool, message: str, na_reason: str) -> None:
        if tier_requires(tier, minimum):
            checks.append(Check(name, passed, message))
        else:
            skipped.append(Check(name, True, f"tier {tier} 는 요구하지 않음 — {na_reason}"))

    gate("PRD", "S", bool(prd_files), f"{len(prd_files)}개",
         "모든 티어가 최소 PRD를 요구한다")
    gate("기능별 티켓", "S",
         not ticket_problems and bool(prd_files) and bool(ticket_files),
         "; ".join(ticket_problems) if ticket_problems else f"{len(ticket_files)}개",
         "모든 티어가 최소 원자 티켓을 요구한다")
    gate("CONTRIBUTING.md", "M", contributing_exists,
         "존재" if contributing_exists else "없음", "S 규모에서는 만들지 않는다")
    gate("크리티컬 패스", "M", bool(critical_path_files),
         ", ".join(critical_path_files) if critical_path_files else "docs/ 아래에 이름 붙은 섹션 없음",
         "S는 단일 원자 티켓이면 의존성 그래프를 생략할 수 있다")
    gate("CI 스켈레톤", "S", bool(ci_workflows),
         f"{len(ci_workflows)}개" if ci_workflows else ".github/workflows/ 없음 또는 비어 있음",
         "모든 티어가 최소 CI를 요구한다")

    return checks, skipped, ticket_files


# ------------------------------------------------------- 운영 커널 (v3 신규)

def kernel_checks(path: Path) -> list[Check]:
    checks: list[Check] = []
    missing = [f for f in KIT_FILES if not (path / f).is_file()]
    checks.append(Check(
        "governance kit 파일", not missing,
        "전부 존재" if not missing else "누락: " + ", ".join(missing[:4]) + ("…" if len(missing) > 4 else ""),
    ))
    kernel = path / "scripts/governance.py"
    if kernel.is_file():
        code, out = run_cmd([sys.executable, str(kernel), "validate", "--root", str(path)])
        tail = out.splitlines()[-1] if out else ""
        checks.append(Check("governance validate", code == 0, tail[:160]))
    else:
        checks.append(Check("governance validate", False, "scripts/governance.py 없음"))

    compile_errors = []
    for script in sorted((path / "scripts").glob("*.py")) if (path / "scripts").is_dir() else []:
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as error:
            compile_errors.append(f"{script.name}: {error.msg}")
    checks.append(Check("py_compile scripts/", not compile_errors,
                        "; ".join(compile_errors)[:160] if compile_errors else "OK"))

    code, out = run_cmd(["git", "status", "--porcelain"], cwd=path)
    if code:
        checks.append(Check("clean worktree", False, "git status 실패 — git repo 인지 확인"))
    else:
        dirty = [line for line in out.splitlines() if line.strip()]
        checks.append(Check("clean worktree", not dirty,
                            "clean" if not dirty else f"{len(dirty)}개 미커밋 변경"))

    # CommitLore — policy 가 요구할 때만. 도구 부재는 침묵 통과가 아니라 FAIL.
    policy_path = path / "governance/policy.v1.json"
    commitlore_required = False
    if policy_path.is_file():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            commitlore_required = bool((policy.get("commitlore") or {}).get("required_at_genesis"))
        except json.JSONDecodeError:
            pass
    if commitlore_required:
        if shutil.which("commitlore") is None:
            checks.append(Check("commitlore doctor", False, "commitlore 실행 파일 없음"))
        else:
            code, out = run_cmd(["commitlore", "doctor"], cwd=path)
            checks.append(Check("commitlore doctor", code == 0,
                                "exit 0" if code == 0 else (out.splitlines()[-1] if out else f"exit {code}")))
    return checks


# ----------------------------------------------------------------- 원격 상태

def github_checks(repo: str, ticket_files: list[Path], tier: str,
                  profile: str | None = None) -> tuple[list[Check], list[Check]]:
    want_full = tier_requires(tier, "M")
    private = profile == "FREE_PRIVATE_COMPENSATING"
    skipped: list[Check] = []
    if not want_full:
        skipped = [
            Check("마일스톤", True, f"tier {tier} 는 요구하지 않음 — S는 마일스톤 없이 이슈만 만든다"),
            Check("오픈 이슈 마일스톤", True, f"tier {tier} 는 요구하지 않음 — 마일스톤 자체가 없다"),
        ]
    if private:
        skipped.append(Check(
            "dev/main 네이티브 보호", True,
            "NOT_APPLICABLE — GitHub Free private 은 ruleset/protected branch 미지원. "
            "보완 통제(external broker·marker·OOB audit)가 보증하며 PASS 로 표시하지 않는다"))

    check_names = ["원격 dev", "기본 브랜치 dev", "원격 main"]
    if not private:
        check_names += ["dev 보호", "main 보호"]
    if want_full:
        check_names.append("마일스톤")
    check_names.append("이슈")
    if want_full:
        check_names.append("오픈 이슈 마일스톤")
    check_names.append("티켓 이슈 링크")

    if shutil.which("gh") is None:
        message = "gh CLI 없음 — 설치 후 다시 실행"
        return [Check(name, False, message) for name in check_names], skipped
    auth_code, _ = run_gh(["auth", "status"])
    if auth_code:
        message = "gh CLI 인증 실패 — gh auth login 실행"
        return [Check(name, False, message) for name in check_names], skipped

    default_code, default_output = run_gh(["api", f"repos/{repo}", "--jq", ".default_branch"])
    branch_code, branch_output = run_gh(
        ["api", f"repos/{repo}/branches?per_page=100", "--paginate", "--jq", ".[].name"]
    )
    branches = set(branch_output.splitlines()) if not branch_code else set()
    branch_error = (
        "GitHub 조회 실패 — " + (branch_output.splitlines()[-1] if branch_output else repo)
        if branch_code else "없음"
    )
    branch_checks = [
        Check("원격 dev", "dev" in branches, "존재" if "dev" in branches else branch_error),
        Check("기본 브랜치 dev", not default_code and default_output == "dev",
              default_output if not default_code else "GitHub 조회 실패 — " + default_output),
        Check("원격 main", "main" in branches, "존재" if "main" in branches else branch_error),
    ]
    if not private:
        # effective rules API 는 classic protection 과 ruleset 을 모두 본다
        for protected_branch in ("dev", "main"):
            code, out = run_gh(["api", f"repos/{repo}/rules/branches/{protected_branch}"])
            if code:
                branch_checks.append(Check(f"{protected_branch} 보호", False,
                                           "유효 규칙 조회 불가 — " + (out.splitlines()[-1] if out else "")))
                continue
            try:
                rules = json.loads(out)
            except json.JSONDecodeError:
                branch_checks.append(Check(f"{protected_branch} 보호", False, "규칙 응답 파싱 실패"))
                continue
            types = {r.get("type") for r in rules}
            contexts = {
                entry.get("context")
                for r in rules if r.get("type") == "required_status_checks"
                for entry in (r.get("parameters") or {}).get("required_status_checks") or []
            }
            need_types = {"pull_request", "required_status_checks"}
            need_contexts = {"governance", "project-ci"} if protected_branch == "dev" else set()
            missing = sorted(need_types - types) + sorted(need_contexts - contexts)
            branch_checks.append(Check(
                f"{protected_branch} 보호", not missing,
                f"rules={sorted(t for t in types if t)} checks={sorted(c for c in contexts if c)}"
                + (f" — 누락 {missing}" if missing else ""),
            ))

    milestone_checks: list[Check] = []
    if want_full:
        milestone_code, milestone_output = run_gh(
            ["api", f"repos/{repo}/milestones?state=all&per_page=100", "--paginate",
             "--jq", ".[].title"]
        )
        if milestone_code:
            milestone_checks = [Check("마일스톤", False,
                                      "GitHub 조회 실패 — " + (milestone_output.splitlines()[-1] if milestone_output else repo))]
        else:
            milestones = milestone_output.splitlines() if milestone_output else []
            backlog_title = next(
                (t for t in milestones if t.strip().casefold().startswith("backlog")), None)
            detail = ("0개; Backlog 없음" if not milestones
                      else f"{len(milestones)}개" + (f"; {backlog_title}" if backlog_title else "; Backlog 없음"))
            milestone_checks = [Check("마일스톤", bool(milestones) and bool(backlog_title), detail)]

    issue_code, issue_output = run_gh(
        ["issue", "list", "-R", repo, "--state", "all", "--limit", "100000",
         "--json", "body,number,state,milestone"]
    )
    if issue_code:
        message = "GitHub 조회 실패 — " + (issue_output.splitlines()[-1] if issue_output else repo)
        issue_dependent = [Check("티켓 이슈 링크", False, message)]
        if want_full:
            issue_dependent.insert(0, Check("오픈 이슈 마일스톤", False, message))
        return [*branch_checks, *milestone_checks, Check("이슈", False, message), *issue_dependent], skipped

    issues = json.loads(issue_output)
    issue_check = Check("이슈", bool(issues), f"{len(issues)}개")
    bodies = [issue.get("body") or "" for issue in issues]
    unreferenced = [
        t.name for t in ticket_files
        if not any(t.name in body or t.stem.split("-")[0] in body for body in bodies)
    ]
    issue_dependent: list[Check] = []
    if want_full:
        unassigned = [i["number"] for i in issues
                      if i.get("state") == "OPEN" and not i.get("milestone")]
        issue_dependent.append(Check(
            "오픈 이슈 마일스톤", not unassigned,
            "마일스톤 없는 오픈 이슈: " + ", ".join(f"#{n}" for n in unassigned)
            if unassigned else "모든 오픈 이슈에 마일스톤 있음"))
    issue_dependent.append(Check(
        "티켓 이슈 링크", not unreferenced,
        "참조 없는 티켓: " + ", ".join(unreferenced) if unreferenced
        else f"{len(ticket_files)}개 티켓 모두 참조됨"))
    return [*branch_checks, *milestone_checks, issue_check, *issue_dependent], skipped


# ------------------------------------------------------------ assurance level

def load_evidence(path: Path, name: str) -> dict | None:
    """repo 자체 evidence 우선, 없으면 factory-level evidence (환경변수 디렉터리)."""
    candidates = [path / "governance" / "evidence" / name]
    factory_dir = os.environ.get("REPO_FACTORY_EVIDENCE_DIR")
    if factory_dir:
        candidates.append(Path(factory_dir) / name)
    for evidence in candidates:
        if evidence.is_file():
            try:
                return json.loads(evidence.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    return None


def profile_of(path: Path) -> str | None:
    lock_path = path / "governance" / "github-profile.lock.json"
    if not lock_path.is_file():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8")).get("profile")
    except json.JSONDecodeError:
        return None


def assurance_level(path: Path, gate_passed: bool, remote_checked: bool,
                    remote_passed: bool) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not gate_passed:
        return "DESIGN_ONLY", ["게이트 실패 — 상위 수준 주장 금지"]
    level = "LOCAL_VERIFIED"
    if not (remote_checked and remote_passed):
        notes.append("원격 상태 미확인 또는 실패 — LOCAL_VERIFIED 초과 주장 금지")
        return level, notes

    repo_profile_early = profile_of(path)
    candidates = [e for e in (load_evidence(path, "canary.json"),
                              load_evidence(path, "canary-private.json")) if e]
    canary = next((e for e in candidates if e.get("profile") == repo_profile_early),
                  candidates[0] if candidates else None)
    if canary is None:
        notes.append("canary evidence 없음 — live canary 없이 GitHub profile verified 를 주장하지 않는다")
        return level, notes
    if canary.get("schema") == "repo-factory.canary-evidence.v1":
        notes.append("legacy v1 canary evidence — profile 인지 v2 canary 재실행 전에는 profile 수준 미부여")
        return level, notes
    if canary.get("schema") != "repo-factory.canary-evidence.v2":
        notes.append(f"알 수 없는 canary evidence schema: {canary.get('schema')}")
        return level, notes
    steps = canary.get("steps") or {}
    missing = [s for s, v in steps.items() if v is not True]
    if not steps or missing:
        notes.append(f"canary steps 불완전: {missing[:5]}")
        return level, notes
    evidence_profile = canary.get("profile")
    repo_profile = profile_of(path)
    if repo_profile and evidence_profile and repo_profile != evidence_profile:
        notes.append(f"evidence profile {evidence_profile} ≠ repo profile {repo_profile} — 보증 이전 금지")
        return level, notes
    profile_level = PROFILE_LEVELS.get(evidence_profile)
    if profile_level is None:
        notes.append(f"알 수 없는 profile: {evidence_profile}")
        return level, notes
    # merge-gate 는 로컬 controller(운영자 자격증명, target repo 밖)가 생성한다. agent 는
    # GitHub write 자격증명이 0이라 merge-gate 를 애초에 만들 수 없다 — 이것이 핵심 불변식이다.
    if evidence_profile in ("FREE_PUBLIC_USER_NATIVE", "FREE_PUBLIC_ORG_NATIVE_QUEUE"):
        notes.append("merge-gate 는 로컬 controller(운영자 자격증명)가 생성 — agent 는 GitHub "
                     "write 자격증명 0 이라 위조 불가. GitHub 이 ruleset 으로 merge-gate 필수를 강제.")
    level = profile_level
    if evidence_profile == "FREE_PRIVATE_COMPENSATING":
        notes.append("COMPENSATING_CONTROLS_ONLY — GitHub-native enforced/branch-protected/"
                     "unbypassable 주장 금지, 9_9_CANDIDATE 발급 불가")

    dogfood = load_evidence(path, "dogfood.json")
    if dogfood is None:
        notes.append("dogfood evidence 없음 — 실사용 3 repo · 30+ lifecycle 미충족")
        return level, notes
    ok = (
        dogfood.get("schema") == "repo-factory.dogfood-evidence.v1"
        and len(dogfood.get("repos") or []) >= 3
        and int(dogfood.get("total_ticket_lifecycles") or 0) >= 30
        and int(dogfood.get("wrong_target") or 0) == 0
        and int(dogfood.get("unauthorized_merge") or 0) == 0
        and int(dogfood.get("false_verified") or 0) == 0
        and int(dogfood.get("duplicate_pr_or_merge") or 0) == 0
        and int(dogfood.get("wrong_check_source_merge") or 0) == 0
    )
    if not ok:
        notes.append("dogfood evidence 기준 미달 (3 repo · 30 lifecycle · 사고 전항목 0)")
        return level, notes
    if level.startswith("FREE_PUBLIC"):
        if int(dogfood.get("native_enforcement_drift") or 0) == 0:
            return "9_9_CANDIDATE", notes
        notes.append("native enforcement drift > 0 — 9_9_CANDIDATE 미부여")
        return "MULTI_REPO_DOGFOOD_VERIFIED", notes
    notes.append("private profile 은 9_9_CANDIDATE 를 발급하지 않는다")
    return "MULTI_REPO_DOGFOOD_VERIFIED", notes


def main() -> int:
    parser = argparse.ArgumentParser(description="레포의 Phase 4(레포 창세) 완료 여부를 판정한다.")
    parser.add_argument("phase", type=int)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--tier", choices=("S", "M", "L"), default="L",
                        help="이 프로젝트의 규모 티어. 기본값 L(전체 요구)")
    parser.add_argument("--expected-plan", default="free",
                        help="GitHub plan 계약값 (검증 기준일 2026-08-08 은 free 만)")
    parser.add_argument("--offline", action="store_true",
                        help="원격(GitHub) 검사를 NOT_CHECKED 로 남기고 로컬만 판정")
    parser.add_argument("--json", action="store_true", dest="as_json")
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 1

    if args.phase in (5, 6):
        label = "웨이브 실행" if args.phase == 5 else "출하"
        print(f"ERROR: Phase {args.phase}({label})은 repo-factory의 범위 밖이다. "
              "이 스킬은 Phase 0~4까지만 다룬다 — 그 이후는 생성된 레포의 자율 루프"
              "(scripts/autopilot.py)와 프로젝트 자신의 몫이다.", file=sys.stderr)
        return 1
    if args.phase != 4:
        print("ERROR: 현재 지원하는 Phase는 4뿐이다.", file=sys.stderr)
        return 1
    if args.repo.count("/") != 1 or args.repo.startswith("/") or args.repo.endswith("/"):
        print("ERROR: --repo 는 owner/name 형식이어야 한다.", file=sys.stderr)
        return 1
    try:
        path = args.path.expanduser().resolve()
    except OSError as error:
        print(f"ERROR: --path 를 해석할 수 없다 — {error}", file=sys.stderr)
        return 1
    if not path.is_dir():
        print(f"ERROR: 로컬 checkout 디렉터리가 없다: {path}", file=sys.stderr)
        return 1

    try:
        local, local_skipped, ticket_files = local_checks(path, args.tier)
        kernel = kernel_checks(path)
        not_checked: list[Check] = []
        if args.offline:
            remote, remote_skipped = [], []
            not_checked.append(Check("원격 상태", True, "NOT_CHECKED — --offline (통과 아님)"))
        else:
            remote, remote_skipped = github_checks(args.repo, ticket_files, args.tier,
                                                   profile_of(path))
            resolver = path / "scripts" / "github-profile.py"
            if resolver.is_file():
                code, out = run_cmd([sys.executable, str(resolver), "verify", "--root", str(path)])
                tail = (out.splitlines()[-1] if out else "")[:160]
                remote.append(Check("github profile", code == 0, tail))
            else:
                remote.append(Check("github profile", False, "scripts/github-profile.py 없음"))
        checks = local + kernel + remote
        skipped = local_skipped + remote_skipped

        passed = all(check.passed for check in checks)
        remote_checked = not args.offline and bool(remote)
        remote_passed = remote_checked and all(c.passed for c in remote)
        level, level_notes = assurance_level(path, passed, remote_checked, remote_passed)

        if args.as_json:
            print(json.dumps({
                "phase": args.phase, "repo": args.repo, "path": str(path),
                "tier": args.tier, "passed": passed,
                "assurance_level": level, "assurance_notes": level_notes,
                "checks": [asdict(c) for c in checks],
                "skipped": [asdict(c) for c in skipped],
                "not_checked": [asdict(c) for c in not_checked],
            }, ensure_ascii=False))
        else:
            print(f"Phase {args.phase} gate v3 — {args.repo} ({path}) [tier={args.tier}]")
            for check in checks:
                print(f"{'PASS' if check.passed else 'FAIL'} | {check.name} | {check.message}")
            for check in not_checked:
                print(f"NOT_CHECKED | {check.name} | {check.message}")
            if skipped:
                print("---")
                print(f"SKIP (tier {args.tier} 는 요구하지 않음 — PASS/FAIL 집계에 포함 안 됨):")
                for check in skipped:
                    print(f"SKIP | {check.name} | {check.message}")
            print(("PASS" if passed else "FAIL") + f" | assurance: {level}")
            for note in level_notes:
                print(f"  · {note}")
        return 0 if passed else 2
    except Exception as error:  # noqa: BLE001, BROAD_EXCEPT_OK
        print(f"ERROR: Phase {args.phase} 게이트를 실행하지 못했다 — {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
