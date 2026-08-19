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
    "visibility": "public",
    "origin": {"channel": "cli", "requestedAt": "2026-08-19T09:00:00Z"},
}

FIXED_OP = "11111111-2222-3333-4444-555555555555"
CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
             "TEST_CMD": "npm test", "BUILD_CMD": "true"}


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

def test_a_plan_this_factory_cannot_finish_is_not_compiled(tmp_path=None):
    """모든 프로필이 ruleset 을 계획하고, private 저장소의 ruleset 은 GitHub Pro 를 요구한다.
    이 배포는 Pro 를 쓰지 않으므로 private Plan 은 **반드시** after-files 에서 죽는다 —
    그때는 이미 원격 저장소가 하나 실재한다. 만들 수 없는 것은 계획하지 않는다."""
    private = copy.deepcopy(REQUEST)
    private["visibility"] = "private"

    with pytest.raises(PlanError, match="public repositories"):
        compiled(private)


def test_public_exposure_is_an_owner_decision_even_when_the_request_asked_for_it():
    result = compiled()

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
    # The ruleset is planned too, and after the files: a rule requiring project-ci cannot
    # exist before the commit that publishes that workflow. The default branch is an operation
    # as well — push order decides which branch GitHub picks, and nothing re-reads what it did.
    #
    # Secret scanning comes **before** the files, and the order is the guarantee: push
    # protection that stands up after the genesis push did not protect the genesis push.
    assert summary["githubOperations"] == ["create-repository:ledger-reconciler",
                                           "enable-secret-scanning:ledger-reconciler",
                                           "set-default-branch:ledger-reconciler",
                                           "create-ruleset:ledger-reconciler"]


def test_the_operation_id_must_be_supplied_rather_than_invented():
    # §16.3 hangs resume on operation identity. A fresh uuid per call turns a retry into a new
    # operation against the same intent, and the ledger — correctly — refuses to recognise it.
    # A default is what makes that mistake easy.
    with pytest.raises(PlanError, match="must be supplied"):
        compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id="")


def test_the_request_stack_is_used_and_a_disagreement_is_refused():
    request = copy.deepcopy(REQUEST)
    request["repositories"][0]["stack"] = "python"

    resolved = compile_plan(request, VERIFICATION, operation_id=FIXED_OP,
                            ci_values={"RUNTIME_LOWER": "3.11", "RUNTIME_LATEST": "3.13",
                                       "INSTALL_CMD": "true", "TEST_CMD": "true", "BUILD_CMD": "true"})
    assert ".github/workflows/project-ci.yml" in resolved["files"]

    with pytest.raises(PlanError, match="declares stack"):
        compile_plan(request, VERIFICATION, operation_id=FIXED_OP, stack="node", ci_values=CI_VALUES)


def test_the_compiler_refuses_a_plan_that_does_not_satisfy_its_own_schema(monkeypatch):
    # Validating the *output* here would pass whether or not the compiler checks anything — the
    # compiler produces valid plans by construction, so the guard would be unfalsifiable and the
    # test would be measuring the schema rather than the guard. Breaking one field the compiler
    # computes is what makes the refusal observable.
    import plan as plan_module

    monkeypatch.setattr(plan_module, "content_digest", lambda _text: "not-a-digest")

    with pytest.raises(PlanError, match="bootstrap-plan.schema.json"):
        compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id=FIXED_OP)


def test_the_remote_owner_comes_from_the_request():
    # It was written into four places in this file. A request naming another account went to
    # this one silently, which is the shape of defect that only shows up on someone else's
    # machine.
    elsewhere = copy.deepcopy(REQUEST)
    elsewhere["remoteOwner"] = "someone-else"

    compiled = compile_plan(elsewhere, VERIFICATION, operation_id=FIXED_OP)

    assert compiled["planCore"]["repositories"][0]["identity"].startswith("github:someone-else/")
    assert compiled["planCore"]["githubOperations"][0]["resourceIdentity"].startswith("github:someone-else/")
    assert compiled["projectManifest"]["repositories"][0]["remote"].startswith("github:someone-else/")


