"""Public scaffold claims and the exact required check must match what genesis can verify."""
import base64
import copy
import json
import os
import re
import subprocess

import pytest

from test_genesis_bundle import REQUEST, compile_bundle


@pytest.fixture(scope="module")
def plan():
    request = copy.deepcopy(REQUEST)
    request["bootstrapProfile"] = "STANDARD"
    request["productIntent"] = {
        "summary": "Check repository READMEs and propose corrections.",
        "references": [],
        "ownerInstructions": ["Public repository only.", "Name: ReadmeTool.",
                              "Visibility: public.", "Repository name: ReadmeTool.",
                              "The repository must be public.", "Create a public repository named ReadmeTool."],
        "publicRequirements": ["Check private repositories as well as public ones."],
    }
    return compile_bundle(request)


def files(plan):
    return {entry["path"]: base64.b64decode(entry["contentBase64"]).decode()
            for entry in plan["genesis"]["files"]}


def jobs(workflow):
    return dict(re.findall(r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)",
                           workflow.split("\njobs:\n", 1)[1], re.MULTILINE | re.DOTALL))


def required_gate(plan):
    job_blocks = jobs(files(plan)[".github/workflows/project-ci.yml"])
    gates = [block for block in job_blocks.values() if re.search(r"^    name: project-ci$", block, re.MULTILINE)]
    assert len(gates) == 1
    return job_blocks, gates[0]


def test_only_the_non_matrix_gate_carries_the_required_check(plan):
    job_blocks, gate = required_gate(plan)
    assert "matrix:" not in gate and "strategy:" not in gate
    matrix_ids = [key for key, block in job_blocks.items() if "matrix:" in block]
    assert len(matrix_ids) == 1
    matrix = matrix_ids[0]
    assert f"needs: [{matrix}]" in gate
    assert "if: always()" in gate
    assert f"${{{{ needs.{matrix}.result }}}}" in gate
    assert 'runtime: ["20", "22"]' in job_blocks[matrix]
    assert "name: test (node ${{ matrix.runtime }})" in job_blocks[matrix]
    assert "continue-on-error" not in files(plan)[".github/workflows/project-ci.yml"]
    assert "if:" not in job_blocks[matrix]
    for operation in plan["githubOperations"]:
        if operation["resourceType"] == "branch-protection":
            assert operation["desiredState"]["requiredStatusChecks"]["contexts"] == ["project-ci"]
    assert plan["projectManifest"]["ciWorkflows"][0]["checkName"] == "project-ci"


@pytest.mark.parametrize("result,passes", [("success", True), ("failure", False),
                                          ("skipped", False), ("cancelled", False)])
def test_gate_executes_and_accepts_only_the_full_matrix_success(plan, result, passes):
    _, gate = required_gate(plan)
    assert "if: always()" in gate
    assert "MATRIX_RESULT: ${{ needs.test.result }}" in gate
    command = re.search(r"^        run: (.+)$", gate, re.MULTILINE).group(1)
    done = subprocess.run(["bash", "-eo", "pipefail", "-c", command],
                          env={**os.environ, "MATRIX_RESULT": result}, capture_output=True)
    assert (done.returncode == 0) is passes


def test_dependency_free_genesis_runs_tests_and_syntax_without_install_or_lock(plan):
    emitted = files(plan)
    package = json.loads(emitted["package.json"])
    assert not package.get("dependencies") and not package.get("devDependencies")
    workflow = emitted[".github/workflows/project-ci.yml"]
    assert "npm install" not in workflow
    assert "run: npm test" in workflow and "run: node --check index.js" in workflow
    assert "cache:" not in workflow and "package-lock.json" not in emitted
    assert "latest is what a contributor will actually have installed" not in workflow
    assert "Node 20 and 22" in workflow


def test_readme_labels_target_features_and_excludes_creation_conditions(plan):
    readme = files(plan)["README.md"]
    assert "Status: scaffold — features below are planned" in readme
    assert "not implemented yet" in readme
    assert "## Target features" in readme
    assert "corresponding slice" in readme and "verified" in readme
    assert "Check repository READMEs and propose corrections." in readme
    assert "Check private repositories as well as public ones." in readme
    for condition in ("Public repository only.", "Name: ReadmeTool.", "Visibility: public.",
                      "Repository name: ReadmeTool.", "The repository must be public.",
                      "Create a public repository named ReadmeTool."):
        assert condition not in readme
        assert condition not in files(plan)["docs/PRD.md"]
    assert "bases and targets the manifest declares" not in readme


def test_prd_separates_scaffold_verification_from_product_acceptance(plan):
    prd = files(plan)["docs/PRD.md"]
    assert "Bootstrap scaffold" in prd and "Product completion" in prd
    assert "only verifies the scaffold" in prd
    assert "does not verify the planned product features" in prd
    assert "npm test" in prd
    assert "issue bodies" in prd and "milestone descriptions" in prd
    assert "docs/ACCEPTANCE.md" not in prd


def test_agents_describes_conditional_setup_and_legitimate_contract_changes(plan):
    agents = files(plan)["AGENTS.md"]
    assert agents.splitlines()[2] == "Project-local development and verification rules."
    assert "CommitLore is `preferred`" in agents
    assert "When initialization is needed" in agents and "After cloning, run" not in agents
    assert "commitlore init --mcp-scope none" in agents and "commitlore doctor" in agents
    assert "existing approved contract" in agents and "separate approval" in agents
    assert "next run" in agents
    assert "Do not weaken" in agents
    assert "base the manifest declares for that branch pattern" not in agents


def test_public_text_contains_no_internal_runtime_names_or_operations(plan):
    public = list(files(plan).values()) + [json.dumps(plan["genesis"][key])
                                          for key in ("commitMessage", "issues", "milestones")]
    for text in public:
        assert not re.search(r"\b(?:ACP|Hermes|Claude Code)\b|Session identity|provider routing|review assignment", text)


def test_explicit_public_backlog_cannot_reintroduce_internal_runtime_names():
    request = copy.deepcopy(REQUEST)
    request["genesis"] = {"commitMessage": "Run through ACP for the current session"}
    with pytest.raises(ValueError, match="PUBLIC_TEXT"):
        compile_bundle(request)
