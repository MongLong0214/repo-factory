#!/usr/bin/env python3
"""Neutral Merge Broker — role-agnostic evidence-gated merge 실행기.

등록된 어떤 에이전트든 merge intent 를 제출할 수 있다. 그러나:
- 누가 요청했는가는 audit 정보일 뿐 authorization 근거가 아니다.
- merge 권한의 유일한 근거 = 현재 exact-head evidence + current policy.
- requester 가 보낸 approved/checks_passed 류 필드는 입력이 아니라 schema 위반이다.
- raw GitHub merge credential 은 에이전트에게 없다. 실행은 GitHub native
  auto-merge/merge queue 또는 로컬 controller(운영자 자격증명, target repo 밖)만 한다.
- branch protection/ruleset bypass 는 어떤 경로로도 하지 않는다.

명령:
  evaluate --root . --intent intent.json (--facts facts.json | --online) [--now RFC3339]
  execute  --root . --intent intent.json (--facts facts.json | --online) [--receipts DIR]

결정: MERGED | QUEUED | DEFERRED | REFUSED | STALE | ALREADY_MERGED | UNKNOWN
- 조건이 덜 찼으면 DEFERRED, 잘못됐으면 REFUSED, facts 불완전이면 UNKNOWN(merge 금지).
- 같은 intent replay 는 duplicate merge 가 아니라 기존 receipt 반환.
종료 코드: 0 (MERGED/QUEUED/ALREADY_MERGED) / 1 (그 외 결정) / 2 사용법·입력 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import governance as gov  # noqa: E402

SCHEMA_INTENT = "repo-governance.merge-intent.v1"
INTENT_REQUIRED = (
    "schema", "request_id", "requester_agent_id", "repository", "pr_number",
    "ticket_id", "operation_id", "expected_head_sha", "expected_base_sha",
    "policy_digest", "requested_at", "expires_at",
)
FORBIDDEN_FIELDS = (
    "approved", "authorized", "checks_passed", "review_passed",
    "safe_to_merge", "green", "mergeable",
)
OK_DECISIONS = ("MERGED", "QUEUED", "ALREADY_MERGED")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def idempotency_key(intent: dict) -> str:
    raw = "|".join(str(intent.get(k, "")) for k in (
        "repository", "pr_number", "operation_id", "expected_head_sha", "policy_digest",
    ))
    return hashlib.sha256(raw.encode()).hexdigest()


def decision(code: str, reason: str, **extra) -> dict:
    return {"decision": code, "reason_code": reason, **extra}


def validate_intent(intent) -> dict | None:
    if not isinstance(intent, dict):
        return decision("REFUSED", "INTENT_NOT_OBJECT")
    present_forbidden = [f for f in FORBIDDEN_FIELDS if f in intent]
    if present_forbidden:
        return decision("REFUSED", "FORBIDDEN_FIELD",
                        detail=f"requester 의 자기 판정 필드는 금지: {present_forbidden} — broker 가 계산한다")
    missing = [k for k in INTENT_REQUIRED if not intent.get(k)]
    if missing:
        return decision("REFUSED", "INTENT_SCHEMA", detail=f"누락 필드: {missing}")
    extra = set(intent) - set(INTENT_REQUIRED)
    if extra:
        return decision("REFUSED", "INTENT_SCHEMA", detail=f"허용되지 않은 필드: {sorted(extra)}")
    if intent["schema"] != SCHEMA_INTENT:
        return decision("REFUSED", "INTENT_SCHEMA", detail=f"schema ≠ {SCHEMA_INTENT}")
    if not isinstance(intent["pr_number"], int) or intent["pr_number"] < 1:
        return decision("REFUSED", "INTENT_SCHEMA", detail="pr_number 는 양의 정수")
    return None


def load_facts_online(policy, intent) -> dict:
    """실 GitHub facts. requester verdict 는 절대 읽지 않는다."""
    repo = intent["repository"]
    pr = gov.gh_json(["api", f"repos/{repo}/pulls/{intent['pr_number']}"])
    head_sha = (pr.get("head") or {}).get("sha")
    base_ref = (pr.get("base") or {}).get("ref")
    branch_tip = gov.gh_json(["api", f"repos/{repo}/branches/{base_ref}"])
    checks = gov.check_runs_for(repo, head_sha) if head_sha else []
    pulls = gov.gh_json(["api", f"repos/{repo}/pulls?state=open&per_page=100", "--paginate"]) or []
    if isinstance(pulls, dict):
        pulls = [pulls]
    open_for_ticket = [
        p["number"] for p in pulls
        if gov.TICKET_LINE_RE.findall(p.get("body") or "") == [intent["ticket_id"]]
    ]
    repo_info = gov.gh_json(["api", f"repos/{repo}"])
    # 공장은 classic protection 이 아니라 ruleset 을 만든다. /rules/branches 는 classic
    # protection 과 ruleset 을 모두 반영하는 effective view 다 — classic /protection API 는
    # ruleset-only repo 에서 404 를 내므로 여기에 쓰면 native public merge 가 영영 막힌다.
    protection_ok = None
    if base_ref:
        try:
            rules = gov.gh_json(["api", f"repos/{repo}/rules/branches/{base_ref}"]) or []
            types = {r.get("type") for r in rules}
            protection_ok = {"pull_request", "required_status_checks"} <= types
        except gov.FactsUnavailable:
            protection_ok = None
    return {
        "repository": repo,
        "pr": {
            "number": pr.get("number"), "state": pr.get("state"),
            "merged": bool(pr.get("merged") or pr.get("merged_at")),
            "head_sha": head_sha, "base_ref": base_ref,
            "base_sha": (pr.get("base") or {}).get("sha"),
            "body": pr.get("body") or "", "draft": bool(pr.get("draft")),
        },
        "base_branch_tip": (branch_tip.get("commit") or {}).get("sha"),
        "checks": [
            {"name": c.get("name"), "head_sha": c.get("head_sha"),
             "conclusion": c.get("conclusion"),
             "app": ((c.get("app") or {}).get("slug") or ""),
             "workflow": ((c.get("check_suite") or {}).get("head_branch") or "")}
            for c in checks
        ],
        "reviews": [],  # 실모드에서 reviewer verdict 는 agent-review check 로 집계된다
        "reviews_source": "check_run",
        "open_prs_for_ticket": open_for_ticket or [intent["pr_number"]],
        "verified_tickets": [],
        "verified_source": "NOT_CHECKED",
        "queue_available": bool((repo_info or {}).get("allow_auto_merge")),
        "branch_protection_ok": protection_ok,
        "active_ownership_overlap": False,
        "budget_exceeded": False,
    }


def evaluate(root: Path, intent: dict, facts: dict | None, now: dt.datetime) -> dict:
    schema_problem = validate_intent(intent)
    if schema_problem:
        return schema_problem

    problems, context = gov.validate_repo(root)
    policy = context["policy"]
    if policy is None:
        return decision("UNKNOWN", "POLICY_UNAVAILABLE", detail="policy SSOT 를 읽을 수 없다 — merge 금지")
    if problems:
        return decision("REFUSED", "CONTRACT_INVALID",
                        detail=f"{len(problems.items)}건 contract 위반 상태에서 merge 하지 않는다")

    # 1. requester 등록 여부 — 역할은 보지 않는다. 등록 identity 만 본다.
    registered = gov.registered_agents(policy)
    if intent["requester_agent_id"] not in registered:
        return decision("REFUSED", "UNREGISTERED_REQUESTER",
                        detail=f"{intent['requester_agent_id']!r} 는 등록된 agent 가 아니다")

    # 2. repository / policy digest 결박
    if intent["repository"] != (policy.get("repository") or {}).get("name"):
        return decision("REFUSED", "REPOSITORY_MISMATCH")
    if intent["policy_digest"] != context["policy_digest"]:
        return decision("STALE", "POLICY_CHANGED",
                        detail=f"intent policy {intent['policy_digest'][:19]}… ≠ current {context['policy_digest'][:19]}…")

    # 3. TTL
    try:
        requested_at = parse_time(intent["requested_at"])
        expires_at = parse_time(intent["expires_at"])
    except ValueError:
        return decision("REFUSED", "INTENT_SCHEMA", detail="requested_at/expires_at 이 RFC3339 가 아니다")
    ttl = (policy.get("merge") or {}).get("intent_ttl_seconds", 600)
    if (expires_at - requested_at).total_seconds() > ttl:
        return decision("REFUSED", "TTL_TOO_LONG", detail=f"intent TTL 은 policy 상 {ttl}s 이하")
    if now > expires_at:
        return decision("STALE", "INTENT_EXPIRED", detail=f"expired {expires_at.isoformat()}")

    # 4. ticket 존재·위임 범위
    ticket = context["tickets"].get(intent["ticket_id"])
    if ticket is None:
        return decision("REFUSED", "TICKET_UNKNOWN")
    meta = ticket["meta"]
    risk = meta.get("risk")
    autonomy = policy["autonomy"]

    if risk not in (autonomy.get("auto_merge") or []):
        return decision("REFUSED", "RISK_NOT_DELEGATED",
                        detail=f"risk={risk} 는 auto_merge 위임 밖 — HUMAN_DECISION_REQUIRED")
    if risk == "high" and not meta.get("predelegated"):
        return decision("REFUSED", "NOT_PREDELEGATED", detail="high 티켓은 predelegated=true 필요")
    if risk == "critical" and autonomy.get("critical_default") != "predelegated":
        return decision("REFUSED", "CRITICAL_HALT", detail="critical_default=halt")

    # private Free + high/critical: custom security lane 없이는 auto-merge 금지
    profile = (context.get("profile") or {}).get("profile")
    if profile == "FREE_PRIVATE_COMPENSATING" and risk in ("high", "critical"):
        commands = policy.get("security_commands") or {}
        missing = [lane for lane in ("sast", "dependency_audit") if not commands.get(lane)]
        if missing:
            return decision("REFUSED", "SECURITY_LANE_MISSING",
                            detail=f"private Free 의 {risk} 티켓은 {missing} command 없이 auto-merge 금지 "
                                   "(없는 scanner 를 있는 것으로 가정하지 않는다)")

    # 5. facts — 없으면 모른다고 말하고 merge 하지 않는다
    if facts is None:
        return decision("UNKNOWN", "FACTS_UNAVAILABLE", detail="current facts 없이는 merge 금지")
    pr = facts.get("pr") or {}
    if pr.get("number") != intent["pr_number"]:
        return decision("REFUSED", "PR_MISMATCH")
    if pr.get("merged"):
        return decision("ALREADY_MERGED", "IDEMPOTENT", detail="PR 은 이미 merge 됐다")
    if pr.get("state") != "open":
        return decision("REFUSED", "PR_NOT_OPEN", detail=f"state={pr.get('state')}")
    if pr.get("draft"):
        return decision("DEFERRED", "PR_DRAFT")

    # 6. exact linkage — Ticket 줄 1개 + operation marker
    ticket_lines = gov.TICKET_LINE_RE.findall(pr.get("body") or "")
    if ticket_lines != [intent["ticket_id"]]:
        return decision("REFUSED", "LINKAGE", detail=f"PR body Ticket 줄 {ticket_lines} ≠ {intent['ticket_id']}")
    marker = gov.OPERATION_MARKER_RE.search(pr.get("body") or "")
    if not marker:
        return decision("REFUSED", "OPERATION_MARKER_MISSING")
    if marker.group("operation") != intent["operation_id"]:
        return decision("REFUSED", "OPERATION_MISMATCH")

    open_prs = facts.get("open_prs_for_ticket") or []
    if len(open_prs) > 1:
        return decision("REFUSED", "MULTIPLE_PRS", detail=f"one ticket one PR 위반: {open_prs}")

    # 7. head/base staleness — 실행 직전 exact 재검사
    if pr.get("head_sha") != intent["expected_head_sha"]:
        return decision("STALE", "HEAD_MOVED",
                        detail=f"head {pr.get('head_sha', '')[:12]} ≠ expected {intent['expected_head_sha'][:12]}")
    base_tip = facts.get("base_branch_tip")
    base_current = base_tip is not None and pr.get("base_sha") == base_tip \
        and intent["expected_base_sha"] == base_tip
    # merge queue 는 organization public 전용이다. allow_auto_merge(개인 public)와
    # 혼동하지 않는다 — 개인 public/private 은 base 가 밀리면 fresh head 에서 재요청한다.
    queue_available = bool(facts.get("queue_available")) \
        and profile == "FREE_PUBLIC_ORG_NATIVE_QUEUE"
    if not base_current:
        if base_tip is None:
            return decision("UNKNOWN", "BASE_TIP_UNAVAILABLE")
        if queue_available and (policy["merge"].get("use_merge_queue_when_available")):
            queued = True
        else:
            return decision("DEFERRED", "BASE_NOT_CURRENT",
                            detail="queue 없음 — exact new head 에서 재검증 후 재요청")
    else:
        queued = False

    # 8. dependencies verified
    deps = meta.get("dependencies") or []
    verified = set(facts.get("verified_tickets") or [])
    if facts.get("verified_source") == "NOT_CHECKED" and deps:
        return decision("UNKNOWN", "DEPS_NOT_CHECKED", detail=f"의존성 {deps} 검증 사실 없음")
    unverified = [d for d in deps if d not in verified]
    if unverified:
        return decision("DEFERRED", "DEPS_UNVERIFIED", detail=str(unverified))

    # 9. required checks — exact head + creator app
    checks_cfg = policy["checks"]
    profiles = policy.get("risk_profiles") or {}
    quorum = int((profiles.get(risk) or {}).get("reviewer_quorum",
                 {"low": 0, "standard": 1, "high": 2, "critical": 2}[risk]))
    required = [checks_cfg["governance"], checks_cfg["project_ci"]]
    if quorum > 0:
        required.append(checks_cfg["review"])
    trusted_apps = {"github-actions", ""}
    check_index: dict[str, dict] = {}
    for c in facts.get("checks") or []:
        if c.get("head_sha") == intent["expected_head_sha"]:
            check_index[c.get("name")] = c
    pending, failed, untrusted = [], [], []
    for name in required:
        run = check_index.get(name)
        if run is None or run.get("conclusion") is None:
            pending.append(name)
        elif run.get("conclusion") != "success":
            failed.append(name)
        elif run.get("app") not in trusted_apps:
            untrusted.append(f"{name}←{run.get('app')}")
    if untrusted:
        return decision("REFUSED", "CHECK_CREATOR_IDENTITY", detail=str(untrusted))
    if failed:
        return decision("REFUSED", "CHECKS_FAILED", detail=str(failed))
    if pending:
        return decision("DEFERRED", "CHECKS_PENDING", detail=str(pending))

    # 10. reviewer quorum — verdict artifact 가 있으면 exact head 결박으로 직접 계산
    if quorum > 0 and facts.get("reviews_source") != "check_run":
        reviewer_ids = set((policy.get("agent_runtime") or {}).get("reviewers") or [])
        on_head = [r for r in facts.get("reviews") or []
                   if r.get("head_sha") == intent["expected_head_sha"]
                   and r.get("reviewer") in reviewer_ids]
        blocks = [r for r in on_head if r.get("verdict") == "BLOCK"]
        passes = {r.get("reviewer") for r in on_head if r.get("verdict") == "PASS"}
        if blocks and passes:
            return decision("REFUSED", "REVIEWER_CONFLICT",
                            detail="reviewer verdict 충돌 — merge 금지")
        if blocks:
            return decision("REFUSED", "REVIEW_BLOCKED")
        unresolved = [
            f for r in on_head for f in r.get("findings") or []
            if f.get("severity") in ("P0", "P1") and not f.get("resolved")
        ]
        if unresolved:
            return decision("REFUSED", "UNRESOLVED_P0_P1", detail=f"{len(unresolved)}건")
        if len(passes) < quorum:
            return decision("DEFERRED", "REVIEW_QUORUM",
                            detail=f"PASS {len(passes)}/{quorum} (exact head 기준)")

    # 11. 나머지 predicate
    if facts.get("active_ownership_overlap"):
        return decision("REFUSED", "OWNERSHIP_OVERLAP")
    if facts.get("budget_exceeded"):
        return decision("REFUSED", "BUDGET_EXCEEDED", detail="quarantine 대상")
    # branch protection 은 GitHub 이 물리적으로 강제하는 native 기능이다. private Free
    # 에는 존재하지 않으므로(그게 profile 의 전제다) merge 게이트로 쓰지 않는다 —
    # 그 경우의 게이트는 external broker + exact-sha(execute) + marker + OOB audit 다.
    # fail-closed: 명시적 private 이 아닌 모든 profile(unknown 포함)은 protection 을 요구한다.
    if profile != "FREE_PRIVATE_COMPENSATING":
        protection_ok = facts.get("branch_protection_ok")
        if protection_ok is None:
            return decision("UNKNOWN", "PROTECTION_NOT_CHECKED")
        if protection_ok is False:
            return decision("REFUSED", "PROTECTION_MISSING",
                            detail="native profile 은 ruleset/branch protection 없이 merge 하지 않는다")

    result = decision(
        "QUEUED" if queued else "MERGED", "EVIDENCE_COMPLETE",
        policy_authorized=True,
        requester_agent_id=intent["requester_agent_id"],
        requester_role_considered=False,
        merge_method="merge_commit",
        head_sha=intent["expected_head_sha"],
        policy_digest=context["policy_digest"],
    )
    return result


def broker_marker(intent: dict) -> str:
    return (f"Repo-Factory-Operation: {intent['operation_id']}\n"
            f"Ticket: {intent['ticket_id']}\n"
            f"Policy-Digest: {intent['policy_digest']}\n"
            f"PR-Head: {intent['expected_head_sha']}")


def gh_run(args: list[str]) -> tuple[int, str, str]:
    """JSON 이 아닌 gh 서브커맨드(pr merge 등)용 — gh_json 을 오용하지 않는다."""
    binary = os.environ.get("REPO_GOVERNANCE_GH", "gh")
    result = subprocess.run([binary, *args], capture_output=True, text=True, check=False)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def execute_native_merge(repo: str, intent: dict, result: dict) -> dict:
    """public native profile: 로컬 controller(운영자 자격증명)가 exact head 에 merge-gate
    를 세우고, ruleset 을 우회하지 않고 native merge/auto-merge 를 요청한다. GitHub 이
    ruleset 요구(required checks·merge-gate)를 그대로 강제한다. agent 는 GitHub write
    자격증명이 0이라 merge-gate 를 만들 수 없다."""
    head = intent["expected_head_sha"]
    try:
        gov.gh_json(["api", "--method", "POST", f"repos/{repo}/statuses/{head}",
                     "-f", "state=success", "-f", "context=merge-gate",
                     "-f", f"description=repo-factory {intent['ticket_id']} {intent['operation_id'][:19]}"])
        result["merge_gate_provenance"] = "commit_status_local_controller"
    except gov.FactsUnavailable as error:
        return decision("UNKNOWN", "MERGE_API_FAILED", detail=f"merge-gate 생성 실패 — {error}")

    code, _, err = gh_run(["pr", "merge", str(intent["pr_number"]), "-R", repo,
                           "--merge", "--auto"])
    if code == 0:
        result["decision"] = "QUEUED"
        result["reason_code"] = "NATIVE_AUTO_MERGE_ENABLED"
        result["executor"] = "github_native_auto_merge"
        return result
    # clean 상태(모든 ruleset 요구 이미 충족)면 auto-merge 가 거절된다 → 일반 merge
    code, _, err2 = gh_run(["pr", "merge", str(intent["pr_number"]), "-R", repo, "--merge"])
    if code == 0:
        result["decision"] = "MERGED"
        result["reason_code"] = "NATIVE_RULESET_SATISFIED"
        result["executor"] = "github_native_merge_ruleset_enforced"
        return result
    return decision("UNKNOWN", "MERGE_API_FAILED", detail=(err or err2)[:300])


def execute(root: Path, intent: dict, facts: dict | None, now: dt.datetime,
            receipts_dir: Path, online: bool) -> dict:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    key = idempotency_key(intent) if isinstance(intent, dict) else None
    receipt_path = receipts_dir / f"{key}.json" if key else None
    if receipt_path and receipt_path.is_file():
        stored = json.loads(receipt_path.read_text(encoding="utf-8"))
        stored["replay"] = True
        return stored

    result = evaluate(root, intent, facts, now)
    if result["decision"] in ("MERGED", "QUEUED"):
        _, context = gov.validate_repo(root)
        profile = (context.get("profile") or {}).get("profile")
        native = profile in ("FREE_PUBLIC_USER_NATIVE", "FREE_PUBLIC_ORG_NATIVE_QUEUE")
        result["profile"] = profile
        result["broker_marker"] = broker_marker(intent)
        if online:
            if native:
                result = execute_native_merge(intent["repository"], intent, result)
            else:
                repo = intent["repository"]
                try:
                    # private compensating: exact-head sha guard + broker marker 로 직접 merge
                    gov.gh_json(["api", "--method", "PUT",
                                 f"repos/{repo}/pulls/{intent['pr_number']}/merge",
                                 "-f", "merge_method=merge",
                                 "-f", f"sha={intent['expected_head_sha']}",
                                 "-f", f"commit_title=Merge {intent['ticket_id']} (PR #{intent['pr_number']})",
                                 "-f", "commit_message=" + broker_marker(intent)])
                    result["executor"] = "local_controller_exact_sha"
                except gov.FactsUnavailable as error:
                    result = decision("UNKNOWN", "MERGE_API_FAILED", detail=str(error))
        else:
            result["executed"] = "simulated"
    result["idempotency_key"] = key
    result["intent_request_id"] = intent.get("request_id") if isinstance(intent, dict) else None
    if receipt_path and result["decision"] in ("MERGED", "QUEUED", "ALREADY_MERGED"):
        # terminal receipt 만 저장 — DEFERRED/UNKNOWN 은 재평가 대상이다
        tmp = receipt_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, receipt_path)
    return result


MARKER_LINE = "Repo-Factory-Operation:"
GENESIS_PREFIXES = ("genesis:", "genesis ")


def audit_commits(root: Path, commits: list[dict]) -> tuple[list[dict], bool]:
    """dev 의 각 commit 이 승인된 경로로 들어왔는지 판정한다.

    private(FREE_PRIVATE_COMPENSATING): merge commit message 에 broker marker 필수.
    public(native): PR 을 통해 들어온 merge 만 허용 (GitHub native enforcement 가
    직접 push 를 이미 차단하므로 여기서는 감사 재확인).
    허용 예외: GENESIS 커밋, 명시 등록된 migration.
    위반: OUT_OF_BAND_WRITE → dependent 중지·quarantine·(policy 에 따라) rollback.
    """
    _, context = gov.validate_repo(root)
    profile = (context.get("profile") or {}).get("profile")
    private = profile == "FREE_PRIVATE_COMPENSATING"
    findings = []
    for commit in commits:
        sha = commit.get("sha", "")
        message = commit.get("message") or ""
        parents = int(commit.get("parents", 1))
        associated_prs = commit.get("associated_prs") or []
        first_line = message.splitlines()[0].lower() if message else ""
        is_genesis_msg = first_line.startswith(GENESIS_PREFIXES)
        if commit.get("registered_migration"):
            verdict = "REGISTERED_MIGRATION"
        elif private:
            # private: broker marker 가 유일한 정당성. genesis 는 메시지로만 예외로 둔다 —
            # parent count 는 shallow checkout(fetch-depth 1)에서 신뢰할 수 없다(실측 canary
            # 버그: OOB direct push 가 grafted root 로 보여 parents=0 → 오탐 GENESIS).
            if MARKER_LINE in message:
                verdict = "OK"
            elif is_genesis_msg:
                verdict = "GENESIS"
            else:
                verdict = "OUT_OF_BAND_WRITE"
        else:
            # public: 진짜 genesis/root(메시지 또는 parents==0, 단 full history 전제) 또는 PR merge
            if is_genesis_msg or parents == 0:
                verdict = "GENESIS"
            elif parents >= 2 and associated_prs:
                verdict = "OK"
            else:
                verdict = "OUT_OF_BAND_WRITE"
        findings.append({"sha": sha, "verdict": verdict,
                         "summary": message.splitlines()[0][:80] if message else ""})
    clean = not any(f["verdict"] == "OUT_OF_BAND_WRITE" for f in findings)
    return findings, clean


def collect_commits_online(repo: str, branch: str, limit: int) -> list[dict]:
    """branch 의 **first-parent 체인**만 감사한다 — merge 로 들어온 worker 커밋
    (2nd parent ancestry)은 push 가 아니므로 OOB 오탐을 내면 안 된다."""
    data = gov.gh_json(["api", f"repos/{repo}/commits?sha={branch}&per_page=100"])
    by_sha = {entry.get("sha"): entry for entry in (data or [])}
    commits = []
    cursor = (data or [{}])[0].get("sha") if data else None
    while cursor and cursor in by_sha and len(commits) < limit:
        entry = by_sha[cursor]
        prs = gov.gh_json(["api", f"repos/{repo}/commits/{cursor}/pulls"]) or []
        parents = entry.get("parents") or []
        commits.append({
            "sha": cursor,
            "message": ((entry.get("commit") or {}).get("message")) or "",
            "parents": len(parents),
            "associated_prs": [p.get("number") for p in prs],
        })
        cursor = parents[0].get("sha") if parents else None
    return commits


def acquire_writer_lock(receipts_dir: Path) -> int | None:
    """repo 당 credentialed merge writer 는 하나다 (CI 에서는 concurrency group 이 보강)."""
    receipts_dir.mkdir(parents=True, exist_ok=True)
    lock = receipts_dir / "writer.lock"
    try:
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("evaluate", "execute"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, default=Path("."))
        p.add_argument("--intent", type=Path, required=True)
        p.add_argument("--facts", type=Path)
        p.add_argument("--online", action="store_true")
        p.add_argument("--now", help="RFC3339 — 테스트/재현용")
        p.add_argument("--receipts", type=Path)
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--root", type=Path, default=Path("."))
    p_audit.add_argument("--facts", type=Path, help='{"commits": [{sha, message, parents, associated_prs}]}')
    p_audit.add_argument("--online", action="store_true")
    p_audit.add_argument("--branch", default="dev")
    p_audit.add_argument("--limit", type=int, default=20)
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    root = args.root.expanduser().resolve()

    if args.command == "audit":
        if args.facts:
            try:
                commits = json.loads(args.facts.read_text(encoding="utf-8")).get("commits") or []
            except (OSError, json.JSONDecodeError) as error:
                print(f"ERROR: audit facts 파싱 실패 — {error}", file=sys.stderr)
                return 2
        elif args.online:
            _, context = gov.validate_repo(root)
            if context["policy"] is None:
                print("ERROR: policy 없음", file=sys.stderr)
                return 2
            try:
                commits = collect_commits_online(
                    context["policy"]["repository"]["name"], args.branch, args.limit)
            except gov.FactsUnavailable as error:
                print(json.dumps({"clean": False, "reason_code": "FACTS_UNAVAILABLE",
                                  "detail": str(error)}, ensure_ascii=False))
                return 1
        else:
            print("ERROR: --facts 또는 --online 필요", file=sys.stderr)
            return 2
        findings, clean = audit_commits(root, commits)
        oob = [f for f in findings if f["verdict"] == "OUT_OF_BAND_WRITE"]
        print(json.dumps({"clean": clean, "findings": findings,
                          "action_required": (
                              "dependent 중지 · quarantine · rollback ticket(autopilot rollback) 또는 "
                              "HUMAN_DECISION_REQUIRED (policy.autonomy.auto_revert_out_of_band)"
                          ) if oob else None}, ensure_ascii=False, indent=2))
        return 0 if clean else 1
    try:
        intent = json.loads(args.intent.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: intent 파싱 실패 — {error}", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)

    facts = None
    if args.facts:
        try:
            facts = json.loads(args.facts.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"ERROR: facts 파싱 실패 — {error}", file=sys.stderr)
            return 2
    elif args.online:
        _, context = gov.validate_repo(root)
        if context["policy"] is not None and validate_intent(intent) is None:
            try:
                facts = load_facts_online(context["policy"], intent)
            except gov.FactsUnavailable as error:
                facts = None
                print(f"NOTE: facts 조회 실패 — {error}", file=sys.stderr)

    receipts_dir = args.receipts or (root / ".governance-broker")
    if args.command == "evaluate":
        result = evaluate(root, intent, facts, now)
    else:
        lock_fd = acquire_writer_lock(receipts_dir)
        if lock_fd is None:
            result = decision("DEFERRED", "SINGLE_WRITER_BUSY", detail="다른 broker 실행 중 — 재시도")
        else:
            try:
                result = execute(root, intent, facts, now, receipts_dir, args.online)
            finally:
                os.close(lock_fd)
                (receipts_dir / "writer.lock").unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] in OK_DECISIONS else 1


if __name__ == "__main__":
    sys.exit(main())