def test_the_deployment_default_is_used_when_the_request_says_nothing():
    compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id=FIXED_OP)

    assert compiled["planCore"]["repositories"][0]["identity"].startswith("github:MongLong0214/")


# --- EnvironmentObservation (PRD §8.2) --------------------------------------------------

def test_observing_the_same_facts_twice_gives_the_same_snapshot_id():
    # If observedAt reached the id, the plan that references it would change every time the
    # same environment was looked at — which is the property §8.3 forbids, arriving through the
    # reference rather than through the bytes.
    from plan import environment_snapshot_id, observe_environment

    first = observe_environment(REQUEST, clock=lambda: "2026-08-19T00:00:00Z")
    later = observe_environment(REQUEST, clock=lambda: "2026-12-25T23:59:59Z")

    assert first["observedAt"] != later["observedAt"]
    assert environment_snapshot_id(first) == environment_snapshot_id(later)


def test_a_changed_fact_gives_a_different_snapshot_id():
    from plan import environment_snapshot_id, observe_environment

    class Taken:
        def observe(self, _type, _identity):
            return {"identity": "someone else's"}

    class Free:
        def observe(self, _type, _identity):
            return None

    taken = observe_environment(REQUEST, port=Taken(), clock=lambda: "2026-08-19T00:00:00Z")
    free = observe_environment(REQUEST, port=Free(), clock=lambda: "2026-08-19T00:00:00Z")

    assert taken["repositories"][0]["remoteNameAvailable"] is False
    assert free["repositories"][0]["remoteNameAvailable"] is True
    assert environment_snapshot_id(taken) != environment_snapshot_id(free)


def test_an_unobserved_name_is_recorded_as_unobserved_not_as_available():
    # None means "not looked at". Writing it as False or True would make an unchecked name read
    # as a checked one, and the plan would rest on an observation nobody made.
    from plan import observe_environment

    unchecked = observe_environment(REQUEST, clock=lambda: "2026-08-19T00:00:00Z")

    assert unchecked["repositories"][0]["remoteNameAvailable"] is None


def test_a_port_that_fails_records_why_rather_than_guessing():
    from plan import observe_environment

    class Broken:
        def observe(self, _type, _identity):
            raise RuntimeError("rate limited")

    observed = observe_environment(REQUEST, port=Broken(), clock=lambda: "2026-08-19T00:00:00Z")

    assert observed["repositories"][0]["remoteNameAvailable"] is None
    assert "rate limited" in observed["repositories"][0]["notObserved"]


def test_the_plan_references_the_observation_and_stays_stable_across_re_observation():
    from plan import observe_environment

    early = observe_environment(REQUEST, clock=lambda: "2026-08-19T00:00:00Z")
    late = observe_environment(REQUEST, clock=lambda: "2026-12-25T23:59:59Z")

    a = compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id=FIXED_OP, environment=early)
    b = compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id=FIXED_OP, environment=late)

    assert a["planCore"]["environmentSnapshotId"].startswith("sha256:")
    assert digest(a["planCore"]) == digest(b["planCore"])


def test_a_plan_without_an_observation_carries_no_reference():
    compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION, operation_id=FIXED_OP)

    assert "environmentSnapshotId" not in compiled["planCore"]


def test_the_ruleset_body_is_in_the_plan_and_not_only_its_name(tmp_path=None):
    """A plan that carries only the ruleset's name approves a name. The protection it actually
    provides — whether it is enforced, which refs it covers, which check it requires, who may
    bypass it — decided the strength, and none of that entered the approved digest. Two plans
    that differ only in `enforcement` had the same digest."""
    core = compile_plan(REQUEST, VERIFICATION, stack="node", ci_values=CI_VALUES,
                        operation_id="11111111-2222-3333-4444-555555555555")["planCore"]
    ruleset = next(o for o in core["githubOperations"] if o["resourceType"] == "ruleset")
    state = ruleset["desiredState"]

    assert state["enforcement"] == "active"
    assert state["target"] == "branch"
    assert state["conditions"]["ref_name"]["include"] == ["refs/heads/main", "refs/heads/dev"]
    assert state["bypass_actors"] == []
    checks = next(r for r in state["rules"] if r["type"] == "required_status_checks")
    # 보고자까지 못 박는다. context 만 요구하면 그 이름으로 check-run 을 만드는 어떤 앱이든
    # 규칙을 충족시키고, 워크플로가 돌지 않아도 머지가 열린다.
    assert checks["parameters"]["required_status_checks"] == [
        {"context": "project-ci", "integration_id": 15368}]


