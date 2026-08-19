"""The compiler validates its input against the same schema that documents it.

`remoteOwner` was read by `scripts/plan.py` and set by a test, and `bootstrap-request.schema.json`
is `additionalProperties: false` and declared it nowhere. Every test passed, because no production
path validated a request: the compiler checked the plan it produced and never the request it was
handed. The schema said one thing, the compiler did another, and nothing compared them.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from plan import PlanError, compile_plan, remote_owner  # noqa: E402

OPERATION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VERIFICATION = [
    {"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True},
]
CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
             "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}
REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1",
    "runId": "run-contract",
    "seed": "a demo project",
    "bootstrapProfile": "STANDARD",
    "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "demo", "stack": "node"}],
    "visibility": "private",
    "origin": {"channel": "cli"},
}


def compile_request(request):
    return compile_plan(request, VERIFICATION, operation_id=OPERATION_ID, ci_values=CI_VALUES)


def test_the_schema_declares_the_field_the_compiler_reads():
    """Not `remoteOwner in properties` — the compiler's own acceptance of a request carrying it."""
    request = copy.deepcopy(REQUEST)
    request["remoteOwner"] = "some-other-account"
    compiled = compile_request(request)
    # The owner is not a decorative field: it is half of every repository identity the plan
    # will act on, so a request that names one and a plan that ignores it would create the
    # repository somewhere else.
    identities = [repo["identity"] for repo in compiled["planCore"]["repositories"]]
    assert identities == ["github:some-other-account/demo"]


def test_a_field_the_schema_does_not_declare_is_refused():
    request = copy.deepcopy(REQUEST)
    request["escalatePlease"] = True
    with pytest.raises(PlanError) as raised:
        compile_request(request)
    assert "bootstrap-request.schema.json" in str(raised.value)


def test_a_missing_required_field_is_refused_by_the_compiler_not_by_a_key_error():
    request = copy.deepcopy(REQUEST)
    del request["visibility"]
    with pytest.raises(PlanError) as raised:
        compile_request(request)
    assert "bootstrap-request.schema.json" in str(raised.value)


def test_a_malformed_owner_is_refused_rather_than_becoming_part_of_a_repository_identity():
    request = copy.deepcopy(REQUEST)
    request["remoteOwner"] = "not/an/owner"
    with pytest.raises(PlanError):
        compile_request(request)


def test_the_owner_is_read_from_the_request_and_not_from_its_provenance():
    """`origin` says where the request came from. The owner says where the repositories go.
    Reading a target out of a provenance object put the same fact in two places, and only one
    of them was declared."""
    request = copy.deepcopy(REQUEST)
    request["origin"] = {"channel": "cli", "remoteOwner": "smuggled-account"}
    with pytest.raises(PlanError) as raised:
        compile_request(request)
    assert "bootstrap-request.schema.json" in str(raised.value)
    assert remote_owner({"origin": {"remoteOwner": "smuggled-account"}}) != "smuggled-account"


def test_the_declared_schema_id_matches_what_the_compiler_validates_against():
    """A guard pointed at the wrong document is not a guard. This reads the file the compiler
    reads, so renaming it breaks here rather than silently disabling validation."""
    schema = json.loads((SKILL / "schemas" / "bootstrap-request.schema.json").read_text(encoding="utf-8"))
    assert schema.get("additionalProperties") is False
    assert "remoteOwner" in schema["properties"]
