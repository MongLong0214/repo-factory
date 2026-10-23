"""Resident ids are ACP keys; repository names and remotes retain their case."""
import base64
import copy
import importlib
import json
import os
import re
import subprocess

import pytest

from test_genesis_bundle import ACP, COMMANDS, REQUEST, ROOT, acp_check, compile_bundle, require_acp
from canonical import digest
from plan import compile_plan


def request_for(name="FreshReadme", project_id=None):
    request = copy.deepcopy(REQUEST)
    request["repositories"][0]["name"] = name
    request["remoteOwner"] = "example"
    if project_id is not None:
        request["projectId"] = project_id
    return request


@pytest.mark.parametrize("explicit,expected", [(None, "freshreadme"), ("freshreadme-2", "freshreadme-2")])
def test_capitalized_repository_compiles_through_real_acp(explicit, expected, tmp_path):
    request = request_for(project_id=explicit)
    plan = compile_bundle(request)
    manifest = plan["projectManifest"]
    assert manifest["projectId"] == expected
    assert manifest["repositories"][0]["remote"] == "github:example/FreshReadme"
    assert plan["repositoryIntent"] == ["github:example/FreshReadme"]
    assert all(op["resourceIdentity"].split("#")[0] == "github:example/FreshReadme"
               for op in plan["githubOperations"])
    checked = acp_check(plan)
    assert checked["normalization"]["allowed"] is True
    assert checked["submission"]["allowed"] is True
    assert checked["outputs"]["allowed"] is True
    assert checked["producerSchemaAllowed"] is True
    assert checked["preflight"]["allowed"] is True
    assert checked["manifestDigest"] == plan["projectManifestDigest"]
    assert checked["genesisDigest"] == plan["genesisDigest"]
    files = {f["path"]: base64.b64decode(f["contentBase64"]).decode() for f in plan["genesis"]["files"]}
    assert json.loads(files[".agent-control-plane/project.json"]) == manifest
    assert files["README.md"].startswith("# FreshReadme\n")
    assert files["AGENTS.md"].startswith("# FreshReadme —")
    # Package identity also comes from the repository, never the replaceable ACP key.
    assert json.loads(files["package.json"])["name"] == "FreshReadme"
    assert files[".github/workflows/project-ci.yml"] == compile_plan(
        request_for("freshreadme"), COMMANDS, operation_id="11111111-2222-3333-4444-555555555555")["files"][".github/workflows/project-ci.yml"]
    assert request["seed"] not in plan["genesis"]["issues"][0]["body"]
    assert request["seed"] in plan["genesis"]["productIntent"]["summary"]
    handoff = checked["outputs"]["value"]["handoff"]
    assert handoff["repositoryFacts"][0]["identity"] == "github:example/FreshReadme"
    decisions = [json.loads(row) for row in handoff["recentDecisions"]]
    assert decisions[0]["productIntent"] == plan["genesis"]["productIntent"]
    assert decisions[0]["genesis"]["issues"] == plan["genesis"]["issues"]
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    done = subprocess.run(["npm", "test"], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert request["repositories"][0]["name"] == "FreshReadme"


@pytest.mark.parametrize("name,expected", [
    ("demo", "demo"), ("FreshReadme", "freshreadme"),
    ("._-Fresh.ReadMe-_.", "fresh.readme"), ("A" * 100, "a" * 81),
])
def test_name_derivation_is_deterministic(name, expected):
    request = request_for(name)
    first = compile_plan(request, COMMANDS, operation_id="11111111-2222-3333-4444-555555555555")
    second = compile_plan(request, COMMANDS, operation_id="11111111-2222-3333-4444-555555555555")
    assert first == second
    assert first["projectManifest"]["projectId"] == expected
    assert first["planCore"]["repositories"][0]["identity"] == f"github:example/{name}"


@pytest.mark.parametrize("bad", ["FreshReadme", "bad/id", "-leading", "a" * 82, "", "id\n", None, 42,
                                  " freshreadme-2 ", ".freshreadme-2", "fresh readme-2"])
def test_invalid_explicit_id_is_refused_without_echoing_it(bad):
    request = request_for()
    request["projectId"] = bad
    with pytest.raises(ValueError) as caught:
        compile_bundle(request)
    message = str(caught.value)
    assert "PROJECT_ID_INVALID: manifest.projectId" in message
    if isinstance(bad, str) and bad:
        assert bad not in message


def test_empty_derived_id_refuses_instead_of_using_seed_or_owner():
    with pytest.raises(ValueError, match=r"PROJECT_ID_INVALID: manifest.projectId.*repositories\[0\].name"):
        compile_bundle(request_for("._-"))
    assert compile_bundle(request_for("._-", "explicit-id"))["projectManifest"]["projectId"] == "explicit-id"


def test_schema_pattern_equals_the_pinned_acp_resident_rule():
    require_acp()
    lock = json.loads((ROOT / "governance/acp-contract.lock.json").read_text())
    assert subprocess.check_output(["git", "-C", str(ACP.parent), "rev-parse", "HEAD"], text=True).strip() == lock["commit"]
    source = (ACP / "bootstrap/genesis-bundle.ts").read_text()
    pattern = re.search(r"/([^/\n]+)/\.test\(manifest\.projectId\)", source).group(1)
    schema = json.loads((ROOT / "schemas/bootstrap-request.schema.json").read_text())
    assert schema["properties"]["projectId"]["pattern"] == pattern


def test_go_module_and_document_titles_use_repository_name():
    request = request_for(project_id="freshreadme-2")
    request["bootstrapProfile"] = "GUARDED"
    request["repositories"][0]["stack"] = "go"
    compiled = compile_plan(request, [{"id": "test", "argv": ["go", "test", "./..."]}], operation_id="11111111-2222-3333-4444-555555555555")
    files = compiled["files"]
    assert files["go.mod"].startswith("module github.com/example/FreshReadme\n")
    for path in ("README.md", "AGENTS.md", "docs/PRD.md", "docs/adr/ADR-0001-architecture.md",
                 "docs/ACCEPTANCE.md", "docs/ROLLBACK.md"):
        assert "FreshReadme" in files[path].splitlines()[0]


@pytest.mark.parametrize("field,expected", [("projectId", "manifest.projectId"),
                                            ("verificationCommands", "manifest.verificationCommands")])
def test_real_bridge_reports_only_safe_fixed_field_categories(field, expected):
    plan = compile_bundle()
    payload = {"projectManifest": plan["projectManifest"], "genesis": plan["genesis"]}
    marker = "RejectedPrivateValue"
    payload["projectManifest"][field] = marker
    done = subprocess.run([str(ACP.parent / "node_modules/.bin/tsx"), str(ROOT / "scripts/acp_bridge.mts")],
                          input=json.dumps(payload), text=True, capture_output=True,
                          env={**os.environ, "ACP_SRC": str(ACP)}, timeout=60)
    assert done.returncode == 1
    assert json.loads(done.stdout)["reason"] == expected
    assert marker not in done.stdout + done.stderr
    with pytest.raises(ValueError) as caught:
        importlib.import_module("bootstrap").acp_normalize(payload, ACP)
    assert expected in str(caught.value)
    assert marker not in str(caught.value)


def test_bridge_output_is_not_trusted_as_an_error_message(monkeypatch):
    module = importlib.import_module("bootstrap")
    marker = "password=synthetic-secret-123456789"
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 1, json.dumps({"reason": marker, "error": marker}), marker))
    with pytest.raises(ValueError) as caught:
        module.acp_normalize({}, ACP)
    assert "ACP_SCHEMA" in str(caught.value)
    assert marker not in str(caught.value)


