"""테스트 공용 헬퍼 — 유효한 생성 레포 fixture 를 installer 로 실제 materialize 한다."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
KIT = SKILL / "templates" / "kit"
INSTALLER = SKILL / "scripts" / "install-governance.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

HEAD = "a" * 40
BASE = "b" * 40
NOW = "2026-08-08T00:00:00Z"
LATER = "2026-08-08T00:05:00Z"
EXPIRED = "2026-08-08T02:00:00Z"


def load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GOV = load_module("governance", KIT / "scripts" / "governance.py")
BROKER = load_module("merge_broker", KIT / "scripts" / "merge-broker.py")
AUTOPILOT = load_module("autopilot", KIT / "scripts" / "autopilot.py")


def base_config(**overrides) -> dict:
    config = {
        "repository": {"name": "acme/demo", "owner": "acme", "visibility": "private"},
        "tier": "M",
        "runtime": {"kind": "python", "lower": "3.10", "latest": "3.12"},
        "project_commands": {"full": "python3 -m unittest", "build": "python3 -m compileall src"},
        "agent_runtime": {
            "controller": "ctrl-a", "worker": "worker-a",
            "reviewers": ["rev-a", "rev-b"], "merge_broker": "broker-a",
            "additional_registered_agents": ["spec-a"],
        },
        "commitlore": {"required_at_genesis": False},
        # private fixture 의 high 티켓이 SECURITY_LANE_MISSING 에 걸리지 않도록
        # 결정적 no-op custom lane 을 부여한다 (해당 predicate 는 전용 테스트가 검증)
        "security_commands": {"sast": "true", "dependency_audit": "true", "secret_scan": None},
    }
    config.update(overrides)
    return config


def ticket_md(meta: dict, body: str = "본문") -> str:
    return (
        f"# {meta['id']} — {meta['title']}\n\n"
        "<!-- repo-governance-ticket:v1\n"
        + json.dumps(meta, ensure_ascii=False, indent=2)
        + "\n-->\n\n"
        f"## 목적\n{body}\n\n## RED\n예상 실패\n\n## GREEN\n최소 통과\n"
    )


def ticket_meta(tid: str, **overrides) -> dict:
    meta = {
        "schema": "repo-governance.ticket.v1",
        "id": tid,
        "title": f"Ticket {tid}",
        "kind": "implementation",
        "risk": "standard",
        "predelegated": False,
        "milestone": None,
        "dependencies": [],
        "adr_refs": ["ADR-0001"],
        "prd_ref": "PRD-F1",
        "owned_paths": [f"src/{tid.lower()}.py", f"test/test_{tid.lower()}.py"],
        "coordinated_paths": [],
        "oracle_paths": [f"conformance/{tid}.acceptance.py"],
        "acceptance": [{
            "id": f"AC-{tid}-1",
            "test_path": f"conformance/{tid}.acceptance.py",
            "cases": [f"case_{tid.lower().replace('-', '_')}_valid"],
        }],
        "commands": {"focused": None, "full": "python3 -m unittest", "build": "true",
                     "lint": None, "typecheck": None, "manual": "LIVE_NA: deterministic"},
        "budgets": {"repair_rounds": 2, "wall_minutes": 60, "external_cost": None},
        "invalidates": [],
        "supersedes": [],
    }
    meta.update(overrides)
    return meta


def write_ticket(root: Path, meta: dict) -> Path:
    path = root / "docs" / "tickets" / f"{meta['id']}-{meta['title'].split()[-1].lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ticket_md(meta), encoding="utf-8")
    for ac in meta.get("acceptance") or []:
        oracle = root / ac["test_path"]
        oracle.parent.mkdir(parents=True, exist_ok=True)
        cases = "\n".join(f"def {c}(): pass" for c in ac["cases"])
        oracle.write_text(f"# sealed acceptance oracle for {meta['id']}\n{cases}\n", encoding="utf-8")
    return path


def public_config(owner_type: str = "User", **overrides) -> dict:
    """public 프로파일 fixture — native 강제 경로 테스트용."""
    config = base_config(
        repository={"name": "acme/pub", "owner": "acme", "visibility": "public"},
        github={"owner_type": owner_type, "plan": "free"},
    )
    config.update(overrides)
    return config


def make_repo(root: Path, config: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = config or base_config()
    config_path = root.parent / f"{root.name}-repo-factory.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(INSTALLER), "--config", str(config_path), "--path", str(root)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"installer 실패: {result.stdout}\n{result.stderr}"

    (root / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "prd").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "adr" / "ADR-0001-scope.md").write_text(
        "# ADR-0001 범위\n\nRejected: 무한 범위 — 이유: 기한.\n", encoding="utf-8")
    (root / "docs" / "prd" / "PRD-F1-core.md").write_text(
        "# PRD-F1 core\n\n목표/AC.\n", encoding="utf-8")
    (root / "docs" / "critical-path.md").write_text(
        "# Critical Path\n\nF1-001 → F2-001\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# AGENTS\ngovernance kernel 은 수정 금지.\n", encoding="utf-8")
    (root / "CONTRIBUTING.md").write_text("# 기여\n", encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "test").mkdir(exist_ok=True)

    write_ticket(root, ticket_meta("F1-001", title="Ticket parse"))
    write_ticket(root, ticket_meta(
        "F2-001", title="Ticket highwork", risk="high", predelegated=True))
    for rel in ("src/f1-001.py", "test/test_f1-001.py", "src/f2-001.py", "test/test_f2-001.py"):
        (root / rel).write_text("# placeholder\n", encoding="utf-8")
    return root


def make_intent(root: Path, ticket: str = "F1-001", pr: int = 7,
                requester: str = "worker-a", now: str = NOW) -> dict:
    result = subprocess.run(
        [sys.executable, str(KIT / "scripts" / "autopilot.py"), "request-merge",
         "--root", str(root), "--ticket", ticket, "--pr", str(pr),
         "--requester", requester, "--head", HEAD, "--base", BASE, "--now", now],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"request-merge 실패: {result.stdout}\n{result.stderr}"
    return json.loads((root / ".autopilot" / f"intent-{ticket}-{pr}.json").read_text(encoding="utf-8"))


def make_facts(intent: dict, **overrides) -> dict:
    marker = (
        f"<!-- repo-governance-operation:\nticket={intent['ticket_id']}\n"
        f"operation={intent['operation_id']}\nbase={BASE}\npolicy={intent['policy_digest']}\n-->"
    )
    facts = {
        "repository": intent["repository"],
        "pr": {
            "number": intent["pr_number"], "state": "open", "merged": False,
            "head_sha": HEAD, "base_ref": "dev", "base_sha": BASE,
            "body": f"Ticket: {intent['ticket_id']}\n\n{marker}\n", "draft": False,
        },
        "base_branch_tip": BASE,
        "checks": [
            {"name": name, "head_sha": HEAD, "conclusion": "success", "app": "github-actions"}
            for name in ("governance", "project-ci", "agent-review")
        ],
        "reviews": [{"reviewer": "rev-a", "verdict": "PASS", "head_sha": HEAD, "findings": []}],
        "reviews_source": "artifact",
        "open_prs_for_ticket": [intent["pr_number"]],
        "verified_tickets": [],
        "verified_source": "checked",
        "queue_available": False,
        "branch_protection_ok": True,
        "active_ownership_overlap": False,
        "budget_exceeded": False,
    }
    facts.update(overrides)
    return facts


def parse_now(value: str):
    return BROKER.parse_time(value)


def validate(root: Path):
    return GOV.validate_repo(root)


def problem_codes(problems) -> set[str]:
    return {item["code"] for item in problems.items}
