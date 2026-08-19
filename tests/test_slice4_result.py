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
from publish import publish_receipt  # noqa: E402
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
        self.defaults = {}

    def observe(self, resource_type, identity):
        if resource_type == "setting" and identity.endswith("#default-branch"):
            # GitHub sets the default to the first branch that arrives, which is `main`. The
            # setting exists only once the repository does, and the operation flips it — so the
            # re-read comparison is against a value that really was something else first.
            repository = identity.split("#", 1)[0]
            if repository not in self.state:
                return None
            return {"identity": identity, "resourceType": "setting",
                    "defaultBranch": self.defaults.get(repository, "main")}
        return self.state.get(identity)

    def create(self, resource_type, identity, spec):
        self.state[identity] = {"identity": identity, "type": resource_type, **spec}

    def update(self, resource_type, identity, spec):
        if resource_type == "setting" and identity.endswith("#default-branch"):
            self.defaults[identity.split("#", 1)[0]] = spec["defaultBranch"]
            return
        raise AssertionError(f"no update is implemented for {resource_type}")


CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
             "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}


def chain_parts() -> tuple:
    """Request → plan → apply(both phases) → the arguments a result is assembled from.

    Both phases, against one ledger and one port. Running only `before-files` produced a
    repository receipt, no ruleset receipt, and a result the receiving parser accepted — half an
    executed bootstrap reported as a finished one. The parser reads shape; completeness is only
    visible here.
    """
    compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION, stack="node",
                            ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")
    port = FakeGitHub()
    with tempfile.TemporaryDirectory() as scratch:
        book = Path(scratch) / "receipts.json"
        before = apply_plan(compiled["planCore"], port, ReceiptLedger(book), phase="before-files")
        # The genesis push sits between the phases and leaves its own receipt, which is what
        # `after-files` reads to know the workflow the ruleset requires is actually in the
        # repository. Without it the later phase refuses rather than running out of order.
        ledger = ReceiptLedger(book)
        ledger.record(publish_receipt(compiled["planCore"], {
            "repositoryIdentity": IDENTITY, "head": HEAD, "branches": ["main", "dev"],
            "committedPaths": sorted(compiled["files"]),
            "remoteHeads": {"main": HEAD, "dev": HEAD},
        }, clock=lambda: "2026-08-19T10:00:00Z"))
        published = ledger.get(f"publish:{IDENTITY}")
        after = apply_plan(compiled["planCore"], port, ReceiptLedger(book), phase="after-files")
    return compiled, before["receipts"] + after["receipts"]


def result_args(**overrides) -> dict:
    """A complete, valid argument set. Each refusal test overrides exactly the one thing it is
    about, so the refusal it asserts is the refusal it triggered."""
    compiled, receipts = chain_parts()
    args = dict(
        run_id=REQUEST["runId"],
        plan=compiled["planCore"],
        plan_digest=diff_summary(compiled)["planDigest"],
        repositories=[{"role": "primary", "identity": IDENTITY,
                       "defaultBranch": "dev", "createdBranches": ["main", "dev"]}],
        receipts=receipts,
        bootstrap_verification=[{"commandId": "test", "repositoryIdentity": IDENTITY,
                                 "exactHead": HEAD, "status": "PASS"}],
        verification_commands=VERIFICATION,
        ci_evidence=[{"repositoryIdentity": IDENTITY, "checkName": "project-ci", "head": HEAD,
                      "conclusion": "PASS", "workflowDigest": "sha256:" + "c" * 64}],
    )
    args.update(overrides)
    return args


def whole_chain() -> dict:
    return build_result(**result_args())


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


# The three cross-checks that ran the control plane's own parser now live in
# tests/test_acp_contract.py, where an absent control plane fails rather than skips. A skipped
# contract check and a passing one look identical in a summary line, which is how a suite goes
# green having verified nothing about the other side.

# --- the assembler refuses before the handoff -------------------------------------------

def test_an_unverified_receipt_is_refused_at_assembly_not_at_handoff():
    args = result_args()
    receipts = copy.deepcopy(args["receipts"])
    receipts[0]["verified"] = False

    with pytest.raises(ResultError, match="post-write re-read"):
        build_result(**result_args(receipts=receipts))


def test_a_repeated_operation_id_is_refused():
    with pytest.raises(ResultError, match="unique"):
        build_result(**result_args(receipts=result_args()["receipts"] * 2))


