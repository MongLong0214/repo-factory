"""Slice 1 — canonical intent digest and the bootstrap plan compiler."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from canonical import CanonicalError, digest, volatile_findings  # noqa: E402
from plan import PlanError, compile_plan, diff_summary  # noqa: E402

VERIFICATION = [
    {"id": "typecheck", "argv": ["npm", "run", "typecheck"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True, "tier": "simple"},
    {"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 1200, "envAllowlist": ["CI"], "network": "deny", "required": True, "tier": "standard"},
    {"id": "security", "argv": ["npm", "audit", "--audit-level=high"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "allowlist", "required": True, "tier": "guarded"},
]

REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1",
    "runId": "run-1",
    "seed": "a small tool that reconciles two ledgers",
    "bootstrapProfile": "STANDARD",
    "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "ledger-reconciler"}],
    "visibility": "private",
    "origin": {"channel": "cli", "requestedAt": "2026-08-19T09:00:00Z"},
}

FIXED_OP = "11111111-2222-3333-4444-555555555555"


def compiled(request=None, **kwargs):
    return compile_plan(request or copy.deepcopy(REQUEST), VERIFICATION,
                        operation_id=FIXED_OP, **kwargs)


# --- canonicalisation (PRD §8.3) ------------------------------------------------------

def test_key_order_is_not_intent_but_array_order_is():
    assert digest({"b": 1, "a": 2}) == digest({"a": 2, "b": 1})
    assert digest({"a": [1, 2]}) != digest({"a": [2, 1]})


def test_newlines_are_normalised_so_a_checkout_setting_is_not_intent():
    assert digest({"body": "one\r\ntwo"}) == digest({"body": "one\ntwo"})


def test_plan_core_refuses_volatile_values_instead_of_dropping_them():
    # Dropping silently would let two plans that differ produce one digest — the property
    # the digest exists to deny.
    for bad in [{"observedAt": "x"}, {"sessionId": "s"}, {"providerUsage": {}}, {"p": "/Users/isaac/x"}]:
        with pytest.raises(CanonicalError):
            digest(bad)


@pytest.mark.parametrize("key", ["candidate", "update", "format", "state", "validate", "repeat",
                                 "bootstrapProfile", "requestDigest", "identity"])
def test_the_volatile_check_does_not_refuse_ordinary_keys(key):
    # A suffix-only rule refuses `format`, which this repository's own request schema uses.
    assert volatile_findings({key: "x"}) == []


# --- RF-S04 ---------------------------------------------------------------------------

def test_rf_s04_a_different_timestamp_is_the_same_plan():
    later = copy.deepcopy(REQUEST)
    later["origin"]["requestedAt"] = "2026-12-25T23:59:59Z"

    assert digest(compiled()["planCore"]) == digest(compiled(later)["planCore"])


def test_a_changed_operation_is_a_different_plan():
    renamed = copy.deepcopy(REQUEST)
    renamed["repositories"][0]["name"] = "ledger-reconciler-2"

    assert digest(compiled()["planCore"]) != digest(compiled(renamed)["planCore"])


def test_a_changed_verification_contract_is_a_different_plan():
    weakened = [dict(VERIFICATION[0], required=False)] + VERIFICATION[1:]
    other = compile_plan(copy.deepcopy(REQUEST), weakened, operation_id=FIXED_OP)

    assert digest(compiled()["planCore"]) != digest(other["planCore"])


# --- plan shape -----------------------------------------------------------------------

def test_the_plan_core_validates_against_its_own_schema():
    schema = json.loads((SKILL / "schemas" / "bootstrap-plan.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(compiled()["planCore"]), key=str)
    assert not errors, "\n".join(str(e) for e in errors)


def test_the_committed_manifest_carries_nothing_machine_specific():
    # PRD §10.2. `forbid` refuses absolute paths, session identity and provider usage, so the
    # manifest being digestible at all is the check.
    digest(compiled()["projectManifest"])


def test_verification_profiles_widen_with_the_tier():
    profiles = compiled()["projectManifest"]["verificationProfiles"]
    assert profiles["simple"] == ["typecheck"]
    assert profiles["standard"] == ["typecheck", "test"]
    assert profiles["guarded"] == ["typecheck", "test", "security"]


def test_the_tier_hint_does_not_reach_the_committed_manifest():
    for command in compiled()["projectManifest"]["verificationCommands"]:
        assert "tier" not in command, "tier selects profiles at compile time; it is not project contract"


# --- human gate (PRD §7 Phase F) -------------------------------------------------------

def test_hermes_may_authorise_a_private_reversible_setup():
    assert compiled()["humanGate"]["authorization"] == "HERMES"
    assert compiled()["planCore"]["authorization"] == "HERMES"


def test_public_exposure_is_an_owner_decision_even_when_the_request_asked_for_it():
    public = copy.deepcopy(REQUEST)
    public["visibility"] = "public"
    result = compiled(public)

    assert result["humanGate"]["authorization"] == "OWNER"
    assert [r["gate"] for r in result["humanGate"]["reasons"]] == ["public-exposure"]
    # The plan carries the classification, so an apply step cannot read past it.
    assert result["planCore"]["authorization"] == "OWNER"


def test_a_declared_human_gate_fact_escalates_without_the_visibility_flag():
    destructive = copy.deepcopy(REQUEST)
    destructive["humanGateFacts"] = ["destructive-replacement of the existing repository"]

    assert compiled(destructive)["humanGate"]["authorization"] == "OWNER"


# --- profile-aware artifact selection (PRD §6) -----------------------------------------

def test_an_optional_artifact_the_profile_does_not_offer_is_refused_not_dropped():
    with pytest.raises(PlanError, match="does not offer"):
        compiled(requested_optional=["adversarial-evidence-search"])


def test_a_requested_optional_artifact_is_selected_and_ordering_stays_stable():
    first = compiled(requested_optional=["adr", "tickets"])["artifacts"]
    second = compiled(requested_optional=["tickets", "adr"])["artifacts"]

    assert first == second
    assert first[-2:] == ["adr", "tickets"]


def test_simple_does_not_silently_gain_the_standard_specification():
    simple = copy.deepcopy(REQUEST)
    simple["bootstrapProfile"] = "SIMPLE"

    assert "compact-prd-or-equivalent-specification" not in compiled(simple)["artifacts"]


# --- summary --------------------------------------------------------------------------

def test_the_diff_summary_names_the_owner_gates_it_would_cross():
    public = copy.deepcopy(REQUEST)
    public["visibility"] = "public"
    summary = diff_summary(compiled(public))

    assert summary["ownerGates"] == ["public-exposure"]
    assert summary["planDigest"].startswith("sha256:")
    assert summary["githubOperations"] == ["create-repository:ledger-reconciler"]
