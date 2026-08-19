"""What actually lands in a generated repository, and what must never (PRD §4.6, §14.1)."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from materialize import CI_PATH, MANIFEST_PATH, materialize  # noqa: E402
from plan import compile_plan  # noqa: E402

REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "r", "seed": "reconciles two ledgers",
    "bootstrapProfile": "SIMPLE", "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "ledger-reconciler"}],
    "visibility": "public", "origin": {"channel": "cli"},
}
VERIFICATION = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
                 "timeoutSeconds": 1200, "envAllowlist": ["CI"], "network": "deny", "required": True}]
CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "PACKAGE_MANAGER": "npm",
             "INSTALL_CMD": "npm ci", "TEST_CMD": "npm test", "BUILD_CMD": "npm run build"}

# §4.6 forbids committing *values*, not the words for them. A prose line saying "provider
# routing is not recorded here" is documentation; a check that refuses it would force the file
# to stop explaining itself while catching nothing real. So these are value shapes.
LEAK_SHAPES = [
    (r"(?<![\w.])/(?:Users|home|var|etc|opt)/", "an absolute filesystem path"),
    (r"~/", "a home-relative path"),
    (r"\b[A-Za-z]:[\\/]", "a drive-lettered path"),
    (r"\b(gh[pousr]_[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})", "a credential"),
    (r"\b\d{9,}\b", "a bare long numeric id, which is the shape of a Telegram identity"),
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "a session or operation uuid"),
]


def compiled(**kwargs):
    return compile_plan(copy.deepcopy(REQUEST), VERIFICATION,
                        operation_id="11111111-2222-3333-4444-555555555555", **kwargs)


def test_a_plan_without_a_resolved_stack_renders_no_ci_and_says_so():
    result = compiled()

    assert CI_PATH not in result["files"]
    assert any("stack-specific CI" in gap for gap in result["unresolvedGaps"])


def test_a_resolved_stack_renders_the_workflow_and_leaves_no_gap():
    result = compiled(stack="node", ci_values=CI_VALUES)

    assert CI_PATH in result["files"]
    assert "name: project-ci" in result["files"][CI_PATH]
    assert result["unresolvedGaps"] == []


def test_an_unknown_stack_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="no reviewed template"):
        compiled(stack="cobol", ci_values=CI_VALUES)


def test_every_planned_file_digest_is_the_digest_of_what_would_be_written():
    # Otherwise the plan describes bytes that do not exist and the authorisation means nothing.
    result = compiled(stack="node", ci_values=CI_VALUES)
    by_path = {f["path"]: f["contentDigest"] for f in result["planCore"]["files"]}

    assert set(by_path) == set(result["files"])
    for path, content in result["files"].items():
        assert by_path[path] == "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_the_committed_manifest_file_is_the_manifest():
    result = compiled()

    assert json.loads(result["files"][MANIFEST_PATH]) == result["projectManifest"]


@pytest.mark.parametrize("pattern,what", LEAK_SHAPES)
def test_the_generated_repository_carries_no_machine_specific_value(pattern: str, what: str):
    # PRD §4.6. Copying a control-plane fact into a repository puts it in two places, and the
    # copy is the one nobody updates.
    result = compiled(stack="node", ci_values=CI_VALUES)
    for path, content in result["files"].items():
        found = re.search(pattern, content)
        assert not found, f"{path} carries {what}: {found.group(0)!r}"


def test_the_leak_check_would_notice_a_real_value():
    # A shape check that never fires is indistinguishable from no check. This proves each
    # pattern fires on the thing it is written for.
    samples = ["/Users/isaac/projects/x", "~/secrets", "C:/keys",
               "ghp_" + "a" * 30, "1718881034", "11111111-2222-3333-4444-555555555555"]
    for pattern, _ in LEAK_SHAPES:
        assert any(re.search(pattern, sample) for sample in samples), pattern


def test_agents_names_the_manifest_as_the_contract_rather_than_restating_it():
    result = compiled()
    agents = result["files"]["AGENTS.md"]

    assert MANIFEST_PATH in agents
    assert "contract change" in agents.lower()


def test_readme_and_agents_both_list_the_verification_commands():
    result = compiled()

    for path in ["README.md", "AGENTS.md"]:
        assert "npm test" in result["files"][path]


def test_the_file_set_is_stable_across_two_compilations():
    first, second = compiled(stack="node", ci_values=CI_VALUES), compiled(stack="node", ci_values=CI_VALUES)

    assert first["files"] == second["files"]


def test_materialize_refuses_a_stack_it_has_no_template_for():
    manifest = compiled()["projectManifest"]

    with pytest.raises(ValueError, match="no reviewed template"):
        materialize(manifest, seed="x", stack="cobol", ci_values=CI_VALUES)
