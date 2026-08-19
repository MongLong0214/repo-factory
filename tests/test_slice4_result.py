"""Slice 4 — the handoff document, checked against the control plane that receives it."""
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
from result import FORBIDDEN_CLAIMS, ResultError, build_result  # noqa: E402

ACP_SRC = Path(os.environ.get("ACP_SRC", Path.home() / "projects/agent-control-plane/src"))
PARSE_CHECK = SKILL / "tests" / "fixtures" / "acp-parse-check.mts"

REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-e2e", "seed": "a ledger reconciler",
    "bootstrapProfile": "STANDARD", "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "ledger-reconciler"}],
    "visibility": "private", "origin": {"channel": "cli", "requestedAt": "2026-08-19T09:00:00Z"},
}
VERIFICATION = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
                 "timeoutSeconds": 1200, "envAllowlist": ["CI"], "network": "deny", "required": True}]
IDENTITY = "github:MongLong0214/ledger-reconciler"
HEAD = "a" * 40


class FakeGitHub:
    def __init__(self):
        self.state = {}

    def observe(self, resource_type, identity):
        return self.state.get(identity)

    def create(self, resource_type, identity, spec):
        self.state[identity] = {"identity": identity, "type": resource_type, **spec}


def whole_chain() -> dict:
    """Request → plan → apply → result, exactly as the pipeline runs it."""
    compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION,
                            operation_id="11111111-2222-3333-4444-555555555555")
    with tempfile.TemporaryDirectory() as scratch:
        applied = apply_plan(compiled["planCore"], FakeGitHub(),
                             ReceiptLedger(Path(scratch) / "receipts.json"),
                             clock="2026-08-19T09:05:00Z")
    return build_result(
        run_id=REQUEST["runId"], plan=compiled["planCore"], plan_digest=diff_summary(compiled)["planDigest"],
        repositories=[{"role": "primary", "identity": IDENTITY,
                       "defaultBranch": "dev", "createdBranches": ["main", "dev"]}],
        receipts=applied["receipts"],
        bootstrap_verification=[{"commandId": "test", "repositoryIdentity": IDENTITY,
                                 "exactHead": HEAD, "status": "PASS"}],
        ci_evidence=[{"repositoryIdentity": IDENTITY, "checkName": "project-ci", "head": HEAD,
                      "conclusion": "PASS", "workflowDigest": "sha256:" + "c" * 64}],
    )


def acp_verdict(result: dict) -> dict:
    node_bin = shutil.which("npx")
    if node_bin is None or not ACP_SRC.is_dir():
        pytest.skip(f"the control plane checkout or npx is unavailable (ACP_SRC={ACP_SRC})")
    completed = subprocess.run(
        [node_bin, "--prefix", str(ACP_SRC.parent), "tsx", str(PARSE_CHECK)],
        input=json.dumps(result), capture_output=True, text=True,
        env={**os.environ, "ACP_SRC": str(ACP_SRC)}, timeout=180,
    )
    if completed.returncode != 0:
        pytest.skip(f"could not run the control plane parser: {completed.stderr.strip()[:200]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


# --- the authority accepts what this pipeline produces ----------------------------------

def test_the_whole_chain_produces_a_result_the_control_plane_accepts():
    # Checked against `parseRepoFactoryResult` itself rather than a local copy of its rules,
    # so a change on the receiving side shows up here instead of at handoff.
    assert acp_verdict(whole_chain()) == {"allowed": True, "reasonCode": "OK", "evidence": {}}


def test_the_control_plane_rejects_a_result_that_claims_activation():
    overclaiming = dict(whole_chain(), doctor={"status": "PASS"})

    verdict = acp_verdict(overclaiming)

    assert verdict["allowed"] is False
    assert verdict["reasonCode"] == "BOOTSTRAP_RESULT_OVERCLAIMS_ACTIVATION"


def test_the_control_plane_rejects_an_unverified_receipt():
    weakened = whole_chain()
    weakened["externalWriteReceipts"][0]["rereadAt"] = None

    assert acp_verdict(weakened)["allowed"] is False


# --- the assembler refuses before the handoff -------------------------------------------

def test_an_unverified_receipt_is_refused_at_assembly_not_at_handoff():
    result = whole_chain()
    receipts = copy.deepcopy(result["externalWriteReceipts"])
    receipts[0]["verified"] = False

    with pytest.raises(ResultError, match="post-write re-read"):
        build_result(run_id="r", plan={"bootstrapOperationId": "o", "projectManifestDigest": "d"},
                     plan_digest="sha256:x", repositories=[{"role": "primary", "identity": IDENTITY,
                                                            "defaultBranch": "dev"}],
                     receipts=receipts,
                     bootstrap_verification=[{"commandId": "t", "repositoryIdentity": IDENTITY,
                                              "exactHead": HEAD, "status": "PASS"}])


def test_a_repeated_operation_id_is_refused():
    result = whole_chain()
    doubled = result["externalWriteReceipts"] * 2

    with pytest.raises(ResultError, match="unique"):
        build_result(run_id="r", plan={"bootstrapOperationId": "o", "projectManifestDigest": "d"},
                     plan_digest="sha256:x",
                     repositories=[{"role": "primary", "identity": IDENTITY, "defaultBranch": "dev"}],
                     receipts=doubled,
                     bootstrap_verification=[{"commandId": "t", "repositoryIdentity": IDENTITY,
                                              "exactHead": HEAD, "status": "PASS"}])


def test_a_failing_verification_has_no_place_in_a_result():
    # The receiving schema types this field as PASS only. A result is not where a failure is
    # reported; unresolvedGaps is, or the result is not produced at all.
    with pytest.raises(ResultError, match="only carry PASS"):
        build_result(run_id="r", plan={"bootstrapOperationId": "o", "projectManifestDigest": "d"},
                     plan_digest="sha256:x",
                     repositories=[{"role": "primary", "identity": IDENTITY, "defaultBranch": "dev"}],
                     receipts=whole_chain()["externalWriteReceipts"],
                     bootstrap_verification=[{"commandId": "t", "repositoryIdentity": IDENTITY,
                                              "exactHead": HEAD, "status": "FAIL"}])


def test_a_result_without_a_receipt_describes_no_bootstrap():
    with pytest.raises(ResultError, match="no external write receipt"):
        build_result(run_id="r", plan={"bootstrapOperationId": "o", "projectManifestDigest": "d"},
                     plan_digest="sha256:x",
                     repositories=[{"role": "primary", "identity": IDENTITY, "defaultBranch": "dev"}],
                     receipts=[], bootstrap_verification=[{"commandId": "t", "repositoryIdentity": IDENTITY,
                                                           "exactHead": HEAD, "status": "PASS"}])


def test_the_forbidden_claim_list_matches_the_receiving_side():
    # Names, not behaviour: the receiver rejects on key presence, so a name that drifts here
    # would let an overclaim through this assembler and be caught only at handoff.
    source = (ACP_SRC / "bootstrap" / "repo-factory-result.ts")
    if not source.is_file():
        pytest.skip("the control plane checkout is unavailable")
    text = source.read_text(encoding="utf-8")
    block = text.split("const FORBIDDEN_CLAIMS = [", 1)[1].split("]", 1)[0]
    theirs = {line.strip().strip('",') for line in block.splitlines() if '"' in line}
    assert set(FORBIDDEN_CLAIMS) == theirs


def test_a_local_checkout_path_is_a_proposal_and_defaults_to_absent():
    # §11 — Repo Factory may propose a local binding but never commits one.
    assert whole_chain()["repositories"][0]["proposedCheckoutPath"] is None
