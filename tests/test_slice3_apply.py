"""Slice 3 — external writes: provenance, re-read, and honest partial state (PRD §16)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from apply import (  # noqa: E402
    OPERATION_NOT_IN_PLAN, PLAN_INTENT_CHANGED, REREAD_MISMATCH, RESOURCE_COLLISION,
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


def plan(*names: str, request_digest: str = "sha256:" + "a" * 64) -> Dict[str, Any]:
    return {
        "bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
        "requestDigest": request_digest,
        "githubOperations": [
            {"operationId": f"create-repository:{n}", "resourceType": "repository",
             "intent": "create", "resourceIdentity": f"github:MongLong0214/{n}"}
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

def test_a_spec_for_an_unplanned_operation_is_refused_before_anything_is_written(tmp_path):
    port = FakeGitHub()

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan("alpha"), port, ledger(tmp_path),
                   specs={"create-repository:alpha": {}, "create-repository:smuggled": {"private": False}})

    assert caught.value.code == OPERATION_NOT_IN_PLAN
    assert "create-repository:smuggled" in caught.value.evidence["operations"]
    assert port.creates == []


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
