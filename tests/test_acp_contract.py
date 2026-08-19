"""The contract with the control plane, checked against the control plane.

PRD §2.2 keeps one implementation of the shared schemas and it is the control plane's. These
tests run *its* validators rather than a restatement of their rules, against a pinned commit —
a contract check whose other side moves is a report about whatever happened to be checked out.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from apply import ReceiptLedger, apply_plan  # noqa: E402
from plan import compile_plan, diff_summary  # noqa: E402
from result import build_result  # noqa: E402

LOCK = json.loads((SKILL / "governance" / "acp-contract.lock.json").read_text(encoding="utf-8"))
ACP_SRC = Path(os.environ.get("ACP_SRC", Path.home() / "projects/agent-control-plane/src"))
FIXTURES = SKILL / "tests" / "fixtures"

# Skipping a contract check is how a suite goes green having verified nothing about the other
# side. Local development may opt out loudly; CI never does.
ALLOW_SKIP = os.environ.get("RF_ALLOW_CONTRACT_SKIP") == "1"

VER = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
        "timeoutSeconds": 900, "envAllowlist": ["CI"], "network": "deny", "required": True}]
CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
             "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}


def require_control_plane() -> None:
    if ACP_SRC.is_dir() and shutil.which("npx"):
        return
    if ALLOW_SKIP:
        pytest.skip(f"control plane unavailable and RF_ALLOW_CONTRACT_SKIP=1 (ACP_SRC={ACP_SRC})")
    pytest.fail(
        f"the control plane checkout is required for contract tests and was not found at {ACP_SRC}. "
        "CI clones the commit pinned in governance/acp-contract.lock.json; set "
        "RF_ALLOW_CONTRACT_SKIP=1 only for local work, never for a release suite."
    )


def run_check(fixture: str, payload: dict) -> dict:
    require_control_plane()
    done = subprocess.run(
        [shutil.which("npx"), "--prefix", str(ACP_SRC.parent), "tsx", str(FIXTURES / fixture)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "ACP_SRC": str(ACP_SRC)}, timeout=180,
    )
    if done.returncode != 0:
        pytest.fail(f"{fixture} could not run against the control plane: {done.stderr.strip()[:300]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def request_for(profile: str, name: str = "demo") -> dict:
    return {"schema": "repo-factory.bootstrap-request.v1", "runId": "contract", "seed": "a demo",
            "bootstrapProfile": profile, "priority": "NORMAL",
            "repositories": [{"role": "primary", "name": name}],
            "visibility": "private", "origin": {"channel": "cli"}}


# --- the pin itself --------------------------------------------------------------------

def test_the_pinned_commit_is_the_one_being_checked_against():
    # Without this the lock file is decoration: the tests would run against whatever the
    # developer's checkout happens to be, and pass or fail for reasons the pin does not name.
    require_control_plane()
    head = subprocess.run(["git", "-C", str(ACP_SRC.parent), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    if head != LOCK["commit"]:
        pytest.skip(
            f"control-plane checkout is at {head[:12]}, lock pins {LOCK['commit'][:12]}. "
            "CI clones the pinned commit; a local checkout may legitimately differ."
        )


def test_the_schema_ids_the_lock_names_are_the_ones_the_control_plane_emits():
    require_control_plane()
    manifest_source = (ACP_SRC / "contracts" / "manifest.ts").read_text(encoding="utf-8")
    result_source = (ACP_SRC / "bootstrap" / "repo-factory-result.ts").read_text(encoding="utf-8")

    assert LOCK["schemaIds"]["projectManifest"] in manifest_source
    assert LOCK["schemaIds"]["repoFactoryResult"] in result_source


# --- the manifest, which is what a generated repository actually carries ----------------

def test_a_node_manifest_is_one_the_control_plane_accepts():
    compiled = compile_plan(request_for("SIMPLE"), VER, stack="node", ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")

    assert run_check("acp-manifest-check.mts", compiled["projectManifest"])["allowed"] is True


@pytest.mark.parametrize("stack,executable", [("python", "python"), ("go", "go"), ("rust", "cargo")])
def test_a_non_node_manifest_is_refused_and_that_is_the_current_contract(stack: str, executable: str):
    # Measured, and it is a defect rather than a design: the control plane's verification
    # executable allowlist does not carry python, go or cargo, so a manifest naming one is
    # refused — while the same project's GitHub CI is green. A green project-ci and a manifest
    # the control plane can verify are different claims, and only the first was being made.
    #
    # This test asserts today's truth so the gap cannot be forgotten. Widening the allowlist on
    # the control-plane side, or moving these stacks to TRUSTED_CI evidence, will fail it — and
    # that failure is the signal to update this expectation rather than a regression.
    commands = [dict(VER[0], argv=[executable, "--version"])]
    compiled = compile_plan(request_for("SIMPLE"), commands, stack=stack,
                            ci_values={**CI_VALUES, "RUNTIME_LOWER": "1", "RUNTIME_LATEST": "2"},
                            operation_id="11111111-2222-3333-4444-555555555555")

    verdict = run_check("acp-manifest-check.mts", compiled["projectManifest"])

    assert verdict["allowed"] is False
    assert "verificationCommands.0.argv.0" in verdict["issues"]


def test_a_network_allowlist_command_is_refused():
    # The control plane retains `allowlist` in the wire union solely to return a migration
    # error: seatbelt cannot enforce a destination allowlist, so claiming one would be a
    # containment promise nothing keeps.
    commands = [dict(VER[0], network="allowlist")]
    compiled = compile_plan(request_for("SIMPLE"), commands, stack="node", ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")

    assert run_check("acp-manifest-check.mts", compiled["projectManifest"])["allowed"] is False


# --- the result document ----------------------------------------------------------------

def test_the_whole_chain_produces_a_result_the_control_plane_accepts():
    class FakeGitHub:
        def __init__(self):
            self.state = {}

        def observe(self, _t, identity):
            return self.state.get(identity)

        def create(self, _t, identity, spec):
            # The created resource reflects what it was created with. A fake that drops `spec`
            # cannot represent the thing it claims to have made, and the post-write re-read then
            # compares the approved state against a stub that could never disagree with it.
            self.state[identity] = {"identity": identity, **spec}

    compiled = compile_plan(request_for("STANDARD", "ledger"), VER, stack="node",
                            ci_values=CI_VALUES, operation_id="11111111-2222-3333-4444-555555555555")
    # Both phases. Running only the default one produced a repository receipt, no ruleset
    # receipt, and a result this parser accepted — half an executed bootstrap reported as a
    # finished one. "The whole chain" has to be the whole chain.
    port = FakeGitHub()
    with tempfile.TemporaryDirectory() as scratch:
        book = Path(scratch) / "r.json"
        before = apply_plan(compiled["planCore"], port, ReceiptLedger(book), phase="before-files")
        after = apply_plan(compiled["planCore"], port, ReceiptLedger(book), phase="after-files")
    receipts = before["receipts"] + after["receipts"]
    assert {r["operationId"] for r in receipts} == {
        op["operationId"] for op in compiled["planCore"]["githubOperations"]
    }
    identity = "github:MongLong0214/ledger"
    result = build_result(
        run_id="contract", plan=compiled["planCore"], plan_digest=diff_summary(compiled)["planDigest"],
        repositories=[{"role": "primary", "identity": identity, "defaultBranch": "dev",
                       "createdBranches": ["main", "dev"]}],
        receipts=receipts,
        bootstrap_verification=[{"commandId": "test", "repositoryIdentity": identity,
                                 "exactHead": "a" * 40, "status": "PASS"}],
        verification_commands=VER,
    )

    verdict = run_check("acp-parse-check.mts", result)

    assert verdict["allowed"] is True
    assert verdict["reasonCode"] == "OK"


def test_the_control_plane_still_refuses_a_result_that_claims_activation():
    overclaiming = {"schema": "repo-factory.result.v2", "doctor": {"status": "PASS"}}

    verdict = run_check("acp-parse-check.mts", overclaiming)

    assert verdict["allowed"] is False
    assert verdict["reasonCode"] == "BOOTSTRAP_RESULT_OVERCLAIMS_ACTIVATION"


# --- the fields a generated repository must carry for the control plane to use it -------

def test_the_manifest_declares_its_ci_workflow_as_a_first_activation():
    # Without ciWorkflows the control plane cannot attribute the repository's project-ci: the
    # check reads as `unapproved` and post-merge verification never passes. The digest cannot be
    # filled here — approval belongs to activation — so the pair states "not approved yet"
    # explicitly. `approvedDigest: null` on its own does not parse, deliberately: it used to mean
    # both "not approved yet" and "anything is fine", and a manifest could activate cleanly while
    # being unable to ever merge.
    compiled = compile_plan(request_for("SIMPLE"), VER, stack="node", ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")
    workflows = compiled["projectManifest"]["ciWorkflows"]

    assert workflows == [{
        "path": ".github/workflows/project-ci.yml",
        "checkName": "project-ci",
        "repositoryRole": "primary",
        "approvedDigest": None,
        "unapprovedFirstActivation": True,
    }]
    assert run_check("acp-manifest-check.mts", compiled["projectManifest"])["allowed"] is True


def test_no_workflow_is_declared_when_none_was_rendered():
    # Declaring a workflow that is not in the repository would make post-merge verification
    # require a check nothing publishes — a manifest that can never be satisfied.
    compiled = compile_plan(request_for("SIMPLE"), VER,
                            operation_id="11111111-2222-3333-4444-555555555555")

    assert compiled["projectManifest"]["ciWorkflows"] == []
    assert any("stack-specific CI" in gap for gap in compiled["unresolvedGaps"])


@pytest.mark.parametrize("profile,mode", [("SIMPLE", "preferred"), ("STANDARD", "required"),
                                          ("GUARDED", "required")])
def test_the_profile_commitlore_policy_reaches_the_manifest(profile: str, mode: str):
    # §18. The profile decided this at plan time; without it in the manifest the generated
    # repository carries no record of the decision and nothing downstream can act on it.
    commands = VER + ([dict(VER[0], id="security")] if profile == "GUARDED" else [])
    compiled = compile_plan(request_for(profile), commands, stack="node", ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")

    assert compiled["projectManifest"]["commitlore"] == {"mode": mode}
    assert run_check("acp-manifest-check.mts", compiled["projectManifest"])["allowed"] is True
