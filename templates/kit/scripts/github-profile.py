#!/usr/bin/env python3
"""GitHub Free enforcement profile resolver — 계정·visibility 사실에서 profile 을 확정한다.

프로파일 (governance/schemas/github-free-capabilities.v1.json 정본):
  User + public          → FREE_PUBLIC_USER_NATIVE       (native 강제력)
  Organization + public  → FREE_PUBLIC_ORG_NATIVE_QUEUE  (+ merge queue)
  any + private          → FREE_PRIVATE_COMPENSATING     (보완 통제만 — native 주장 금지)

분류 규칙 (절대 위반 금지):
  403 을 기능 미지원으로 단정하지 않는다 · 404 를 plan 제한으로 단정하지 않는다 ·
  API timeout 을 unavailable 로 단정하지 않는다 · public/private 를 같은 profile 로
  처리하지 않는다 · 못 읽은 plan 은 plan_verified=false 로 정직하게 남긴다.

사용법:
  python3 scripts/github-profile.py resolve --repo owner/repo --expected-plan free --json
  python3 scripts/github-profile.py resolve --repo owner/repo --expected-plan free --write-lock governance/github-profile.lock.json
  python3 scripts/github-profile.py verify --root .          # lock ↔ 현재 GitHub 사실 대조

오류 코드: PLAN_MISMATCH · VISIBILITY_MISMATCH · OWNER_TYPE_UNSUPPORTED ·
EXTERNAL_STATE_UNAVAILABLE · AUTHENTICATION_FAILED · PROFILE_LOCK_MISMATCH
종료 코드: 0 profile 확정 / 1 설정·계약 위반 / 2 외부 상태 부족·API 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

SCHEMA_LOCK = "repo-governance.github-profile.lock.v1"


def gh_raw(args: list[str]) -> tuple[int, str, str]:
    binary = os.environ.get("REPO_GOVERNANCE_GH", "gh")
    result = subprocess.run([binary, *args], capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def fail(code: str, message: str, exit_code: int) -> int:
    print(json.dumps({"ok": False, "error": code, "message": message}, ensure_ascii=False))
    return exit_code


def classify_api_error(stderr: str) -> str:
    """403/404/timeout 을 성급하게 해석하지 않는다 — 전부 외부 상태 부족이다."""
    if "HTTP 401" in stderr or "authentication" in stderr.lower():
        return "AUTHENTICATION_FAILED"
    return "EXTERNAL_STATE_UNAVAILABLE"


def build_lock(repo_info: dict, plan: str | None, expected_plan: str) -> dict:
    owner_type = (repo_info.get("owner") or {}).get("type")
    visibility = repo_info.get("visibility")
    if visibility == "private":
        profile = "FREE_PRIVATE_COMPENSATING"
    elif owner_type == "Organization":
        profile = "FREE_PUBLIC_ORG_NATIVE_QUEUE"
    else:
        profile = "FREE_PUBLIC_USER_NATIVE"
    private = visibility == "private"
    return {
        "schema": SCHEMA_LOCK,
        "plan": expected_plan,
        "plan_verified": plan is not None and plan == expected_plan,
        "owner_type": owner_type,
        "repository": repo_info.get("full_name"),
        "visibility": visibility,
        "profile": profile,
        "native_branch_enforcement": not private,
        "native_auto_merge": not private,
        "merge_queue": profile == "FREE_PUBLIC_ORG_NATIVE_QUEUE",
        "assurance_limit": "COMPENSATING_CONTROLS_ONLY" if private else None,
        "resolved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def validate_lock_shape(lock) -> list[str]:
    problems: list[str] = []
    if not isinstance(lock, dict) or lock.get("schema") != SCHEMA_LOCK:
        return [f"schema ≠ {SCHEMA_LOCK}"]
    required = ("plan", "plan_verified", "owner_type", "repository", "visibility",
                "profile", "native_branch_enforcement", "native_auto_merge",
                "merge_queue", "resolved_at")
    problems += [f"누락 필드: {k}" for k in required if k not in lock]
    if problems:
        return problems
    if lock["profile"] not in ("FREE_PUBLIC_USER_NATIVE", "FREE_PUBLIC_ORG_NATIVE_QUEUE",
                               "FREE_PRIVATE_COMPENSATING"):
        problems.append(f"알 수 없는 profile: {lock['profile']}")
    if lock["visibility"] == "private":
        if lock["profile"] != "FREE_PRIVATE_COMPENSATING":
            problems.append("private 인데 compensating profile 이 아니다 — 보증 혼동 금지")
        if lock["native_branch_enforcement"] or lock["native_auto_merge"] or lock["merge_queue"]:
            problems.append("private Free 에서 native 강제력 주장 금지")
        if lock.get("assurance_limit") != "COMPENSATING_CONTROLS_ONLY":
            problems.append("private lock 은 assurance_limit=COMPENSATING_CONTROLS_ONLY 필수")
    elif lock["visibility"] == "public":
        if lock["owner_type"] == "User" and lock["profile"] != "FREE_PUBLIC_USER_NATIVE":
            problems.append("User+public ⇒ FREE_PUBLIC_USER_NATIVE")
        if lock["owner_type"] == "Organization" and lock["profile"] != "FREE_PUBLIC_ORG_NATIVE_QUEUE":
            problems.append("Organization+public ⇒ FREE_PUBLIC_ORG_NATIVE_QUEUE")
        if lock["profile"] == "FREE_PUBLIC_USER_NATIVE" and lock["merge_queue"]:
            problems.append("개인 계정 public 에 merge queue 주장 금지")
    else:
        problems.append(f"visibility ∈ {{public, private}}: {lock['visibility']}")
    return problems


def resolve(repo: str, expected_plan: str):
    """반환 (lock, error_code, message)."""
    code, out, err = gh_raw(["api", f"repos/{repo}"])
    if code:
        return None, classify_api_error(err), f"repos/{repo} 조회 실패 — {err[:200]}"
    try:
        repo_info = json.loads(out)
    except json.JSONDecodeError:
        return None, "EXTERNAL_STATE_UNAVAILABLE", "repository 응답이 JSON 이 아니다"
    if (repo_info.get("full_name") or "").lower() != repo.lower():
        return None, "EXTERNAL_STATE_UNAVAILABLE", \
            f"exact owner/repo 불일치: 요청 {repo} ≠ 응답 {repo_info.get('full_name')}"
    owner_type = (repo_info.get("owner") or {}).get("type")
    if owner_type not in ("User", "Organization"):
        return None, "OWNER_TYPE_UNSUPPORTED", f"owner.type={owner_type!r}"
    if repo_info.get("visibility") not in ("public", "private"):
        return None, "VISIBILITY_MISMATCH", f"visibility={repo_info.get('visibility')!r}"

    code, out, err = gh_raw(["api", "user"])
    if code:
        return None, classify_api_error(err), f"authenticated user 조회 실패 — {err[:200]}"
    me = json.loads(out) if out else {}
    plan = ((me.get("plan") or {}).get("name")) if isinstance(me.get("plan"), dict) else None
    if plan is not None and plan != expected_plan:
        return None, "PLAN_MISMATCH", f"expected {expected_plan} ≠ actual {plan}"

    # Actions 활성 여부 — 못 보면 못 봤다고만 남긴다 (unavailable 단정 금지)
    lock = build_lock(repo_info, plan, expected_plan)
    code, out, _ = gh_raw(["api", f"repos/{repo}/actions/permissions"])
    if not code and out:
        try:
            lock["actions_enabled"] = bool(json.loads(out).get("enabled"))
        except json.JSONDecodeError:
            pass
    lock["default_branch"] = repo_info.get("default_branch")
    return lock, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--repo", required=True)
    p_resolve.add_argument("--expected-plan", default="free")
    p_resolve.add_argument("--expected-visibility", choices=("public", "private"))
    p_resolve.add_argument("--write-lock", type=Path)
    p_resolve.add_argument("--json", action="store_true", dest="as_json")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--root", type=Path, default=Path("."))
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    if args.command == "resolve":
        if args.expected_plan != "free":
            return fail("PLAN_MISMATCH", "이 resolver 는 GitHub Free 검증 기준(2026-08-08)만 지원한다", 1)
        lock, error, message = resolve(args.repo, args.expected_plan)
        if error:
            return fail(error, message, 2 if error in ("EXTERNAL_STATE_UNAVAILABLE",
                                                       "AUTHENTICATION_FAILED") else 1)
        if args.expected_visibility and lock["visibility"] != args.expected_visibility:
            return fail("VISIBILITY_MISMATCH",
                        f"config {args.expected_visibility} ≠ remote {lock['visibility']}", 1)
        problems = validate_lock_shape(lock)
        if problems:
            return fail("PROFILE_INVALID", "; ".join(problems), 1)
        if args.write_lock:
            args.write_lock.parent.mkdir(parents=True, exist_ok=True)
            args.write_lock.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
        print(json.dumps({"ok": True, "lock": lock}, ensure_ascii=False,
                         indent=None if args.as_json else 2))
        return 0

    # verify: 커밋된 lock ↔ 현재 GitHub 사실
    root = args.root.expanduser().resolve()
    lock_path = root / "governance" / "github-profile.lock.json"
    if not lock_path.is_file():
        return fail("PROFILE_LOCK_MISMATCH", f"{lock_path} 없음", 1)
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return fail("PROFILE_LOCK_MISMATCH", f"lock 파싱 실패 — {error}", 1)
    problems = validate_lock_shape(lock)
    if problems:
        return fail("PROFILE_LOCK_MISMATCH", "; ".join(problems), 1)
    current, error, message = resolve(lock["repository"], lock["plan"])
    if error:
        return fail(error, message, 2)
    drift = [
        f"{key}: lock={lock[key]!r} ≠ current={current[key]!r}"
        for key in ("owner_type", "visibility", "profile")
        if lock[key] != current[key]
    ]
    if drift:
        return fail("PROFILE_LOCK_MISMATCH", "; ".join(drift), 1)
    print(json.dumps({"ok": True, "profile": lock["profile"],
                      "plan_verified": current["plan_verified"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
