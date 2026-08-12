#!/usr/bin/env python3
"""Autonomous Canary v2 — GitHub Free profile 별 disposable end-to-end 실증.

public (FREE_PUBLIC_USER_NATIVE): active ruleset(bypass 0) · required checks
(integration id 결박) · merge-gate 없는 merge 가 GitHub 에 거부됨 · native
auto-merge · direct/force push 거부 · CodeQL/secret scanning/Dependabot ·
post-merge · revert · OOB audit clean · teardown.

private (FREE_PRIVATE_COMPENSATING): native 보호 부재를 관측으로 확인(주장 안 함)
· 외부 broker 의 exact-head merge + commit marker · post-merge 트리거 · 의도적
OOB direct push 를 audit/post-merge 가 검출 · rollback/invalidation · teardown.

각 step 은 GitHub API 재조회로 확인된 경우에만 true. 실패는 fail-closed 로
evidence 에 기록. evidence: repo-factory.canary-evidence.v2 (+profile).

사용법:
  python3 scripts/run-canary.py --owner <login> --profile public|private [--keep]

종료 코드: 0 = 해당 profile 전 step 실증 / 1 = 실패 / 2 = 사용법
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
KIT = SKILL / "templates" / "kit"
sys.path.insert(0, str(KIT / "scripts"))
import governance as gov  # noqa: E402

POLL_INTERVAL = 15
POLL_TIMEOUT = 900

PUBLIC_STEPS = (
    "repo_created", "actions_source_collected", "rulesets_active",
    "merge_settings_exact", "security_features_enabled", "issue_sync",
    "worker_stub_pr", "ci_failed_once", "repaired", "reviewer_pass",
    "direct_push_rejected", "stale_intent_rejected",
    "merge_without_gate_rejected", "native_auto_merged", "post_merge_pass",
    "verified", "second_ticket_auto_started", "duplicate_pr_zero",
    "revert_flow", "replay_idempotent", "oob_audit_clean", "teardown",
)
PRIVATE_STEPS = (
    "repo_created", "native_enforcement_absent_confirmed", "issue_sync",
    "worker_stub_pr", "ci_gate_green", "reviewer_pass", "stale_intent_rejected",
    "custom_exact_head_merge", "broker_marker_present", "post_merge_triggered",
    "verified", "second_ticket_auto_started", "duplicate_pr_zero",
    "out_of_band_write_detected", "post_merge_failure_detected",
    "rollback_invalidation", "replay_idempotent", "teardown",
)

REVIEWER_STUB = '''#!/usr/bin/env python3
import json, sys
packet = json.load(sys.stdin)
print(json.dumps({"verdict": "PASS", "head_sha": packet.get("head_sha"), "findings": []}))
'''


class CanaryFailure(Exception):
    pass


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run(argv, cwd=None, check=True, input_text=None, env=None):
    merged_env = dict(os.environ)
    merged_env.update({"GIT_TERMINAL_PROMPT": "0",
                       "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
                       "GH_PROMPT_DISABLED": "1"})
    merged_env.update(env or {})
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            input=input_text, env=merged_env, check=False)
    if check and result.returncode:
        raise CanaryFailure(f"{' '.join(map(str, argv[:4]))}… exit {result.returncode}: "
                            f"{(result.stderr or result.stdout)[:500]}")
    return result


def gh(*args, check=True, input_text=None):
    return run(["gh", *args], check=check, input_text=input_text)


def gh_json(*args, check=True):
    result = gh(*args, check=check)
    if result.returncode:
        return None
    return json.loads(result.stdout) if result.stdout.strip() else None


def git(clone: Path, *args, check=True):
    return run(["git", "-C", str(clone), *args], check=check)


def ticket_meta(tid: str, title: str, deps: list[str]) -> dict:
    slug = tid.lower().replace("-", "_")
    return {
        "schema": "repo-governance.ticket.v1", "id": tid, "title": title,
        "kind": "implementation", "risk": "standard", "predelegated": False,
        "milestone": None, "dependencies": deps, "adr_refs": ["ADR-0001"],
        "prd_ref": "PRD-F1",
        "owned_paths": [f"src/{slug}.py", f"test/test_{slug}.py"],
        "coordinated_paths": [],
        "oracle_paths": [f"conformance/{tid}.acceptance.py"],
        "acceptance": [{"id": f"AC-{tid}-1",
                        "test_path": f"conformance/{tid}.acceptance.py",
                        "cases": [f"case_{slug}_contract"]}],
        "commands": {"focused": None, "full": "python3 -m unittest discover -s test -t . -v",
                     "build": "python3 -m compileall src", "lint": None,
                     "typecheck": None, "manual": "LIVE_NA: canary"},
        "budgets": {"repair_rounds": 2, "wall_minutes": 60, "external_cost": None},
        "invalidates": [], "supersedes": [],
    }


def write_ticket(clone: Path, meta: dict) -> None:
    body = (f"# {meta['id']} — {meta['title']}\n\n<!-- repo-governance-ticket:v1\n"
            + json.dumps(meta, ensure_ascii=False, indent=2)
            + "\n-->\n\n## 목적\ncanary 실증 티켓.\n\n## RED\n초기 커밋 실패.\n\n## GREEN\nrepair 후 green.\n")
    (clone / "docs" / "tickets" / f"{meta['id']}-canary.md").write_text(body, encoding="utf-8")
    for ac in meta["acceptance"]:
        oracle = clone / ac["test_path"]
        oracle.parent.mkdir(parents=True, exist_ok=True)
        oracle.write_text(
            f"# sealed acceptance oracle — {meta['id']}\n"
            + "".join(f"def {c}():\n    return True\n" for c in ac["cases"]),
            encoding="utf-8")


def wait_checks(repo: str, sha: str, required: list[str], *, expect_failure_of=None,
                timeout: int = POLL_TIMEOUT) -> dict[str, str]:
    deadline = time.time() + timeout
    pending = list(required)
    while time.time() < deadline:
        runs = gov.check_runs_for(repo, sha)
        state = {r.get("name"): r.get("conclusion") for r in runs}
        pending = [n for n in required if state.get(n) is None]
        if not pending:
            log(f"  checks@{sha[:8]}: " + ", ".join(f"{n}={state.get(n)}" for n in required))
            return {n: state.get(n) for n in required}
        if expect_failure_of and state.get(expect_failure_of) not in (None, "success"):
            log(f"  checks@{sha[:8]}: {expect_failure_of}={state.get(expect_failure_of)} (expected failure)")
            return {n: state.get(n) for n in required}
        time.sleep(POLL_INTERVAL)
    raise CanaryFailure(f"checks timeout on {sha[:8]} (pending: {pending})")


class Canary:
    def __init__(self, owner: str, repo_name: str, profile_mode: str, keep: bool,
                 evidence_dir: Path, workdir: Path):
        self.owner = owner
        self.repo = f"{owner}/{repo_name}"
        self.mode = profile_mode  # public | private
        self.visibility = profile_mode
        self.profile = ("FREE_PUBLIC_USER_NATIVE" if profile_mode == "public"
                        else "FREE_PRIVATE_COMPENSATING")
        self.keep = keep
        self.evidence_dir = evidence_dir
        self.workdir = workdir
        self.clone = workdir / "clone"
        self.step_names = PUBLIC_STEPS if profile_mode == "public" else PRIVATE_STEPS
        self.steps: dict[str, bool] = {s: False for s in self.step_names}
        self.notes: dict[str, str] = {}
        self.actions_app_id: int | None = None
        self.policy_digest = None
        self.deviations = [
            "canary 는 controller/broker 를 1회성으로 즉시 실행 (프로덕션은 로컬 controller 가 "
            "주기적으로 reconcile — 동일 코드경로)",
            "worker/reviewer 는 결정적 stub adapter (프로덕션은 실 provider adapter)",
        ]

    def mark(self, step: str, note: str = "") -> None:
        self.steps[step] = True
        if note:
            self.notes[step] = note
        log(f"STEP {step} ✓ {note}")

    # ------------------------------------------------------------- genesis

    def step_create_repo(self):
        gh("repo", "create", self.repo, f"--{self.visibility}",
           "--description", f"repo-factory canary v2 ({self.profile}, disposable)")
        info = gh_json("api", f"repos/{self.repo}")
        if not info or info.get("full_name", "").lower() != self.repo.lower():
            raise CanaryFailure("repo 재조회 불일치")
        gh("api", "--method", "PATCH", f"repos/{self.repo}",
           "-F", "allow_squash_merge=false", "-F", "allow_rebase_merge=false",
           "-F", "allow_merge_commit=true", "-F", "delete_branch_on_merge=true",
           "-F", "allow_auto_merge=true")
        self.mark("repo_created", f"{info['full_name']} ({info['visibility']})")

    def step_genesis(self):
        self.clone.mkdir(parents=True)
        git(self.clone, "init", "-b", "main")
        git(self.clone, "config", "user.email", "canary@repo-factory.local")
        git(self.clone, "config", "user.name", "repo-factory-canary")
        config = {
            "repository": {"name": self.repo, "owner": self.owner,
                           "visibility": self.visibility},
            "tier": "S",
            "runtime": {"kind": "python", "lower": "3.10", "latest": "3.12"},
            "project_commands": {"full": "python3 -m unittest discover -s test -t . -v",
                                 "build": "python3 -m compileall src"},
            "agent_runtime": {"controller": "ctrl", "worker": "wk",
                              "reviewers": ["rv1", "rv2"], "merge_broker": "bk",
                              "additional_registered_agents": ["spec"]},
            "commitlore": {"required_at_genesis": False},
            "github": {"owner_type": "User", "plan": "free"},
            "security_commands": {"sast": None, "dependency_audit": None,
                                  "secret_scan": None},
        }
        config_path = self.workdir / "repo-factory.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run([sys.executable, str(SKILL / "scripts" / "install-governance.py"),
             "--config", str(config_path), "--path", str(self.clone)])
        stub = self.clone / "scripts" / "canary-reviewer.py"
        stub.write_text(REVIEWER_STUB, encoding="utf-8")
        stub.chmod(0o755)
        for reviewer in ("rv1", "rv2"):
            adapter_path = self.clone / "governance" / "adapters" / f"{reviewer}.json"
            adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
            adapter["invoke"]["review"] = ["python3", "scripts/canary-reviewer.py"]
            adapter_path.write_text(json.dumps(adapter, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        (self.clone / "docs" / "adr").mkdir(parents=True)
        (self.clone / "docs" / "prd").mkdir(parents=True)
        (self.clone / "docs" / "tickets").mkdir(parents=True)
        (self.clone / "docs" / "adr" / "ADR-0001-canary-scope.md").write_text(
            "# ADR-0001 canary 범위\n\nRejected: 실제 제품 코드 — disposable 실증.\n", encoding="utf-8")
        (self.clone / "docs" / "prd" / "PRD-F1-canary.md").write_text(
            "# PRD-F1 canary\n\n자율 루프 실증.\n", encoding="utf-8")
        write_ticket(self.clone, ticket_meta("F1-001", "Canary loop alpha", []))
        write_ticket(self.clone, ticket_meta("F1-002", "Canary loop beta", ["F1-001"]))
        (self.clone / "src").mkdir()
        (self.clone / "test").mkdir()
        (self.clone / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.clone / "test" / "__init__.py").write_text("", encoding="utf-8")
        (self.clone / "test" / "test_genesis.py").write_text(
            "import unittest\n\nclass Genesis(unittest.TestCase):\n"
            "    def test_repo_alive(self):\n        self.assertTrue(True)\n", encoding="utf-8")
        (self.clone / "README.md").write_text(
            "# canary (disposable)\n\n"
            + ("GitHub-native enforcement enabled.\n" if self.mode == "public" else
               "GitHub Free private repository: compensating controls active.\n"
               "Native protected branches/rulesets/auto-merge are unavailable on this plan.\n"),
            encoding="utf-8")
        (self.clone / "AGENTS.md").write_text(
            "# AGENTS\ngovernance kernel 수정 금지 · merge 는 broker 만 · 티켓 밖 작업 금지.\n",
            encoding="utf-8")
        result = run([sys.executable, str(self.clone / "scripts" / "governance.py"),
                      "validate", "--root", str(self.clone)])
        log("  genesis validate: " + result.stdout.strip().splitlines()[-1])
        _, context = gov.validate_repo(self.clone)
        self.policy_digest = context["policy_digest"]
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-m", "genesis: contract + governance kernel (canary v2)")
        git(self.clone, "remote", "add", "origin", f"git@github.com:{self.repo}.git")
        git(self.clone, "push", "-u", "origin", "main")
        git(self.clone, "checkout", "-b", "dev")
        git(self.clone, "push", "-u", "origin", "dev")
        gh("api", "--method", "PATCH", f"repos/{self.repo}", "-F", "default_branch=dev")

    # -------------------------------------------------------------- public

    def step_collect_actions_source(self):
        head = git(self.clone, "rev-parse", "HEAD").stdout.strip()
        deadline = time.time() + 300
        while time.time() < deadline:
            raw = gh_json("api", f"repos/{self.repo}/commits/{head}/check-runs?per_page=50")
            runs = (raw or {}).get("check_runs") or []
            apps = {(r.get("app") or {}).get("id") for r in runs
                    if (r.get("app") or {}).get("slug") == "github-actions"}
            apps.discard(None)
            if apps:
                self.actions_app_id = sorted(apps)[0]
                self.mark("actions_source_collected",
                          f"GitHub Actions integration id={self.actions_app_id} (실제 check run 에서 수집)")
                return
            time.sleep(10)
        raise CanaryFailure("initial check run 에서 Actions app id 를 수집하지 못했다")

    def ruleset_payload(self, name: str, branch: str, contexts: list[tuple[str, int | None]],
                        with_merge_methods: bool) -> dict:
        pull_request_params = {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": False,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": False,
        }
        if with_merge_methods:
            pull_request_params["allowed_merge_methods"] = ["merge"]
        checks = []
        for context, integration in contexts:
            entry: dict = {"context": context}
            if integration:
                entry["integration_id"] = integration
            checks.append(entry)
        return {
            "name": name, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
            "bypass_actors": [],
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": pull_request_params},
                {"type": "required_status_checks",
                 "parameters": {"strict_required_status_checks_policy": True,
                                "required_status_checks": checks}},
            ],
        }

    def step_rulesets(self):
        aid = self.actions_app_id
        dev_contexts = [("governance", aid), ("project-ci", aid), ("agent-review", aid),
                        ("security-gate", aid), ("merge-gate", None)]
        main_contexts = [("governance", aid), ("project-ci", aid),
                         ("security-gate", aid), ("merge-gate", None)]
        for name, branch, contexts in (("repo-factory-dev", "dev", dev_contexts),
                                       ("repo-factory-main", "main", main_contexts)):
            created = False
            for with_methods in (True, False):
                payload = self.ruleset_payload(name, branch, contexts, with_methods)
                result = gh("api", "--method", "POST", f"repos/{self.repo}/rulesets",
                            "--input", "-", check=False, input_text=json.dumps(payload))
                if result.returncode == 0:
                    created = True
                    if not with_methods:
                        self.notes.setdefault("rulesets_active", "")
                        self.deviations.append(
                            f"{name}: allowed_merge_methods 파라미터 미지원 API — merge 방식은 repo 설정으로 강제")
                    break
            if not created:
                raise CanaryFailure(f"ruleset {name} 생성 실패: {result.stderr[:300]}")
        rulesets = gh_json("api", f"repos/{self.repo}/rulesets") or []
        active = {r["name"]: r["enforcement"] for r in rulesets}
        if active.get("repo-factory-dev") != "active" or active.get("repo-factory-main") != "active":
            raise CanaryFailure(f"ruleset 재조회 실패: {active}")
        effective = gh_json("api", f"repos/{self.repo}/rules/branches/dev") or []
        types = {r.get("type") for r in effective}
        if not {"pull_request", "required_status_checks", "non_fast_forward", "deletion"} <= types:
            raise CanaryFailure(f"dev 유효 규칙 불충분: {types}")
        self.mark("rulesets_active",
                  f"dev+main active, bypass 0, required checks integration_id={aid} + merge-gate")
        info = gh_json("api", f"repos/{self.repo}")
        exact = (info.get("allow_merge_commit"), info.get("allow_squash_merge"),
                 info.get("allow_rebase_merge"), info.get("delete_branch_on_merge"),
                 info.get("allow_auto_merge"))
        if exact != (True, False, False, True, True):
            raise CanaryFailure(f"merge settings 불일치: {exact}")
        self.mark("merge_settings_exact", "merge_commit only + delete_head + auto_merge on")

    def step_security_features(self):
        details = []
        # Dependabot — 모든 profile
        gh("api", "--method", "PUT", f"repos/{self.repo}/vulnerability-alerts")
        gh("api", "--method", "PUT", f"repos/{self.repo}/automated-security-fixes",
           check=False)
        details.append("dependabot alerts+security-updates ON")
        if self.mode == "public":
            result = gh("api", "--method", "PATCH",
                        f"repos/{self.repo}/code-scanning/default-setup",
                        "-f", "state=configured", check=False)
            if result.returncode:
                for _ in range(6):
                    time.sleep(20)
                    result = gh("api", "--method", "PATCH",
                                f"repos/{self.repo}/code-scanning/default-setup",
                                "-f", "state=configured", check=False)
                    if result.returncode == 0:
                        break
            setup = gh_json("api", f"repos/{self.repo}/code-scanning/default-setup",
                            check=False)
            state = (setup or {}).get("state")
            if state != "configured":
                raise CanaryFailure(f"CodeQL default setup 미구성: {setup}")
            details.append(f"CodeQL default setup={state} (python)")
            info = gh_json("api", f"repos/{self.repo}")
            ss = ((info.get("security_and_analysis") or {}).get("secret_scanning") or {}).get("status")
            if ss != "enabled":
                gh("api", "--method", "PATCH", f"repos/{self.repo}", "--input", "-",
                   input_text=json.dumps({"security_and_analysis":
                                          {"secret_scanning": {"status": "enabled"}}}))
                info = gh_json("api", f"repos/{self.repo}")
                ss = ((info.get("security_and_analysis") or {}).get("secret_scanning") or {}).get("status")
            if ss != "enabled":
                raise CanaryFailure(f"secret scanning 미활성: {ss}")
            details.append("secret scanning=enabled")
            self.mark("security_features_enabled", " · ".join(details))
        else:
            # private Free: native security 기능을 시도조차 하지 않는다
            self.notes["security_native_skipped"] = \
                "private Free — CodeQL/dependency review/secret scanning/attestation 호출 0"
            log("  " + self.notes["security_native_skipped"])

    def step_confirm_no_native_private(self):
        result = gh("api", f"repos/{self.repo}/branches/dev/protection", check=False)
        classic_absent = result.returncode != 0
        result2 = gh("api", "--method", "POST", f"repos/{self.repo}/rulesets",
                     "--input", "-", check=False,
                     input_text=json.dumps(self.ruleset_payload(
                         "probe", "dev", [("governance", None)], False)))
        ruleset_denied = result2.returncode != 0
        if not ruleset_denied:
            gh("api", "--method", "DELETE",
               f"repos/{self.repo}/rulesets/{json.loads(result2.stdout)['id']}", check=False)
            raise CanaryFailure("private Free 에서 ruleset 이 생성됐다 — capability matrix 재검증 필요")
        self.mark("native_enforcement_absent_confirmed",
                  f"protection API 부정 응답 관측(classic={classic_absent}, ruleset={ruleset_denied}) — "
                  "matrix 정본과 일치. PASS 로 가장하지 않고 보완 통제로 전환")

    def step_issue_sync(self):
        run([sys.executable, str(SKILL / "scripts" / "create-issues.py"),
             "--root", str(self.clone), "--confirm-external-write"])
        rerun = run([sys.executable, str(SKILL / "scripts" / "create-issues.py"),
                     "--root", str(self.clone), "--confirm-external-write"])
        if "create 0" not in rerun.stdout:
            raise CanaryFailure(f"issue sync rerun 이 idempotent 하지 않다: {rerun.stdout[:200]}")
        self.mark("issue_sync", "marker 1:1, rerun duplicate 0")

    # ------------------------------------------------------------- workers

    def operation_id_for(self, tid: str, deps: list[str]) -> str:
        import importlib.util
        spec = importlib.util.spec_from_file_location("rf_autopilot", KIT / "scripts" / "autopilot.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.operation_id(self.repo, tid, {d: "verified" for d in deps},
                                   self.policy_digest)

    def worker_pr(self, tid: str, deps: list[str], failing: bool) -> tuple[int, str, str]:
        slug = tid.lower().replace("-", "_")
        branch = f"feat/{tid}-canary"
        with tempfile.TemporaryDirectory() as tmp:
            wc = Path(tmp) / "wc"
            run(["git", "clone", "--quiet", f"git@github.com:{self.repo}.git", str(wc)])
            git(wc, "config", "user.email", "canary-worker@repo-factory.local")
            git(wc, "config", "user.name", "canary-worker")
            git(wc, "checkout", "-b", branch, "origin/dev")
            (wc / "src" / f"{slug}.py").write_text(
                f"def value_{slug}():\n    return 42\n", encoding="utf-8")
            expected = "41  # RED: 의도된 실패" if failing else "42"
            (wc / "test" / f"test_{slug}.py").write_text(
                "import unittest\n"
                f"from src.{slug} import value_{slug}\n\n"
                f"class T{tid.replace('-', '')}(unittest.TestCase):\n"
                f"    def test_contract(self):\n"
                f"        self.assertEqual(value_{slug}(), {expected})\n", encoding="utf-8")
            git(wc, "add", "-A")
            git(wc, "commit", "-m", f"{tid}: worker stub implementation")
            git(wc, "push", "-u", "origin", branch)
            head = git(wc, "rev-parse", "HEAD").stdout.strip()
        op = self.operation_id_for(tid, deps)
        base_sha = gh_json("api", f"repos/{self.repo}/branches/dev")["commit"]["sha"]
        body = (f"Ticket: {tid}\n\n<!-- repo-governance-operation:\nticket={tid}\n"
                f"operation={op}\nbase={base_sha}\npolicy={self.policy_digest}\n-->\n\n"
                "worker stub PR (canary v2)")
        result = gh("pr", "create", "-R", self.repo, "--base", "dev",
                    "--head", branch, "--title", f"{tid}: canary worker", "--body", body)
        return int(result.stdout.strip().rsplit("/", 1)[-1]), head, branch

    def repair(self, tid: str, branch: str) -> str:
        slug = tid.lower().replace("-", "_")
        with tempfile.TemporaryDirectory() as tmp:
            wc = Path(tmp) / "wc"
            run(["git", "clone", "--quiet", "--branch", branch,
                 f"git@github.com:{self.repo}.git", str(wc)])
            git(wc, "config", "user.email", "canary-worker@repo-factory.local")
            git(wc, "config", "user.name", "canary-worker")
            test_path = wc / "test" / f"test_{slug}.py"
            test_path.write_text(test_path.read_text(encoding="utf-8")
                                 .replace("41  # RED: 의도된 실패", "42"), encoding="utf-8")
            git(wc, "add", "-A")
            git(wc, "commit", "-m", f"{tid}: repair — GREEN")
            git(wc, "push")
            return git(wc, "rev-parse", "HEAD").stdout.strip()

    # -------------------------------------------------------------- broker

    def build_intent(self, tid: str, pr_number: int, head: str, requester: str,
                     deps: list[str]) -> tuple[Path, dict]:
        base_sha = gh_json("api", f"repos/{self.repo}/branches/dev")["commit"]["sha"]
        now = dt.datetime.now(dt.timezone.utc)
        intent = {
            "schema": "repo-governance.merge-intent.v1",
            "request_id": str(uuid.uuid4()),
            "requester_agent_id": requester,
            "repository": self.repo,
            "pr_number": pr_number,
            "ticket_id": tid,
            "operation_id": self.operation_id_for(tid, deps),
            "expected_head_sha": head,
            "expected_base_sha": base_sha,
            "policy_digest": self.policy_digest,
            "requested_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + dt.timedelta(seconds=600)).isoformat().replace("+00:00", "Z"),
        }
        path = self.workdir / f"intent-{tid}-{pr_number}-{requester}.json"
        path.write_text(json.dumps(intent), encoding="utf-8")
        return path, intent

    def collect_facts(self, intent: dict) -> Path:
        pr = gh_json("api", f"repos/{self.repo}/pulls/{intent['pr_number']}")
        base_sha = gh_json("api", f"repos/{self.repo}/branches/dev")["commit"]["sha"]
        runs = gov.check_runs_for(self.repo, intent["expected_head_sha"])
        protection_ok = True  # public: ruleset 재조회 / private: 보완 통제 스스로
        if self.mode == "public":
            effective = gh_json("api", f"repos/{self.repo}/rules/branches/dev") or []
            protection_ok = bool(effective)
        facts = {
            "repository": self.repo,
            "pr": {"number": pr["number"], "state": pr["state"],
                   "merged": bool(pr.get("merged")),
                   "head_sha": pr["head"]["sha"], "base_ref": pr["base"]["ref"],
                   "base_sha": pr["base"]["sha"], "body": pr.get("body") or "",
                   "draft": bool(pr.get("draft"))},
            "base_branch_tip": base_sha,
            "checks": [{"name": r.get("name"), "head_sha": intent["expected_head_sha"],
                        "conclusion": r.get("conclusion"),
                        "app": (r.get("app") or {}).get("slug", "")}
                       for r in runs],
            "reviews": [], "reviews_source": "check_run",
            "open_prs_for_ticket": [intent["pr_number"]],
            "verified_tickets": intent.get("_verified", []),
            "verified_source": "checked",
            "queue_available": False,
            "branch_protection_ok": protection_ok,
            "active_ownership_overlap": False,
            "budget_exceeded": False,
        }
        path = self.workdir / f"facts-{intent['ticket_id']}-{intent['pr_number']}.json"
        path.write_text(json.dumps(facts), encoding="utf-8")
        return path

    def broker_execute(self, tid: str, pr_number: int, head: str, requester: str,
                       deps: list[str], online: bool) -> dict:
        intent_path, intent = self.build_intent(tid, pr_number, head, requester, deps)
        intent["_verified"] = deps
        facts_path = self.collect_facts(intent)
        argv = [sys.executable, str(self.clone / "scripts" / "merge-broker.py"),
                "execute", "--root", str(self.clone), "--intent", str(intent_path),
                "--facts", str(facts_path),
                "--receipts", str(self.workdir / "receipts")]
        if online:
            argv.append("--online")
        result = run(argv, check=False)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise CanaryFailure(f"broker 출력 파싱 실패: {result.stdout[:300]}{result.stderr[:300]}")

    def wait_pr_merged(self, pr_number: int, timeout: int = POLL_TIMEOUT) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            pr = gh_json("api", f"repos/{self.repo}/pulls/{pr_number}")
            if pr.get("merged"):
                return pr.get("merge_commit_sha")
            time.sleep(POLL_INTERVAL)
        raise CanaryFailure(f"PR #{pr_number} 이 merge 되지 않았다 (timeout)")

    # ------------------------------------------------------------ flows

    def flow_ticket_one(self):
        """F1-001: RED → CI 실패 → repair → reviewer PASS → stale 거부 → merge."""
        pr1, head1, branch1 = self.worker_pr("F1-001", [], failing=True)
        self.mark("worker_stub_pr", f"PR #{pr1} head={head1[:8]}")
        required = ["governance", "project-ci", "agent-review", "security-gate"]
        state = wait_checks(self.repo, head1, required, expect_failure_of="project-ci")
        if state.get("project-ci") != "failure":
            raise CanaryFailure(f"의도된 CI 실패 미관측: {state}")
        premature = self.broker_execute("F1-001", pr1, head1, "wk", [], online=False)
        if premature["decision"] in ("MERGED", "QUEUED"):
            raise CanaryFailure("빨간 CI 에서 broker 가 merge 허용 — false-ready!")
        if self.mode == "public":
            self.mark("ci_failed_once",
                      f"project-ci=failure · broker={premature['decision']}/{premature['reason_code']}")
        head2 = self.repair("F1-001", branch1)
        state = wait_checks(self.repo, head2, required)
        if any(v != "success" for v in state.values()):
            raise CanaryFailure(f"repair 후에도 red: {state}")
        if self.mode == "public":
            self.mark("repaired", f"new head {head2[:8]} all green")
        else:
            self.mark("ci_gate_green", f"repair 후 4 lanes green @ {head2[:8]} "
                      f"(RED 관측: project-ci=failure, broker={premature['reason_code']})")
        self.mark("reviewer_pass", f"agent-review=success (stub quorum, exact head {head2[:8]})")
        stale = self.broker_execute("F1-001", pr1, head1, "wk", [], online=False)
        if stale["decision"] != "STALE":
            raise CanaryFailure(f"옛 head intent 가 STALE 이 아니다: {stale}")
        self.mark("stale_intent_rejected", f"old head {head1[:8]} → STALE/{stale['reason_code']}")
        return pr1, head2

    def flow_public_merge(self, pr1: int, head2: str):
        # merge-gate 없이 GitHub 가 merge 를 거부하는지 — native enforcement 실증
        attempt = gh("api", "--method", "PUT", f"repos/{self.repo}/pulls/{pr1}/merge",
                     "-f", "merge_method=merge", "-f", f"sha={head2}", check=False)
        if attempt.returncode == 0:
            raise CanaryFailure("merge-gate 없이 merge API 가 성공했다 — ruleset 미작동!")
        self.mark("merge_without_gate_rejected",
                  f"merge API → HTTP 거부 ({attempt.stderr.splitlines()[0][:80] if attempt.stderr else 'blocked'})")
        decision = self.broker_execute("F1-001", pr1, head2, "wk", [], online=True)
        if decision["decision"] not in ("QUEUED", "MERGED"):
            raise CanaryFailure(f"native auto-merge 활성화 실패: {decision}")
        merge_sha = self.wait_pr_merged(pr1)
        self.mark("native_auto_merged",
                  f"broker merge-gate({decision.get('merge_gate_provenance')}) + auto-merge → "
                  f"GitHub 가 {merge_sha[:8]} 로 merge (사람 승인 0)")
        return merge_sha

    def flow_private_merge(self, pr1: int, head2: str):
        decision = self.broker_execute("F1-001", pr1, head2, "wk", [], online=True)
        if decision["decision"] != "MERGED":
            raise CanaryFailure(f"custom merge 실패: {decision}")
        pr = gh_json("api", f"repos/{self.repo}/pulls/{pr1}")
        if not pr.get("merged"):
            raise CanaryFailure("merge 재조회 실패")
        merge_sha = pr["merge_commit_sha"]
        self.mark("custom_exact_head_merge",
                  f"exact sha={head2[:8]} guard 로 merge → {merge_sha[:8]}")
        commit = gh_json("api", f"repos/{self.repo}/commits/{merge_sha}")
        message = (commit.get("commit") or {}).get("message") or ""
        if "Repo-Factory-Operation:" not in message:
            raise CanaryFailure("merge commit 에 broker marker 없음")
        self.mark("broker_marker_present", "Repo-Factory-Operation/Ticket/Policy-Digest/PR-Head")
        return merge_sha

    def flow_post_merge(self, merge_sha: str):
        state = wait_checks(self.repo, merge_sha, ["post-merge"])
        if state.get("post-merge") != "success":
            raise CanaryFailure(f"post-merge red: {state}")
        if self.mode == "public":
            self.mark("post_merge_pass", f"post-merge=success @ {merge_sha[:8]}")
        else:
            self.mark("post_merge_triggered",
                      f"비-GITHUB_TOKEN merge 가 push workflow 를 실제로 트리거함 "
                      f"(post-merge=success @ {merge_sha[:8]})")
        tree = gh_json("api", f"repos/{self.repo}/contents/conformance/F1-001.acceptance.py?ref=dev",
                       check=False)
        if not tree:
            raise CanaryFailure("current tree 에 oracle 부재")
        self.mark("verified", "merged + post-merge green + current oracle 존재")

    def flow_second_ticket(self):
        plan = json.loads(run([sys.executable, str(self.clone / "scripts" / "autopilot.py"),
                               "run-once", "--root", str(self.clone), "--offline",
                               "--verified", "F1-001"]).stdout)
        if "F1-002" not in plan["startable"]:
            raise CanaryFailure(f"F1-002 not startable: {plan}")
        pr2, _head3, _branch = self.worker_pr("F1-002", ["F1-001"], failing=False)
        self.mark("second_ticket_auto_started", f"F1-002 ready→PR #{pr2}")
        f2 = [p for p in (gh_json("api", f"repos/{self.repo}/pulls?state=open") or [])
              if "Ticket: F1-002" in (p.get("body") or "")]
        if len(f2) != 1:
            raise CanaryFailure(f"F1-002 open PR {len(f2)} ≠ 1")
        self.mark("duplicate_pr_zero", f"F1-002 open PR = 1 (#{pr2})")

    def flow_public_extras(self):
        # direct/force push 거부 — ruleset 실증
        (self.clone / "poke.txt").write_text("direct\n", encoding="utf-8")
        git(self.clone, "fetch", "origin")
        git(self.clone, "checkout", "-B", "dev-probe", "origin/dev")
        git(self.clone, "add", "poke.txt")
        git(self.clone, "commit", "-m", "direct push probe")
        push = git(self.clone, "push", "origin", "dev-probe:dev", check=False)
        force = git(self.clone, "push", "-f", "origin", "dev-probe:dev", check=False)
        if push.returncode == 0 or force.returncode == 0:
            raise CanaryFailure("ruleset 이 direct/force push 를 막지 못했다!")
        git(self.clone, "checkout", "main", check=False)
        self.mark("direct_push_rejected",
                  f"direct exit={push.returncode}, force exit={force.returncode} (둘 다 거부)")

    def flow_revert(self, merge_sha: str, requester: str):
        run([sys.executable, str(self.clone / "scripts" / "autopilot.py"),
             "rollback", "--root", str(self.clone), "--ticket", "F1-001",
             "--reason", "canary revert drill"])
        _, context = gov.validate_repo(self.clone)
        rollback_id = next(t for t in context["tickets"] if t.startswith("R"))
        rb_branch = f"revert/{rollback_id}-canary"
        git(self.clone, "fetch", "origin")
        git(self.clone, "checkout", "-B", rb_branch, "origin/dev")
        git(self.clone, "revert", "-m", "1", "--no-edit", merge_sha)
        git(self.clone, "add", "docs/tickets")
        git(self.clone, "commit", "--amend", "--no-edit")
        git(self.clone, "push", "-u", "origin", rb_branch)
        rb_head = git(self.clone, "rev-parse", "HEAD").stdout.strip()
        rb_op = self.operation_id_for(rollback_id, [])
        rb_base = gh_json("api", f"repos/{self.repo}/branches/dev")["commit"]["sha"]
        rb_body = (f"Ticket: {rollback_id}\n\n<!-- repo-governance-operation:\n"
                   f"ticket={rollback_id}\noperation={rb_op}\nbase={rb_base}\n"
                   f"policy={self.policy_digest}\n-->\n\nRollback of {merge_sha}")
        result = gh("pr", "create", "-R", self.repo, "--base", "dev", "--head", rb_branch,
                    "--title", f"{rollback_id}: revert F1-001", "--body", rb_body)
        rb_pr = int(result.stdout.strip().rsplit("/", 1)[-1])
        required = ["governance", "project-ci", "agent-review", "security-gate"]
        state = wait_checks(self.repo, rb_head, required)
        if any(v != "success" for v in state.values()):
            raise CanaryFailure(f"revert PR red: {state}")
        decision = self.broker_execute(rollback_id, rb_pr, rb_head, requester, [], online=True)
        if self.mode == "public":
            if decision["decision"] not in ("QUEUED", "MERGED"):
                raise CanaryFailure(f"revert auto-merge 실패: {decision}")
            self.wait_pr_merged(rb_pr)
        elif decision["decision"] != "MERGED":
            raise CanaryFailure(f"revert merge 실패: {decision}")
        status = json.loads(run([sys.executable, str(self.clone / "scripts" / "governance.py"),
                                 "status", "--root", str(self.clone), "--offline",
                                 "--json"]).stdout)
        if status["tickets"]["F1-001"]["declared_state"] != "invalidated":
            raise CanaryFailure("F1-001 미invalidated")
        step = "revert_flow" if self.mode == "public" else "rollback_invalidation"
        self.mark(step, f"{rollback_id} PR #{rb_pr} — requester={requester} → F1-001 invalidated")
        # replay 멱등
        replay = self.broker_execute(rollback_id, rb_pr, rb_head, "spec", [], online=False)
        if replay["decision"] != "ALREADY_MERGED" and not replay.get("replay"):
            raise CanaryFailure(f"replay 비멱등: {replay}")
        self.mark("replay_idempotent", f"spec 중복 intent → {replay['decision']}")

    def flow_private_oob(self):
        """private Free 의 현실: direct push 가 물리적으로 가능 → 검출이 보완 통제다."""
        git(self.clone, "fetch", "origin")
        git(self.clone, "checkout", "-B", "oob-probe", "origin/dev")
        (self.clone / "oob.txt").write_text("out of band\n", encoding="utf-8")
        git(self.clone, "add", "oob.txt")
        git(self.clone, "commit", "-m", "manual hotpatch without ticket")
        push = git(self.clone, "push", "origin", "oob-probe:dev", check=False)
        if push.returncode != 0:
            raise CanaryFailure("private Free 인데 direct push 가 거부됨 — profile 판정 재검토 필요")
        oob_sha = git(self.clone, "rev-parse", "HEAD").stdout.strip()
        audit = run([sys.executable, str(self.clone / "scripts" / "merge-broker.py"),
                     "audit", "--root", str(self.clone), "--online", "--branch", "dev",
                     "--limit", "5"], check=False)
        payload = json.loads(audit.stdout)
        oob = [f for f in payload["findings"] if f["verdict"] == "OUT_OF_BAND_WRITE"]
        if audit.returncode == 0 or not oob:
            raise CanaryFailure(f"OOB direct push 미검출: {payload}")
        self.mark("out_of_band_write_detected",
                  f"{oob_sha[:8]} → OUT_OF_BAND_WRITE (audit exit 1, quarantine 신호)")
        state = wait_checks(self.repo, oob_sha, ["post-merge"])
        if state.get("post-merge") == "success":
            raise CanaryFailure("marker 없는 push 에 post-merge 가 green — audit step 미작동")
        self.mark("post_merge_failure_detected",
                  f"post-merge={state.get('post-merge')} (marker audit 이 workflow 에서도 잡음)")

    def flow_public_oob_audit(self):
        audit = run([sys.executable, str(self.clone / "scripts" / "merge-broker.py"),
                     "audit", "--root", str(self.clone), "--online", "--branch", "dev",
                     "--limit", "10"], check=False)
        payload = json.loads(audit.stdout)
        if audit.returncode != 0:
            raise CanaryFailure(f"public dev 에 OOB 검출: {payload}")
        self.mark("oob_audit_clean", f"{len(payload['findings'])} commits 전부 PR/GENESIS 경유")

    # ------------------------------------------------------------- driver

    def execute(self):
        self.step_create_repo()
        self.step_genesis()
        if self.mode == "public":
            self.step_collect_actions_source()
            self.step_rulesets()
            self.step_security_features()
            self.step_issue_sync()
            pr1, head2 = self.flow_ticket_one()
            self.flow_public_extras()
            merge_sha = self.flow_public_merge(pr1, head2)
            self.flow_post_merge(merge_sha)
            self.flow_second_ticket()
            self.flow_revert(merge_sha, requester="rv1")
            self.flow_public_oob_audit()
        else:
            self.step_confirm_no_native_private()
            self.step_security_features()
            self.step_issue_sync()
            pr1, head2 = self.flow_ticket_one()
            merge_sha = self.flow_private_merge(pr1, head2)
            self.flow_post_merge(merge_sha)
            self.flow_second_ticket()
            self.flow_private_oob()
            self.flow_revert(merge_sha, requester="rv1")

    def teardown(self):
        if self.keep:
            self.notes["teardown"] = "--keep 지정 — 수동 삭제 필요"
            return
        result = gh("repo", "delete", self.repo, "--yes", check=False)
        if result.returncode:
            raise CanaryFailure(f"teardown 실패: {result.stderr[:200]}")
        check = gh("api", f"repos/{self.repo}", check=False)
        if check.returncode == 0:
            raise CanaryFailure("삭제 후에도 repo 조회됨")
        self.mark("teardown", "repo 삭제 + 404 재조회")

    def write_evidence(self, error: str | None) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema": "repo-factory.canary-evidence.v2",
            "profile": self.profile,
            "repository": self.repo,
            "visibility": self.visibility,
            "merge_gate": "local_controller",
            "actions_integration_id": self.actions_app_id,
            "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "policy_digest": self.policy_digest,
            "steps": self.steps,
            "notes": self.notes,
            "deviations": self.deviations,
            "error": error,
        }
        name = "canary.json" if self.mode == "public" else "canary-private.json"
        path = self.evidence_dir / name
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--profile", choices=("public", "private"), required=True)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=SKILL / "evidence")
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    repo_name = args.repo or (
        f"repo-factory-canary-{args.profile}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    workdir = Path(tempfile.mkdtemp(prefix=f"rf-canary-{args.profile}-"))
    canary = Canary(args.owner, repo_name, args.profile, args.keep,
                    args.evidence_dir, workdir)
    error = None
    try:
        canary.execute()
    except CanaryFailure as failure:
        error = str(failure)
        log(f"CANARY FAIL: {error}")
    finally:
        try:
            canary.teardown()
        except CanaryFailure as failure:
            error = (error + " | " if error else "") + str(failure)
            log(f"TEARDOWN FAIL: {failure}")
        path = canary.write_evidence(error)
        log(f"evidence → {path}")
    all_pass = all(canary.steps.values()) and error is None
    missing = [s for s, v in canary.steps.items() if not v]
    log("RESULT: " + (f"{canary.profile} canary {len(canary.steps)}/{len(canary.steps)} 실증"
                      if all_pass else f"INCOMPLETE — {missing}"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
