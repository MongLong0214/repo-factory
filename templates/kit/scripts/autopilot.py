#!/usr/bin/env python3
"""Autopilot controller — Policy-Delegated Execution Authority 실행기.

할 수 있는 것: ready ticket claim, 승인된 worker/reviewer adapter 호출, ticket
branch 생성·재사용, PR 생성·갱신, exact-head evidence 수집, merge intent 제출,
post-merge 실패 시 rollback ticket 생성, 재시도·격리·정지.
할 수 없는 것: policy/oracle/kernel 수정, branch protection 우회, 사람 승인 가장.

이벤트를 순서대로 믿지 않는다 — 매 실행마다 source of truth 를 전수 재계산한다
(reconciliation). 중복/역순/누락 webhook 이 있어도 결과가 같아야 한다.

명령:
  reconcile     --root . (--offline | --online)
  run-once      --root . (--offline | --online) [--verified IDS] [--dispatch]
  dispatch      --root . --ticket F1-001 [--verified IDS]
  request-merge --root . --ticket F1-001 --pr N --requester ID --head SHA --base SHA
                [--submit --facts F | --submit --online] [--now RFC3339]
  review        --root . --event event.json [--out DIR]
  recover       --root . [--now RFC3339]
  rollback      --root . --ticket F1-001 [--reason TEXT]

종료 코드: 0 성공 / 1 정지·격리·검증 실패 / 2 사용법·입력 오류
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import governance as gov  # noqa: E402

LEASE_DIR = ".autopilot/leases"
FAILURE_CLASSES = ("TRANSIENT", "REPAIRABLE", "CONTRACT", "SECURITY", "POLICY")

# 순서가 의미다: 테스트 assertion 실패를 transient 로 숨기지 않는다.
FAILURE_RULES = (
    ("SECURITY", re.compile(r"(secret|credential leak|prompt injection|unauthorized write)", re.I)),
    ("CONTRACT", re.compile(r"(unowned|ownership|self[- ]expansion|oracle modif|kernel touched|contract)", re.I)),
    ("POLICY", re.compile(r"(not delegated|predelegat|human_decision|policy gap|critical_halt)", re.I)),
    ("REPAIRABLE", re.compile(r"(assert(ion)?(error)?|test.* fail|FAILED|expect(ed)? .* but|exit code 1)", re.I)),
    ("TRANSIENT", re.compile(r"(429|rate ?limit|timed? ?out|timeout|ECONNRESET|ETIMEDOUT|50[023]|service unavailable|runner lost|cancell?ed|network)", re.I)),
)
CI_RERUN_ALLOWED = re.compile(r"(cancell?ed|runner lost|platform outage|infrastructure timeout)", re.I)


def classify_failure(text: str) -> str:
    for label, pattern in FAILURE_RULES:
        if pattern.search(text or ""):
            return label
    return "REPAIRABLE"


def operation_id(repository: str, ticket_id: str, dependency_verified_heads: dict[str, str],
                 policy_digest: str) -> str:
    raw = gov.canonical_bytes({
        "repository": repository, "ticket_id": ticket_id,
        "dependency_verified_heads": dependency_verified_heads,
        "policy_digest": policy_digest,
    })
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def operation_marker(ticket_id: str, op_id: str, base_sha: str, policy_digest: str) -> str:
    return (
        "<!-- repo-governance-operation:\n"
        f"ticket={ticket_id}\noperation={op_id}\nbase={base_sha}\npolicy={policy_digest}\n-->"
    )


def load_context_or_die(root: Path):
    problems, context = gov.validate_repo(root)
    if context["policy"] is None:
        print("ERROR: policy SSOT 없음 — autopilot 은 policy 없이 아무것도 하지 않는다", file=sys.stderr)
        raise SystemExit(2)
    return problems, context


# ------------------------------------------------------------ lease / claim

def lease_path(root: Path, ticket_id: str) -> Path:
    return root / LEASE_DIR / f"{ticket_id}.json"


def read_lease(root: Path, ticket_id: str) -> dict | None:
    path = lease_path(root, ticket_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_lease(root: Path, ticket_id: str, lease: dict) -> None:
    path = lease_path(root, ticket_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lease, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------- adapters

def load_adapter(root: Path, adapter_id: str) -> dict | None:
    path = root / "governance" / "adapters" / f"{adapter_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def adapter_wiring(root: Path) -> dict:
    """설치된 어댑터가 실제로 실행 가능한지. 미배선은 침묵 통과 대상이 아니다.

    install-governance 는 invoke 를 {execute: null, review: null, repair: null} 로
    깔고 운영자가 채우기를 기대한다. 채우기 전까지 dispatch 는 invoke_adapter 에서
    exit 2 로 끝나는데, reconcile 출력에는 그 사실이 드러나지 않아
    "startable 이 있는데 아무 일도 안 일어난다"로만 보였다.

    phase-gate 가 "도구 부재는 침묵 통과가 아니라 FAIL" 로 다루는 것과 같은 규칙을
    실행 어댑터에도 적용한다.
    """
    adapters_dir = root / "governance" / "adapters"
    found = sorted(p.stem for p in adapters_dir.glob("*.json")) if adapters_dir.is_dir() else []
    if not found:
        return {"state": "MISSING", "adapters": [], "unwired": [],
                "detail": "governance/adapters 에 어댑터가 없다 — dispatch 는 실행되지 않는다"}
    unwired = []
    for aid in found:
        adapter = load_adapter(root, aid) or {}
        invoke = adapter.get("invoke") or {}
        missing = sorted(op for op in ("execute", "review", "repair") if not invoke.get(op))
        if missing:
            unwired.append({"adapter": aid, "operations": missing})
    if not unwired:
        return {"state": "WIRED", "adapters": found, "unwired": []}
    blocked = any("execute" in u["operations"] for u in unwired)
    return {
        "state": "UNWIRED" if blocked else "PARTIAL",
        "adapters": found,
        "unwired": unwired,
        "detail": ("execute 가 비어 있어 dispatch 가 아무것도 실행하지 않는다"
                   if blocked else "일부 operation 만 배선됐다"),
    }


def invoke_adapter(root: Path, adapter: dict, operation: str, payload: dict,
                   timeout_seconds: int = 3600) -> tuple[int, dict | None, str]:
    argv = ((adapter.get("invoke") or {}).get(operation))
    if not argv:
        return 2, None, f"adapter {adapter.get('id')} 는 {operation} 을 지원하지 않는다"
    try:
        result = subprocess.run(
            argv, input=json.dumps(payload, ensure_ascii=False), cwd=root,
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, None, str(error)
    output = None
    if result.stdout.strip():
        try:
            output = json.loads(result.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            output = None
    return result.returncode, output, result.stderr.strip()


WORKER_OUTPUT_REQUIRED = ("status", "operation_id", "ticket_id", "base_sha",
                          "head_sha", "branch", "changed_paths", "commands_run", "evidence")


def validate_worker_output(output) -> list[str]:
    """자연어 보고만으로 완료 인정 금지 — 기계 필드가 evidence 다."""
    if not isinstance(output, dict):
        return ["worker output 이 JSON 객체가 아니다"]
    return [f"누락 필드: {k}" for k in WORKER_OUTPUT_REQUIRED if k not in output]


# -------------------------------------------------------------- reconcile 류

def compute_ready(context, verified: set[str]) -> list[str]:
    tickets = context["tickets"]
    invalidated = {ref for t in tickets.values() for ref in t["meta"].get("invalidates") or []}
    superseded = {ref for t in tickets.values() for ref in t["meta"].get("supersedes") or []}
    ready = []
    for tid, ticket in sorted(tickets.items()):
        meta = ticket["meta"]
        if tid in invalidated or tid in superseded or tid in verified:
            continue
        deps = meta.get("dependencies") or []
        # dependent 는 의존성이 post-merge verified 되기 전에는 시작되지 않는다
        if all(dep in verified for dep in deps):
            ready.append(tid)
    return ready


def open_tickets(context, verified: set[str]) -> dict[str, dict]:
    """아직 살아 있고 검증되지 않은 티켓. compute_ready 와 같은 제외 규칙을 쓴다."""
    tickets = context["tickets"]
    dropped = {ref for t in tickets.values()
               for key in ("invalidates", "supersedes")
               for ref in t["meta"].get(key) or []}
    return {tid: t for tid, t in tickets.items()
            if tid not in dropped and tid not in verified}


def compute_blocked(context, verified: set[str]) -> list[dict]:
    """시작할 수 없는 티켓과 그것이 기다리는 대상.

    reconcile 이 ready/startable/held 만 내면 "아무것도 안 도는" 상태에서
    원인을 알 수 없다. held 는 ready 인 것 중 위험도로 막힌 것이라,
    의존성 때문에 대기 중인 티켓은 어느 목록에도 나타나지 않았다.
    """
    blocked = []
    for tid, ticket in sorted(open_tickets(context, verified).items()):
        waiting = [d for d in ticket["meta"].get("dependencies") or [] if d not in verified]
        if waiting:
            blocked.append({"ticket": tid, "waiting_on": sorted(waiting)})
    return blocked


def critical_path(context, verified: set[str]) -> dict:
    """남은 티켓 중 가장 긴 의존 사슬. 최소 몇 라운드가 남았는지를 뜻한다.

    병렬 폭이 아니라 이 depth 가 남은 소요를 결정한다. 사이클은 governance 의
    TICKET_DAG_CYCLE 이 잡지만, 그 검사를 건너뛴 입력에서도 여기서 멈추지 않도록
    방문 중 노드를 만나면 그 가지를 끊는다.
    """
    remaining = open_tickets(context, verified)
    memo: dict[str, list[str]] = {}

    def longest(tid: str, visiting: frozenset[str]) -> list[str]:
        if tid in memo:
            return memo[tid]
        if tid in visiting:            # 사이클 — 이 가지는 더 세지 않는다
            return []
        best: list[str] = []
        for dep in remaining.get(tid, {}).get("meta", {}).get("dependencies") or []:
            if dep not in remaining:   # 이미 verified 이거나 폐기됨
                continue
            chain = longest(dep, visiting | {tid})
            if len(chain) > len(best):
                best = chain
        result = best + [tid]
        memo[tid] = result
        return result

    deepest: list[str] = []
    for tid in sorted(remaining):
        chain = longest(tid, frozenset())
        if len(chain) > len(deepest):
            deepest = chain
    return {"depth": len(deepest), "chain": deepest}


def cmd_run_once(root: Path, online: bool, verified: set[str], dispatch: bool) -> int:
    problems, context = load_context_or_die(root)
    if problems:
        print(json.dumps({"state": "human_decision_required",
                          "reason_code": "CONTRACT_INVALID",
                          "problems": problems.items[:10]}, ensure_ascii=False))
        return 1
    policy = context["policy"]
    autonomy = policy["autonomy"]
    if online:
        payload, code = gov.online_status(root)
        if code:
            print(json.dumps({"state": "unknown", "reason_code": "FACTS_UNAVAILABLE",
                              "detail": payload.get("reason")}, ensure_ascii=False))
            return 1
        verified = verified | {
            tid for tid, s in (payload.get("tickets") or {}).items()
            if s.get("technical_state") == "verified"
        }
    ready = compute_ready(context, verified)
    startable, held = [], []
    for tid in ready:
        meta = context["tickets"][tid]["meta"]
        risk = meta.get("risk")
        if risk == "critical" and autonomy.get("critical_default") != "predelegated":
            held.append({"ticket": tid, "reason_code": "CRITICAL_HALT"})
        elif risk not in (autonomy.get("auto_start") or []):
            held.append({"ticket": tid, "reason_code": "RISK_NOT_DELEGATED", "risk": risk})
        elif risk == "high" and not meta.get("predelegated"):
            held.append({"ticket": tid, "reason_code": "NOT_PREDELEGATED"})
        else:
            startable.append(tid)
    wip_cap = policy["wip"]["max_active_tickets"]
    active_leases = [p.stem for p in (root / LEASE_DIR).glob("*.json")] if (root / LEASE_DIR).is_dir() else []
    budget = max(0, wip_cap - len(active_leases))
    remaining = open_tickets(context, verified)
    plan = {
        "state": "reconciled",
        "verified": sorted(verified),
        "ready": ready,
        "startable": startable[:budget],
        "held": held,
        # 의존성 대기는 ready 에도 held 에도 안 잡힌다. 셋 다 비면 원인을 알 수 없었다.
        "blocked": compute_blocked(context, verified),
        "progress": {
            "verified": len(verified),
            "remaining": len(remaining),
            "total": len(verified) + len(remaining),
        },
        # 남은 최장 의존 사슬 = 최소 잔여 라운드 수. 병렬 폭이 아니라 이것이 소요를 정한다.
        "critical_path": critical_path(context, verified),
        # 어댑터가 미배선이면 startable 이 있어도 dispatch 는 아무것도 실행하지 않는다.
        "adapter": adapter_wiring(root),
        "wip": {"cap": wip_cap, "active": active_leases},
        "dispatched": [],
    }
    if dispatch:
        for tid in plan["startable"]:
            code = cmd_dispatch(root, tid, verified, quiet=True)
            plan["dispatched"].append({"ticket": tid, "exit": code})
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_dispatch(root: Path, ticket_id: str, verified: set[str], quiet: bool = False) -> int:
    problems, context = load_context_or_die(root)
    if problems:
        print("ERROR: contract invalid — dispatch 금지", file=sys.stderr)
        return 1
    ticket = context["tickets"].get(ticket_id)
    if ticket is None:
        print(f"ERROR: 티켓 {ticket_id} 없음", file=sys.stderr)
        return 2
    meta = ticket["meta"]
    policy = context["policy"]
    deps = meta.get("dependencies") or []
    unverified = [d for d in deps if d not in verified]
    if unverified:
        print(f"ERROR: 의존성 미검증 {unverified} — ready 가 아니다", file=sys.stderr)
        return 1

    dep_heads = {d: "verified" for d in deps}
    op_id = operation_id(policy["repository"]["name"], ticket_id, dep_heads, context["policy_digest"])
    existing = read_lease(root, ticket_id)
    if existing and existing.get("operation_id") == op_id:
        # 같은 operation 의 rerun 은 기존 branch/PR 을 resume 한다. duplicate 생성 금지.
        if not quiet:
            print(json.dumps({"resumed": True, "lease": existing}, ensure_ascii=False))
        return 0

    slug = re.sub(r"[^a-z0-9]+", "-", str(meta.get("title", "")).lower()).strip("-")[:24] or "work"
    prefix = {"implementation": "feat", "contract-change": "contract",
              "governance-change": "governance", "rollback": "revert"}[meta["kind"]]
    branch = f"{prefix}/{ticket_id}-{slug}"
    lease = {
        "ticket_id": ticket_id, "operation_id": op_id, "branch": branch,
        "attempt": (existing or {}).get("attempt", 0) + 1,
        "repair_rounds": 0,
        "claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "heartbeat_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "worker_run_id": str(uuid.uuid4()),
        "ttl_seconds": int(policy["autonomy"]["max_ticket_wall_minutes"]) * 60,
    }
    write_lease(root, ticket_id, lease)

    adapter = load_adapter(root, policy["agent_runtime"]["worker"])
    if adapter is None:
        print("ERROR: worker adapter 없음 — EXTERNAL_CAPABILITY_MISSING", file=sys.stderr)
        return 1
    packet = {
        "operation_id": op_id,
        "repository": policy["repository"]["name"],
        "ticket_id": ticket_id,
        "ticket_digest": gov.digest_obj(meta),
        "policy_digest": context["policy_digest"],
        "base_sha": None,
        "branch": branch,
        "owned_paths": meta.get("owned_paths") or [],
        "coordinated_paths": meta.get("coordinated_paths") or [],
        "oracle_paths": meta.get("oracle_paths") or [],
        "commands": meta.get("commands") or {},
        "budgets": meta.get("budgets") or {},
    }
    code, output, stderr = invoke_adapter(root, adapter, "execute", packet)
    if code:
        label = classify_failure(stderr)
        limit = (policy["autonomy"]["max_transient_retries"] if label == "TRANSIENT"
                 else policy["autonomy"]["max_repair_rounds"])
        exceeded = lease["attempt"] > limit
        state = "quarantined" if exceeded or label == "SECURITY" else \
            "human_decision_required" if label == "POLICY" else \
            "blocked" if label == "CONTRACT" else "repair"
        print(json.dumps({"ticket": ticket_id, "state": state, "failure_class": label,
                          "attempt": lease["attempt"], "limit": limit,
                          "stderr_tail": stderr[-400:]}, ensure_ascii=False))
        return 1
    errors = validate_worker_output(output)
    if errors:
        print(json.dumps({"ticket": ticket_id, "state": "repair",
                          "reason_code": "WORKER_OUTPUT_INVALID", "errors": errors},
                         ensure_ascii=False))
        return 1
    if not quiet:
        print(json.dumps({"ticket": ticket_id, "state": "executed",
                          "operation_id": op_id, "worker": output}, ensure_ascii=False))
    return 0


def cmd_request_merge(root: Path, ticket_id: str, pr_number: int, requester: str,
                      head_sha: str, base_sha: str, now: str | None,
                      submit: bool, facts_path: Path | None, online: bool) -> int:
    problems, context = load_context_or_die(root)
    policy = context["policy"]
    ticket = context["tickets"].get(ticket_id)
    if ticket is None:
        print(f"ERROR: 티켓 {ticket_id} 없음", file=sys.stderr)
        return 2
    deps = ticket["meta"].get("dependencies") or []
    op_id = operation_id(policy["repository"]["name"], ticket_id,
                         {d: "verified" for d in deps}, context["policy_digest"])
    requested = (dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
                 if now else dt.datetime.now(dt.timezone.utc))
    ttl = policy["merge"]["intent_ttl_seconds"]
    intent = {
        "schema": "repo-governance.merge-intent.v1",
        "request_id": str(uuid.uuid4()),
        "requester_agent_id": requester,
        "repository": policy["repository"]["name"],
        "pr_number": pr_number,
        "ticket_id": ticket_id,
        "operation_id": op_id,
        "expected_head_sha": head_sha,
        "expected_base_sha": base_sha,
        "policy_digest": context["policy_digest"],
        "requested_at": requested.isoformat().replace("+00:00", "Z"),
        "expires_at": (requested + dt.timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z"),
    }
    intent_path = root / ".autopilot" / f"intent-{ticket_id}-{pr_number}.json"
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    intent_path.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
    if not submit:
        print(json.dumps({"intent": intent, "path": str(intent_path)}, ensure_ascii=False, indent=2))
        return 0
    broker = Path(__file__).resolve().parent / "merge-broker.py"
    argv = [sys.executable, str(broker), "execute", "--root", str(root),
            "--intent", str(intent_path)]
    if facts_path:
        argv += ["--facts", str(facts_path)]
    if online:
        argv += ["--online"]
    if now:
        argv += ["--now", now]
    result = subprocess.run(argv, check=False)
    return result.returncode


def cmd_review(root: Path, event_path: Path, out_dir: Path | None) -> int:
    problems, context = load_context_or_die(root)
    policy = context["policy"]
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: event 파싱 실패 — {error}", file=sys.stderr)
        return 2
    pr = event.get("pull_request") or {}
    body = pr.get("body") or ""
    ids = gov.TICKET_LINE_RE.findall(body)
    if len(ids) != 1 or ids[0] not in context["tickets"]:
        print("ERROR: PR 의 Ticket 결박이 유효하지 않다", file=sys.stderr)
        return 1
    ticket = context["tickets"][ids[0]]
    risk = ticket["meta"].get("risk")
    quorum = int((policy["risk_profiles"].get(risk) or {}).get(
        "reviewer_quorum", {"low": 0, "standard": 1, "high": 2, "critical": 2}[risk]))
    if quorum == 0:
        print(json.dumps({"verdicts": [], "quorum": 0, "result": "PASS"}, ensure_ascii=False))
        return 0
    head_sha = (pr.get("head") or {}).get("sha")
    packet = {
        "ticket_digest": gov.digest_obj(ticket["meta"]),
        "policy_digest": context["policy_digest"],
        "base_sha": (pr.get("base") or {}).get("sha"),
        "head_sha": head_sha,
        # reviewer 입력은 immutable digest — worker transcript 는 어떤 필드로도 넘기지 않는다
    }
    verdicts = []
    for reviewer_id in policy["agent_runtime"]["reviewers"][:max(quorum, 1)]:
        adapter = load_adapter(root, reviewer_id)
        if adapter is None:
            verdicts.append({"reviewer": reviewer_id, "verdict": "UNAVAILABLE"})
            continue
        code, output, stderr = invoke_adapter(root, adapter, "review", packet)
        if code or not isinstance(output, dict) or output.get("verdict") not in ("PASS", "REVISE", "BLOCK"):
            verdicts.append({"reviewer": reviewer_id, "verdict": "UNAVAILABLE", "stderr": stderr[-200:]})
            continue
        if output.get("head_sha") != head_sha:
            verdicts.append({"reviewer": reviewer_id, "verdict": "STALE"})
            continue
        verdicts.append({"reviewer": reviewer_id, "verdict": output["verdict"],
                         "findings": output.get("findings") or [], "head_sha": head_sha})
    passes = [v for v in verdicts if v["verdict"] == "PASS"]
    blocks = [v for v in verdicts if v["verdict"] == "BLOCK"]
    result = "PASS" if len(passes) >= quorum and not blocks else "FAIL"
    payload = {"quorum": quorum, "verdicts": verdicts, "result": result, "head_sha": head_sha}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "agent-review.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result == "PASS" else 1


def cmd_recover(root: Path, now: str | None) -> int:
    current = (dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
               if now else dt.datetime.now(dt.timezone.utc))
    lease_dir = root / LEASE_DIR
    report = []
    for path in sorted(lease_dir.glob("*.json")) if lease_dir.is_dir() else []:
        try:
            lease = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report.append({"lease": path.name, "action": "quarantine", "reason_code": "LEASE_CORRUPT"})
            continue
        heartbeat = dt.datetime.fromisoformat(str(lease.get("heartbeat_at")).replace("Z", "+00:00"))
        expired = (current - heartbeat).total_seconds() > int(lease.get("ttl_seconds", 3600))
        # lease 만료 ≠ 새 PR — 기존 branch/PR 을 inspect 후 resume 또는 quarantine
        report.append({
            "lease": path.name, "ticket": lease.get("ticket_id"),
            "expired": expired,
            "action": "inspect_and_resume" if expired else "keep",
            "branch": lease.get("branch"), "operation_id": lease.get("operation_id"),
        })
    print(json.dumps({"now": current.isoformat(), "leases": report}, ensure_ascii=False, indent=2))
    return 0


def cmd_rollback(root: Path, ticket_id: str, reason: str) -> int:
    problems, context = load_context_or_die(root)
    target = context["tickets"].get(ticket_id)
    if target is None:
        print(f"ERROR: 티켓 {ticket_id} 없음", file=sys.stderr)
        return 2
    meta = target["meta"]
    seq = 1
    while any(t.startswith(f"R{seq}-") for t in context["tickets"]):
        seq += 1
    rollback_id = f"R{seq}-001"
    rollback_meta = {
        "schema": "repo-governance.ticket.v1",
        "id": rollback_id,
        "title": f"Rollback {ticket_id}: {reason or 'post-merge failure'}",
        "kind": "rollback",
        "risk": meta.get("risk", "standard"),
        "predelegated": bool(meta.get("predelegated")),
        "milestone": None,
        "dependencies": [],
        "adr_refs": [],
        "prd_ref": None,
        "owned_paths": meta.get("owned_paths") or [],
        "coordinated_paths": [],
        "oracle_paths": [],
        "acceptance": [],
        "commands": {"focused": None, "full": (context["policy"].get("project_commands") or {}).get("full"),
                     "build": (context["policy"].get("project_commands") or {}).get("build"),
                     "lint": None, "typecheck": None,
                     "manual": "post-merge CI green on revert merge"},
        "budgets": {"repair_rounds": 1, "wall_minutes": 60, "external_cost": None},
        "invalidates": [ticket_id],
        "supersedes": [],
    }
    body = (
        f"# {rollback_id} — Rollback {ticket_id}\n\n"
        "<!-- repo-governance-ticket:v1\n"
        + json.dumps(rollback_meta, ensure_ascii=False, indent=2)
        + "\n-->\n\n"
        f"## 목적\npost-merge 실패한 {ticket_id} 의 exact merge 를 revert 한다.\n\n"
        f"## 이유\n{reason or '(post-merge CI failure)'}\n\n"
        "## 절차\n1. revert 브랜치 생성 (`revert/" + rollback_id + "-rollback`)\n"
        "2. exact failing merge commit 을 `git revert -m 1` 로 되돌린다\n"
        "3. governance/project CI green\n"
        "4. 정책 범위 내 자동 merge → 원 티켓 invalidated\n\n"
        "## 금지\nprotected branch 직접 revert push 금지. dependents 는 post-merge verified 전 시작 금지.\n"
    )
    out = root / "docs" / "tickets" / f"{rollback_id}-rollback-{ticket_id.lower()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(json.dumps({"rollback_ticket": rollback_id, "path": str(out),
                      "invalidates": [ticket_id]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--root", type=Path, default=Path("."))

    p = sub.add_parser("reconcile"); common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--offline", action="store_true"); g.add_argument("--online", action="store_true")

    p = sub.add_parser("run-once"); common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--offline", action="store_true"); g.add_argument("--online", action="store_true")
    p.add_argument("--verified", default="", help="쉼표 구분 verified ticket IDs (offline 재현용)")
    p.add_argument("--dispatch", action="store_true")

    p = sub.add_parser("dispatch"); common(p)
    p.add_argument("--ticket", required=True)
    p.add_argument("--verified", default="")

    p = sub.add_parser("request-merge"); common(p)
    p.add_argument("--ticket", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--requester", required=True, dest="requester")
    p.add_argument("--head", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--now")
    p.add_argument("--submit", action="store_true")
    p.add_argument("--facts", type=Path)
    p.add_argument("--online", action="store_true")

    p = sub.add_parser("review"); common(p)
    p.add_argument("--event", type=Path, required=True)
    p.add_argument("--out", type=Path)

    p = sub.add_parser("recover"); common(p)
    p.add_argument("--now")

    p = sub.add_parser("rollback"); common(p)
    p.add_argument("--ticket", required=True)
    p.add_argument("--reason", default="")

    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: --root 없음: {root}", file=sys.stderr)
        return 2

    if args.command == "reconcile":
        if args.offline:
            payload = gov.offline_status(root)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["contract_valid"] else 1
        payload, code = gov.online_status(root)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return code
    if args.command == "run-once":
        verified = {v for v in args.verified.split(",") if v}
        return cmd_run_once(root, args.online, verified, args.dispatch)
    if args.command == "dispatch":
        verified = {v for v in args.verified.split(",") if v}
        return cmd_dispatch(root, args.ticket, verified)
    if args.command == "request-merge":
        return cmd_request_merge(root, args.ticket, args.pr, args.requester,
                                 args.head, args.base, args.now, args.submit,
                                 args.facts, args.online)
    if args.command == "review":
        return cmd_review(root, args.event, args.out)
    if args.command == "recover":
        return cmd_recover(root, args.now)
    if args.command == "rollback":
        return cmd_rollback(root, args.ticket, args.reason)
    return 2


if __name__ == "__main__":
    sys.exit(main())
