"""Slice 3 — external writes: provenance, re-read, and honest partial state (PRD §16)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from apply import (  # noqa: E402
    PHASE_OUT_OF_ORDER, PLAN_INTENT_CHANGED, REREAD_MISMATCH, RESOURCE_COLLISION, UNKNOWN_PHASE,
    ApplyError, ReceiptLedger, apply_plan,
)


class FakeGitHub:
    """실제 포트와 같은 계약. `existing` 은 우리가 만들지 않은 원격 상태다."""

    def __init__(self, existing: Dict[str, Dict[str, Any]] = None, vanish: List[str] = None):
        self.state: Dict[str, Dict[str, Any]] = dict(existing or {})
        self.vanish = set(vanish or [])
        self.creates: List[str] = []

    def observe(self, resource_type: str, identity: str) -> Optional[Dict[str, Any]]:
        return self.state.get(identity)

    def create(self, resource_type: str, identity: str, spec: Dict[str, Any]) -> None:
        self.creates.append(identity)
        # `vanish` 는 명령이 성공했는데 원격에 없는 경우다. exit 0 이 증거가 아닌 이유.
        if identity not in self.vanish:
            self.state[identity] = {"identity": identity, "type": resource_type, **spec}


def plan(*names: str, request_digest: str = "sha256:" + "a" * 64,
         desired: Dict[str, Any] = None, visibility: str = "private") -> Dict[str, Any]:
    """`desiredState` 는 Operation 안에 있다. Plan 밖 인자로 옮기면 승인된 digest 가
    실행될 effect 를 결정하지 못한다."""
    return {
        "bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
        "requestDigest": request_digest,
        "repositories": [
            {"role": "primary", "identity": f"github:MongLong0214/{n}", "visibility": visibility}
            for n in names
        ],
        "githubOperations": [
            {"operationId": f"create-repository:{n}", "resourceType": "repository",
             "intent": "create", "resourceIdentity": f"github:MongLong0214/{n}",
             "desiredState": dict(desired) if desired is not None
             else {"private": visibility != "public"}}
            for n in names
        ],
    }


def ledger(tmp_path: Path) -> ReceiptLedger:
    return ReceiptLedger(tmp_path / "receipts.json")


# --- RF-S14: post-write reread ---------------------------------------------------------

def test_every_applied_resource_carries_a_verified_receipt(tmp_path):
    port = FakeGitHub()
    result = apply_plan(plan("alpha", "beta"), port, ledger(tmp_path))

    assert result["completed"] is True
    assert [r["resourceIdentity"] for r in result["receipts"]] == [
        "github:MongLong0214/alpha", "github:MongLong0214/beta"]
    assert all(r["verified"] and r["afterStateDigest"].startswith("sha256:") for r in result["receipts"])
    assert all(r["preexisting"] is False for r in result["receipts"])


def test_a_successful_create_that_is_absent_on_reread_is_not_a_completion(tmp_path):
    # PRD §16.2 — Command Exit 0 만으로 완료 증거가 되지 않는다.
    port = FakeGitHub(vanish=["github:MongLong0214/alpha"])

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha"), port, ledger(tmp_path))

    assert caught.value.code == REREAD_MISMATCH
    assert port.creates == ["github:MongLong0214/alpha"], "the create was issued; the reread is what failed"
    assert caught.value.receipts == []


# --- RF-S15: provenance, not name ------------------------------------------------------

def test_an_unrelated_resource_of_the_same_name_is_a_collision_and_changes_nothing(tmp_path):
    port = FakeGitHub(existing={"github:MongLong0214/alpha": {"identity": "someone else's"}})

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha"), port, ledger(tmp_path))

    assert caught.value.code == RESOURCE_COLLISION
    assert port.creates == [], "nothing may be written on top of a resource we cannot claim"
    assert port.state["github:MongLong0214/alpha"] == {"identity": "someone else's"}


def test_a_resource_we_already_verified_is_resumed_rather_than_recreated(tmp_path):
    book = ledger(tmp_path)
    port = FakeGitHub()
    apply_plan(plan("alpha"), port, book)

    again = apply_plan(plan("alpha"), port, ReceiptLedger(book.path))

    assert port.creates == ["github:MongLong0214/alpha"], "resume must not write a second time"
    assert again["completed"] is True
    assert again["receipts"][0]["operationId"] == "create-repository:alpha"


def test_the_same_resource_under_a_changed_intent_is_refused(tmp_path):
    # §16.3 requires the same operation *and* the same intent. A plan that changed is not a
    # resume, and treating it as one applies an approval to something else.
    book = ledger(tmp_path)
    apply_plan(plan("alpha"), FakeGitHub(), book)

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha", request_digest="sha256:" + "b" * 64), FakeGitHub(), ReceiptLedger(book.path))

    assert caught.value.code == PLAN_INTENT_CHANGED


# --- RF-S24: plan-before-apply ---------------------------------------------------------

def test_an_operation_that_would_create_something_other_than_what_was_approved_is_refused(tmp_path):
    # The effect used to arrive in a separate `specs` argument, so an approved private plan and
    # an executed public repository had the same plan digest — the gate read one object and the
    # port wrote another. The effect is inside the operation now, and the two places that state
    # a repository's exposure have to agree before anything is written.
    port = FakeGitHub()
    smuggled = plan("alpha")
    smuggled["githubOperations"][0]["desiredState"] = {"private": False}

    with pytest.raises(ApplyError) as caught:
        apply_plan(smuggled, port, ledger(tmp_path))

    assert caught.value.code == PLAN_INTENT_CHANGED
    assert port.creates == []


def test_a_resource_created_in_a_state_the_plan_did_not_approve_is_refused(tmp_path):
    # A re-read that only asks "is it there?" passes a ruleset created `disabled` exactly as it
    # passes one created `active`. §16.2 asks whether what was approved is what is there.
    class Drifting(FakeGitHub):
        def create(self, resource_type, identity, spec):
            self.creates.append(identity)
            self.state[identity] = {**spec, "identity": identity, "enforcement": "disabled"}

    port = Drifting()
    drifted = plan("alpha")
    drifted["githubOperations"] = [
        {"operationId": "create-ruleset:alpha", "resourceType": "ruleset", "intent": "create",
         "resourceIdentity": "github:MongLong0214/alpha#acp-managed-branches",
         "desiredState": {"enforcement": "active", "target": "branch"}},
    ]

    with pytest.raises(ApplyError) as caught:
        apply_plan(drifted, port, ledger(tmp_path))

    assert caught.value.code == REREAD_MISMATCH
    assert any("enforcement" in gap for gap in caught.value.evidence["gaps"])


# --- RF-S16: partial apply and deterministic resume ------------------------------------

def test_a_partial_apply_reports_what_completed_rather_than_claiming_atomicity(tmp_path):
    # PRD §16.4 — 가짜 Atomicity 를 주장하지 않는다.
    port = FakeGitHub(existing={"github:MongLong0214/beta": {"identity": "not ours"}})

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha", "beta"), port, ledger(tmp_path))

    assert caught.value.code == RESOURCE_COLLISION
    assert [r["resourceIdentity"] for r in caught.value.receipts] == ["github:MongLong0214/alpha"]


def test_resume_after_a_partial_apply_starts_from_the_verified_receipt(tmp_path):
    book = ledger(tmp_path)
    blocked = FakeGitHub(existing={"github:MongLong0214/beta": {"identity": "not ours"}})
    with pytest.raises(ApplyError):
        apply_plan(plan("alpha", "beta"), blocked, book)

    # The collision is resolved out of band; the same plan is run again against a fresh process.
    cleared = FakeGitHub(existing={"github:MongLong0214/alpha": {"identity": "ours from round one"}})
    result = apply_plan(plan("alpha", "beta"), cleared, ReceiptLedger(book.path))

    assert result["completed"] is True
    assert cleared.creates == ["github:MongLong0214/beta"], "alpha is resumed from its receipt, not rebuilt"


def test_the_ledger_survives_the_process_that_wrote_it(tmp_path):
    book = ledger(tmp_path)
    apply_plan(plan("alpha"), FakeGitHub(), book)

    reloaded = ReceiptLedger(book.path)

    assert [r["operationId"] for r in reloaded.all()] == ["create-repository:alpha"]
    assert reloaded.get("create-repository:alpha")["verified"] is True


def test_a_receipt_is_written_before_the_next_operation_is_attempted(tmp_path):
    # Without this the process dying mid-plan loses the resume point and the next run
    # re-creates a resource it already made — which then reads as a collision against itself.
    book = ledger(tmp_path)
    port = FakeGitHub(existing={"github:MongLong0214/beta": {"identity": "not ours"}})
    with pytest.raises(ApplyError):
        apply_plan(plan("alpha", "beta"), port, book)

    assert ReceiptLedger(book.path).get("create-repository:alpha") is not None


def test_a_receipt_for_a_resource_that_no_longer_exists_is_not_a_resume(tmp_path):
    # A receipt says a write happened, not that its result is still there. Trusting it without
    # looking reports a deleted repository as completed, and every later step builds on that.
    from apply import RESUMED_RESOURCE_ABSENT

    book = ledger(tmp_path)
    apply_plan(plan("alpha"), FakeGitHub(), book)

    vanished = FakeGitHub()  # the remote no longer has it
    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha"), vanished, ReceiptLedger(book.path))

    assert caught.value.code == RESUMED_RESOURCE_ABSENT
    assert vanished.creates == [], "a disagreement between ledger and remote is not fixed by writing"


def test_the_receipt_states_when_each_observation_happened(tmp_path):
    # One value in both fields lets a receipt claim a re-read at a moment the re-read had not
    # happened. §16.2 asks for the fact of a re-read; the receipt has to say when it was.
    ticks = iter(["2026-08-19T00:00:01Z", "2026-08-19T00:00:02Z"])
    port = FakeGitHub()

    result = apply_plan(plan("alpha"), port, ledger(tmp_path), clock=lambda: next(ticks))
    receipt = result["receipts"][0]

    assert receipt["createdAt"] == "2026-08-19T00:00:01Z"
    assert receipt["rereadAt"] == "2026-08-19T00:00:02Z"
    assert receipt["createdAt"] < receipt["rereadAt"]


def test_a_default_clock_still_produces_a_usable_receipt(tmp_path):
    result = apply_plan(plan("alpha"), FakeGitHub(), ledger(tmp_path))
    receipt = result["receipts"][0]

    assert receipt["createdAt"].endswith("Z") and receipt["rereadAt"].endswith("Z")
    assert receipt["createdAt"] <= receipt["rereadAt"]


def test_a_ledger_write_that_dies_leaves_the_previous_one_readable(tmp_path, monkeypatch):
    # Checking a successful write proves nothing: an in-place write also finishes cleanly when
    # nothing goes wrong. The property only exists in the failure — a write that dies mid-flight
    # must not leave truncated JSON, because the next run then cannot read the ledger at all and
    # the file kept to protect the resume point becomes what blocks it.
    import apply as apply_module
    import json as _json

    book = ledger(tmp_path)
    apply_plan(plan("alpha"), FakeGitHub(), book)
    intact = book.path.read_text(encoding="utf-8")

    monkeypatch.setattr(apply_module.os, "replace",
                        lambda *_: (_ for _ in ()).throw(OSError("crash during publish")))
    # alpha is already there, so the resume check passes and beta is the operation whose
    # receipt write dies.
    resumed = FakeGitHub(existing={"github:MongLong0214/alpha": {"identity": "ours"}})
    with pytest.raises(OSError):
        apply_plan(plan("alpha", "beta"), resumed, ReceiptLedger(book.path))

    assert book.path.read_text(encoding="utf-8") == intact, "the previous ledger was damaged"
    assert len(_json.loads(book.path.read_text(encoding="utf-8"))) == 1


def phased_plan(*names: str):
    core = plan(*names)
    core["githubOperations"] = [dict(op, phase="before-files") for op in core["githubOperations"]] + [
        {"operationId": f"create-ruleset:{n}", "resourceType": "ruleset", "intent": "create",
         "resourceIdentity": f"github:MongLong0214/{n}#acp-managed-branches", "phase": "after-files",
         "desiredState": {"enforcement": "active", "target": "branch"}}
        for n in names
    ]
    return core


def test_only_the_named_phase_is_applied(tmp_path):
    # A ruleset requiring project-ci cannot exist before the commit that publishes that
    # workflow — it would refuse the push that gives the repository its content. The phase is
    # the plan's decision, not the caller's, so applying one does not reach the other.
    port = FakeGitHub()
    book = ledger(tmp_path)

    before = apply_plan(phased_plan("alpha"), port, book, phase="before-files")

    assert [r["operationId"] for r in before["receipts"]] == ["create-repository:alpha"]
    assert before["completed"] is True
    assert port.creates == ["github:MongLong0214/alpha"]


def genesis_receipt(name: str) -> Dict[str, Any]:
    identity = f"github:MongLong0214/{name}"
    return {"bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
            "requestDigest": "sha256:" + "a" * 64,
            "operationId": f"publish:{identity}", "resourceType": "genesis-commit",
            "resourceIdentity": identity, "createdAt": "2026-08-19T10:00:00Z",
            "rereadAt": "2026-08-19T10:00:00Z", "verified": True}


def test_the_later_phase_applies_against_the_same_ledger(tmp_path):
    port = FakeGitHub()
    book = ledger(tmp_path)
    apply_plan(phased_plan("alpha"), port, book, phase="before-files")
    book.record(genesis_receipt("alpha"))

    after = apply_plan(phased_plan("alpha"), port, ReceiptLedger(book.path), phase="after-files")

    assert [r["operationId"] for r in after["receipts"]] == ["create-ruleset:alpha"]
    assert "github:MongLong0214/alpha#acp-managed-branches" in port.creates
    # Both operation receipts and the genesis receipt survive in one ledger.
    assert len(ReceiptLedger(book.path).all()) == 3


def test_the_later_phase_refuses_while_the_earlier_one_is_unfinished(tmp_path):
    # The order used to be a comment and a `phase` field the caller chose to honour. A caller
    # could apply after-files first, and a ruleset requiring project-ci that exists before the
    # commit carrying that workflow refuses the very push that gives the repository content.
    with pytest.raises(ApplyError) as caught:
        apply_plan(phased_plan("alpha"), FakeGitHub(), ledger(tmp_path), phase="after-files")

    assert caught.value.code == PHASE_OUT_OF_ORDER
    assert "create-repository:alpha" in caught.value.evidence["waitingOn"]


def test_the_later_phase_refuses_until_the_genesis_commit_is_published(tmp_path):
    port = FakeGitHub()
    book = ledger(tmp_path)
    apply_plan(phased_plan("alpha"), port, book, phase="before-files")

    with pytest.raises(ApplyError) as caught:
        apply_plan(phased_plan("alpha"), port, ReceiptLedger(book.path), phase="after-files")

    assert caught.value.code == PHASE_OUT_OF_ORDER
    assert caught.value.evidence["waitingOn"] == ["github:MongLong0214/alpha"]


def test_a_phase_the_plan_does_not_have_is_refused_rather_than_reported_complete(tmp_path):
    # An unknown phase staged zero operations, and zero staged operations satisfied
    # `completed = len(applied) == len(staged)`. A typo reported a finished bootstrap.
    with pytest.raises(ApplyError) as caught:
        apply_plan(phased_plan("alpha"), FakeGitHub(), ledger(tmp_path), phase="after-file")

    assert caught.value.code == UNKNOWN_PHASE
