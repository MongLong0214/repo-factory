"""A profile's `required` list only means something if something checks it (PRD §6)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from materialize import (  # noqa: E402
    ACCEPTANCE_PATH, ADR_PATH, ARTIFACT_EVIDENCE, CI_PATH, ROLLBACK_PATH, SKELETONS, SPEC_PATH,
    artifact_coverage,
)
from plan import compile_plan, load_profile  # noqa: E402

VER = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
        "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True}]
SECURITY = {"id": "security", "argv": ["npm", "audit", "--audit-level=high"], "repositoryRole": "primary",
            "cwd": ".", "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "allowlist", "required": True}
CI = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
      "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}


def compiled(profile: str, commands=None, **kwargs):
    request = {"schema": "repo-factory.bootstrap-request.v1", "runId": "r", "seed": "a demo project",
               "bootstrapProfile": profile, "priority": "NORMAL",
               "repositories": [{"role": "primary", "name": "demo"}],
               "visibility": "private", "origin": {"channel": "cli"}}
    return compile_plan(request, commands or VER, stack="node", ci_values=CI,
                        operation_id="11111111-2222-3333-4444-555555555555", **kwargs)


def test_every_artifact_any_profile_requires_has_a_stated_evidence():
    # An artifact with no entry is uncheckable, so it would sit in `required` forever while
    # nothing looked for it — the state this whole file exists to end.
    for profile in ["simple", "standard", "guarded"]:
        data = load_profile(profile)
        for artifact in data["required"] + data["optional"]:
            if artifact in ARTIFACT_EVIDENCE:
                continue
            pytest.fail(f"{profile}: {artifact} has no stated evidence in ARTIFACT_EVIDENCE")


def test_simple_and_standard_are_fully_covered():
    for profile in ["SIMPLE", "STANDARD"]:
        assert compiled(profile)["unresolvedGaps"] == [], profile


def test_guarded_says_so_when_no_security_command_was_supplied():
    # The profile requires one; the caller supplies the commands. Producing a file named
    # "security" would satisfy the list while verifying nothing.
    gaps = compiled("GUARDED")["unresolvedGaps"]

    assert any("security-command" in gap for gap in gaps)


def test_guarded_is_covered_once_the_security_command_exists():
    assert compiled("GUARDED", commands=VER + [SECURITY])["unresolvedGaps"] == []


def test_a_profile_only_gets_the_documents_it_asks_for():
    # §6.1 — producing a document to satisfy a count is a defect, so SIMPLE has no ADR.
    simple, guarded = compiled("SIMPLE")["files"], compiled("GUARDED", commands=VER + [SECURITY])["files"]

    for path in [SPEC_PATH, ADR_PATH, ACCEPTANCE_PATH, ROLLBACK_PATH]:
        assert path not in simple, f"SIMPLE should not carry {path}"
        assert path in guarded, f"GUARDED should carry {path}"
    assert SPEC_PATH in compiled("STANDARD")["files"]
    assert ADR_PATH not in compiled("STANDARD")["files"]


def test_a_missing_file_is_reported_rather_than_assumed():
    manifest = compiled("STANDARD")["projectManifest"]
    _, missing = artifact_coverage(["readme", "agents"], {"README.md": "x"}, manifest)

    assert missing == ["agents"]


def test_coverage_reads_the_manifest_for_contracts_that_are_not_files():
    # `verification-commands` and `branch-contract` live inside the manifest. Looking for a
    # file named after them would make them permanently missing.
    manifest = compiled("SIMPLE")["projectManifest"]
    satisfied, missing = artifact_coverage(["verification-commands", "branch-contract"], {}, manifest)

    assert missing == [] and len(satisfied) == 2


def test_the_handoff_document_is_not_looked_for_in_the_repository():
    # `repo-factory-result` is produced at handoff. Treating it as a repository file would
    # leave every profile permanently short by one.
    _, missing = artifact_coverage(["repo-factory-result"], {}, {})

    assert missing == []


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_every_default_stack_has_a_runnable_skeleton(stack: str):
    # A workflow with nothing to run is a red first CI that blames the project for the
    # factory having built half a repository.
    assert stack in SKELETONS
    files = SKELETONS[stack]("demo-project")
    assert files, stack
    # Rust puts unit tests inside the module under `#[cfg(test)]`, so a path check would call a
    # conventional Rust crate untested. What matters is that something asserts.
    assert any("assert" in body or "test" in path.lower() for path, body in files.items()), \
        f"{stack} skeleton has nothing that tests"


@pytest.mark.parametrize("stack", ["python", "go", "rust"])
def test_a_stack_with_a_skeleton_reports_no_skeleton_gap(stack: str):
    request = {"schema": "repo-factory.bootstrap-request.v1", "runId": "r", "seed": "demo",
               "bootstrapProfile": "SIMPLE", "priority": "NORMAL",
               "repositories": [{"role": "primary", "name": "demo"}],
               "visibility": "private", "origin": {"channel": "cli"}}
    values = {"RUNTIME_LOWER": "1", "RUNTIME_LATEST": "2", "INSTALL_CMD": "true",
              "TEST_CMD": "true", "BUILD_CMD": "true"}
    result = compile_plan(copy.deepcopy(request), VER, stack=stack, ci_values=values,
                         operation_id="11111111-2222-3333-4444-555555555555")

    assert not any("skeleton" in gap for gap in result["unresolvedGaps"])