@pytest.mark.parametrize("project_id", ["0fresh.readme_2-", "a" * 81])
def test_valid_explicit_id_is_preserved_verbatim_through_real_acp(project_id):
    plan = compile_bundle(request_for(project_id=project_id))
    assert plan["projectManifest"]["projectId"] == project_id
    checked = acp_check(plan)
    assert checked["manifest"]["projectId"] == project_id
    assert checked["normalization"]["allowed"] is True
    assert checked["submission"]["allowed"] is True


def test_invalid_explicit_id_error_does_not_claim_name_derivation():
    # JSON Schema's `$` can match before a final newline; RF's full match must refuse it.
    with pytest.raises(ValueError) as caught:
        compile_bundle(request_for(project_id="freshreadme-2\n"))
    message = str(caught.value)
    assert "PROJECT_ID_INVALID: manifest.projectId" in message
    assert "repositories[0].name" not in message
    assert "freshreadme-2" not in message


@pytest.mark.parametrize("explicit", [None, "freshreadme"])
def test_valid_id_has_no_hidden_suffix_across_bootstrap_runs(explicit):
    module = importlib.import_module("bootstrap")
    for run_id, operation_id in [
        ("run-first", "11111111-2222-3333-4444-555555555555"),
        ("run-second", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ]:
        request = request_for(project_id=explicit)
        request["runId"] = run_id
        # Schema compilation has no registry state. Even an id such as `freshreadme`,
        # which may already be occupied, must reach ACP unchanged for its registry to decide.
        plan = module.compile_bootstrap_plan(request, COMMANDS, operation_id=operation_id, acp_src=ACP)
        assert plan["projectManifest"]["projectId"] == "freshreadme"
        checked = acp_check(plan)
        assert checked["manifest"]["projectId"] == "freshreadme"
        assert checked["normalization"]["allowed"] is True
        assert checked["submission"]["allowed"] is True


def test_actual_freshreadme_target_compiles_through_real_pinned_acp():
    request = copy.deepcopy(REQUEST)
    request["runId"] = "run_284fdfe39d4a405b9b763b10"
    request["repositories"][0]["name"] = "FreshReadme"
    request["projectId"] = "freshreadme-2"
    plan = compile_bundle(request)
    assert plan["requestDigest"] == digest(request, volatile="strip")
    assert plan["projectManifest"]["projectId"] == "freshreadme-2"
    assert plan["projectManifest"]["repositories"][0]["remote"] == "github:MongLong0214/FreshReadme"
    checked = acp_check(plan)
    assert checked["manifest"] == plan["projectManifest"]
    assert checked["manifestDigest"] == plan["projectManifestDigest"]
    assert checked["genesisDigest"] == plan["genesisDigest"]
    assert checked["normalization"]["allowed"] is True
    assert checked["submission"]["allowed"] is True
    assert checked["outputs"]["allowed"] is True
    assert checked["producerSchemaAllowed"] is True
    assert checked["preflight"]["allowed"] is True