def test_the_repository_operation_states_the_exposure_it_will_create(tmp_path=None):
    """The gate reads `repositories[].visibility`; the port writes the operation's state. Both
    have to say the same thing, in the plan, or the exposure under approval and the exposure
    created are two different facts and the digest cannot tell them apart."""
    core = compile_plan(REQUEST, VERIFICATION, stack="node", ci_values=CI_VALUES,
                        operation_id="11111111-2222-3333-4444-555555555555")["planCore"]
    repository_op = next(o for o in core["githubOperations"] if o["resourceType"] == "repository")

    assert repository_op["desiredState"] == {"private": False}
    assert all(r["visibility"] == "public" for r in core["repositories"])


def test_two_plans_that_differ_only_in_ruleset_strength_have_different_digests(tmp_path=None):
    compiled = compile_plan(REQUEST, VERIFICATION, stack="node", ci_values=CI_VALUES,
                            operation_id="11111111-2222-3333-4444-555555555555")["planCore"]
    weakened = copy.deepcopy(compiled)
    ruleset = next(o for o in weakened["githubOperations"] if o["resourceType"] == "ruleset")
    ruleset["desiredState"]["enforcement"] = "disabled"

    assert digest(compiled) != digest(weakened)


def test_the_ruleset_is_planned_after_the_files_and_the_repository_before_them():
    # Asserted on the compiler's own output, not a hand-built plan: the phase is the compiler's
    # decision and a test that supplies its own phases proves nothing about it. A ruleset
    # requiring project-ci that exists before the commit publishing that workflow refuses the
    # push which gives the repository its content.
    operations = {op["operationId"]: op for op in compiled()["planCore"]["githubOperations"]}

    assert operations["create-repository:ledger-reconciler"]["phase"] == "before-files"
    assert operations["create-ruleset:ledger-reconciler"]["phase"] == "after-files"


def test_the_ruleset_identity_carries_the_name_the_plan_chose():
    # GitHub assigns the id, so before the ruleset exists the name is the only handle. It has to
    # travel in the identity or the receipt cannot find what it created.
    from plan import RULESET_NAME

    operations = {op["operationId"]: op for op in compiled()["planCore"]["githubOperations"]}

    assert operations["create-ruleset:ledger-reconciler"]["resourceIdentity"].endswith(f"#{RULESET_NAME}")


def test_push_protection_stands_before_the_push_it_protects():
    """secret scanning 은 파일보다 **앞** 단계다.

    push protection 은 자격증명이 착지하는 것을 막는 것이다. genesis push 뒤에 켜면 그 첫
    푸시 — 공장이 만든 파일 전부가 처음 올라가는 그 푸시 — 는 보호 밖에서 지나간다.
    `after-files` 로 옮겨도 Plan 은 멀쩡해 보이고 영수증도 전부 검증되므로, 이 순서를 보는
    것이 없으면 아무도 모른다."""
    core = compiled()["planCore"]
    phases = {o["operationId"]: o.get("phase") for o in core["githubOperations"]}

    assert phases["enable-secret-scanning:ledger-reconciler"] == "before-files"
    # 저장소가 있어야 그 설정이 존재한다 — 저장소 생성보다는 뒤여야 한다.
    ids = [o["operationId"] for o in core["githubOperations"]]
    assert ids.index("create-repository:ledger-reconciler") < ids.index(
        "enable-secret-scanning:ledger-reconciler")
