"""RF-S03 — the Bootstrap CTO's lean verdict is produced by `lean.py` and consumed by `plan.py`.

The compiler applied a verdict only when someone handed it one, and nothing produced one, so
"an over-designed STANDARD spec is cut down" held only for a hand-written document. These tests
run the verdict from the command that writes it to the command that compiles with it.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from canonical import digest  # noqa: E402
from lean import main as lean_main  # noqa: E402
from plan import compile_plan, load_profile, main as plan_main  # noqa: E402

VERIFICATION = [
    {"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True},
]
REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-lean",
    "seed": "a small tool that reconciles two ledgers", "bootstrapProfile": "STANDARD",
    "priority": "NORMAL", "repositories": [{"role": "primary", "name": "ledger-reconciler"}],
    "visibility": "public", "ownerConstraints": ["no-paid-plan-change"],
    "humanGateFacts": ["public-exposure"], "origin": {"channel": "cli"},
}
OPERATION_ID = "11111111-2222-3333-4444-555555555555"
OVER_DESIGNED = ["adr", "issue-projection", "research-dossier", "tickets"]
TICKETS = "tickets=the control plane dispatches the first wave from them"


def generate(tmp_path, capsys, *argv):
    (tmp_path / "request.json").write_text(json.dumps(REQUEST))
    code = lean_main(["--request", str(tmp_path / "request.json"), *argv])
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def plan(tmp_path, capsys, review, optional):
    (tmp_path / "verification.json").write_text(json.dumps(VERIFICATION))
    (tmp_path / "review.json").write_text(json.dumps(review))
    argv = ["--request", str(tmp_path / "request.json"),
            "--verification", str(tmp_path / "verification.json"),
            "--operation-id", OPERATION_ID, "--lean-review", str(tmp_path / "review.json")]
    if optional:
        argv += ["--optional", *optional]
    code = plan_main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured


def library(optional):
    return compile_plan(copy.deepcopy(REQUEST), VERIFICATION, requested_optional=optional,
                        operation_id=OPERATION_ID)


# --- RF-S03: the lean verdict is generated, then consumed on the plan command -------------

def test_the_generated_revision_cuts_what_nothing_consumes_and_keeps_scope(tmp_path, capsys):
    code, review = generate(tmp_path, capsys, "--optional", *OVER_DESIGNED, "--keep", TICKETS)
    assert code == 0
    assert review["verdict"] == "LEAN_REVISE"
    assert [row["item"] for row in review["removedItems"]] == ["adr", "issue-projection",
                                                               "research-dossier"]
    assert all(row["reason"].strip() for row in review["removedItems"])
    assert review["keptItems"] == [{"item": "tickets",
                                    "reason": "the control plane dispatches the first wave from them"}]

    code, revised = plan(tmp_path, capsys, review, OVER_DESIGNED)
    assert code == 0
    over, lean = library(OVER_DESIGNED), library(["tickets"])
    # Cut down: the artifacts nothing consumes are gone from the plan and from its gaps.
    assert revised["artifacts"] == lean["artifacts"] == load_profile("STANDARD")["required"] + ["tickets"]
    assert len(revised["artifacts"]) < len(over["artifacts"])
    assert not any(item in gap for gap in revised["unresolvedGaps"]
                   for item in ("adr", "issue-projection", "research-dossier"))
    # Without changing product scope: what the approval binds is the same plan, byte for byte.
    assert revised["planCore"] == json.loads(json.dumps(over["planCore"]))
    assert revised["diffSummary"]["planDigest"] == digest(over["planCore"])
    assert set(load_profile("STANDARD")["required"]) <= set(revised["artifacts"])
    assert revised["leanReview"] == {**review, "digest": digest(review)}


@pytest.mark.parametrize("optional, keep", [([], []), (["tickets"], ["--keep", TICKETS])],
                         ids=["nothing-optional", "every-option-consumed"])
def test_an_already_lean_spec_gets_a_pass_verdict_and_an_unchanged_plan(optional, keep, tmp_path, capsys):
    code, review = generate(tmp_path, capsys, *(["--optional", *optional] if optional else []), *keep)
    assert code == 0
    assert review["verdict"] == "LEAN_ACCEPT"
    assert review["removedItems"] == []

    code, accepted = plan(tmp_path, capsys, review, optional)
    assert code == 0
    unreviewed = library(optional)
    assert accepted["planCore"] == json.loads(json.dumps(unreviewed["planCore"]))
    assert accepted["artifacts"] == unreviewed["artifacts"]
    assert accepted["files"] == unreviewed["files"]


@pytest.mark.parametrize("item", ["compact-prd-or-equivalent-specification", "seed"])
def test_a_cut_into_scope_is_generated_as_a_ceo_decision_and_stops_the_plan(item, tmp_path, capsys):
    code, review = generate(tmp_path, capsys, "--remove", f"{item}=the seed is one sentence")
    assert code == 0
    assert review == {"verdict": "CEO_DECISION_REQUIRED",
                      "removedItems": [{"item": item, "reason": "the seed is one sentence"}]}

    code, refused = plan(tmp_path, capsys, review, [])
    assert code == 1
    assert refused.out == ""
    assert "CEO_DECISION_REQUIRED" in refused.err


@pytest.mark.parametrize("argv, refusal", [
    (["--keep", "adr=a decision exists"], "LEAN_ITEM_NOT_REQUESTED"),
    (["--remove", "adr=no decision"], "LEAN_ITEM_NOT_REQUESTED"),
    (["--optional", "adr", "--keep", "adr=a decision exists", "--remove", "adr=no decision"],
     "LEAN_REVIEW_INVALID"),
    (["--optional", "adr", "--keep", "adr="], "LEAN_REVIEW_INVALID"),
    (["--remove", "seed"], "LEAN_REVIEW_INVALID"),
    (["--optional", "prd"], "does not offer"),
], ids=["keep-unrequested", "cut-unrequested", "keep-and-cut", "no-consumer", "scope-cut-no-reason",
        "not-offered"])
def test_the_generator_refuses_a_verdict_the_plan_would_refuse(argv, refusal, tmp_path, capsys):
    code, error = generate(tmp_path, capsys, *argv)
    assert code == 1
    assert refusal in error