def test_a_failing_verification_has_no_place_in_a_result():
    # The receiving schema types this field as PASS only. A result is not where a failure is
    # reported; unresolvedGaps is, or the result is not produced at all.
    with pytest.raises(ResultError, match="only carry PASS"):
        build_result(**result_args(bootstrap_verification=[
            {"commandId": "test", "repositoryIdentity": IDENTITY, "exactHead": HEAD, "status": "FAIL"}]))


def test_a_result_without_a_receipt_describes_no_bootstrap():
    with pytest.raises(ResultError, match="no external write receipt"):
        build_result(**result_args(receipts=[]))


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


# --- the result is closed against the plan it claims to have executed --------------------

def test_a_plan_operation_with_no_receipt_refuses_the_result():
    """The defect this pins: the chain ran only `before-files`, so the ruleset was never created
    and never claimed, and the receiving parser accepted the result anyway. The parser reads
    shape. Whether the bootstrap finished is only visible against the plan."""
    args = result_args()
    partial = [r for r in args["receipts"] if r["resourceType"] != "ruleset"]
    assert partial != args["receipts"], "the fixture must have a ruleset receipt to remove"

    with pytest.raises(ResultError, match="no receipt"):
        build_result(**result_args(receipts=partial))


def test_a_receipt_for_an_operation_the_plan_does_not_contain_refuses_the_result():
    args = result_args()
    extra = copy.deepcopy(args["receipts"][0])
    extra["operationId"] = "create-repository:not-in-this-plan"

    with pytest.raises(ResultError, match="does not contain"):
        build_result(**result_args(receipts=args["receipts"] + [extra]))


def test_a_receipt_written_under_a_different_approval_refuses_the_result():
    args = result_args()
    borrowed = copy.deepcopy(args["receipts"])
    borrowed[0]["requestDigest"] = "sha256:" + "f" * 64

    with pytest.raises(ResultError, match="different approved intent"):
        build_result(**result_args(receipts=borrowed))


def test_a_receipt_naming_another_resource_refuses_the_result():
    args = result_args()
    swapped = copy.deepcopy(args["receipts"])
    swapped[0]["resourceIdentity"] = "github:MongLong0214/somewhere-else"

    with pytest.raises(ResultError, match="different resource"):
        build_result(**result_args(receipts=swapped))


def test_a_result_may_not_carry_unresolved_gaps():
    """A gap is the honest record of something that could not be made. Reporting it inside a
    completed bootstrap puts both statements in one document and lets the reader take either."""
    with pytest.raises(ResultError, match="unresolved gaps"):
        build_result(**result_args(unresolved_gaps=["stack-specific-ci"]))


def test_a_required_verification_command_with_no_result_refuses_the_result():
    with pytest.raises(ResultError, match="required verification"):
        build_result(**result_args(bootstrap_verification=[
            {"commandId": "some-other-command", "repositoryIdentity": IDENTITY,
             "exactHead": HEAD, "status": "PASS"}]))


def test_verification_commands_that_are_not_the_approved_ones_refuse_the_result():
    """Coverage against a list the caller supplies is coverage against whatever they chose to
    supply. The list has to be the one the plan's digest was taken over."""
    swapped = [dict(VERIFICATION[0], id="test", timeoutSeconds=1)]

    with pytest.raises(ResultError, match="not the ones the plan approved"):
        build_result(**result_args(verification_commands=swapped))


def test_a_default_branch_the_plan_never_set_refuses_the_result():
    """The builder used to carry whatever the caller passed. The remote could be `main` while the
    result asserted `dev`, and the receiving side had no way to tell."""
    with pytest.raises(ResultError, match="approved, re-read operation set"):
        build_result(**result_args(repositories=[
            {"role": "primary", "identity": IDENTITY, "defaultBranch": "main",
             "createdBranches": ["main", "dev"]}]))


def test_a_repository_with_no_default_branch_operation_refuses_the_result():
    args = result_args()
    plan_without = copy.deepcopy(args["plan"])
    plan_without["githubOperations"] = [op for op in plan_without["githubOperations"]
                                        if not op["resourceIdentity"].endswith("#default-branch")]
    receipts = [r for r in args["receipts"] if not r["resourceIdentity"].endswith("#default-branch")]

    with pytest.raises(ResultError, match="no approved operation set"):
        build_result(**result_args(plan=plan_without, receipts=receipts))
