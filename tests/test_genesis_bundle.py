"""Exercise the RF -> ACP boundary without any remote writes."""
import base64
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ACP = Path(os.environ.get("ACP_SRC", Path.home() / "projects/agent-control-plane/src"))
REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-genesis",
    "seed": "An animation editor inspired by https://evergif.com/ — preserve the owner's idea.",
    "bootstrapProfile": "SIMPLE", "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "animation-editor", "stack": "node"}],
    "visibility": "public", "origin": {"channel": "cli"},
}
COMMANDS = [{"id": "test", "argv": ["npm", "test"]}]


def compile_bundle(request=None, commands=None):
    return importlib.import_module("bootstrap").compile_bootstrap_plan(
        copy.deepcopy(request or REQUEST), copy.deepcopy(COMMANDS if commands is None else commands),
        operation_id="11111111-2222-3333-4444-555555555555", acp_src=ACP)


def acp_check(plan):
    done = subprocess.run(
        [str(ACP.parent / "node_modules/.bin/tsx"), str(ROOT / "tests/fixtures/acp-genesis-check.mts")],
        input=json.dumps(plan), text=True, capture_output=True,
        env={**os.environ, "ACP_SRC": str(ACP)}, timeout=60, check=True)
    return json.loads(done.stdout)


def test_bundle_is_inline_and_byte_digests_match_acp_normalization():
    plan = compile_bundle()
    bundle = plan["genesis"]
    assert set(bundle) == {"schema", "commitMessage", "files", "branches", "labels", "milestones", "issues", "productIntent"}
    assert bundle["schema"] == "acp.genesis-bundle.v1"
    assert bundle["branches"] == {"main": "main", "integration": "dev", "genesis": "genesis"}
    files = {f["path"]: base64.b64decode(f["contentBase64"], validate=True) for f in bundle["files"]}
    for entry in bundle["files"]:
        assert entry["sha256"] == "sha256:" + hashlib.sha256(files[entry["path"]]).hexdigest()
        assert entry["mode"] in ("100644", "100755")
    manifest = plan["projectManifest"]
    assert json.loads(files[".agent-control-plane/project.json"]) == manifest
    workflow = manifest["ciWorkflows"][0]
    assert workflow["approvedDigest"] == "sha256:" + hashlib.sha256(files[workflow["path"]]).hexdigest()
    assert workflow["unapprovedFirstActivation"] is False
    profile = manifest["branchProfile"]
    assert profile["longLived"] == ["main", "dev"]
    assert (profile["defaultBranch"], profile["mergeStrategy"], profile["releaseTagPolicy"]) == ("dev", "merge_commit", "semver")
    assert manifest["commitlore"] == {"mode": "preferred"}
    checked = acp_check(plan)
    assert checked["manifest"] == manifest
    assert checked["manifestDigest"] == plan["projectManifestDigest"]
    assert checked["genesisDigest"] == plan["genesisDigest"]
    assert checked["operationsAllowed"] is True, checked
    assert not any("autopilot" in p or "merge-broker" in p or "governance" in p for p in files)


def test_produced_plan_passes_acp_plan_submission_validator():
    checked = acp_check(compile_bundle())
    assert checked["submission"]["allowed"] is True, checked
    assert checked["submission"]["value"]["genesis"] == compile_bundle()["genesis"]


def test_generated_verification_really_runs(tmp_path):
    plan = compile_bundle()
    for file in plan["genesis"]["files"]:
        path = tmp_path / file["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(file["contentBase64"]))
    command = plan["projectManifest"]["verificationCommands"][0]
    done = subprocess.run(command["argv"], cwd=tmp_path / command["cwd"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "greet names its argument" in done.stdout


def test_product_reference_and_owner_instructions_survive_the_plan_and_issue():
    request = copy.deepcopy(REQUEST)
    request["productIntent"] = {"summary": request["seed"], "references": ["https://evergif.com/"],
                                "ownerInstructions": ["Keep animation editing as the product."]}
    plan = compile_bundle(request)
    assert plan["genesis"]["productIntent"] == request["productIntent"]
    assert request["seed"] in plan["summary"]
    assert "https://evergif.com/" in plan["genesis"]["issues"][0]["body"]
    assert plan["genesis"]["issues"][0]["milestoneKey"] == plan["genesis"]["milestones"][0]["key"]


def test_a_shortened_product_summary_cannot_erase_the_original_seed_or_reference():
    request = copy.deepcopy(REQUEST)
    request["productIntent"] = {"summary": "Animation tool", "references": [], "ownerInstructions": []}
    intent = compile_bundle(request)["genesis"]["productIntent"]
    assert REQUEST["seed"] in intent["summary"]
    assert "https://evergif.com/" in intent["references"]


@pytest.mark.parametrize("field", ["seed", "issue", "milestone"])
def test_credentials_are_refused_before_materialization(field, monkeypatch):
    module = importlib.import_module("bootstrap")
    request = copy.deepcopy(REQUEST)
    marker = "password=synthetic-secret-123456789"
    if field == "seed":
        request["seed"] = marker
    else:
        request["genesis"] = {"issues" if field == "issue" else "milestones": [
            {"key": "first", "title": "first", "body" if field == "issue" else "description": marker,
             **({"labels": [], "milestoneKey": None} if field == "issue" else {})}]}
    def must_not_materialize(*args, **kwargs):
        pytest.fail("secret-bearing input reached materialization")
    monkeypatch.setattr(module, "compile_plan", must_not_materialize)
    with pytest.raises(ValueError, match="SECRET_SCREEN") as error:
        compile_bundle(request)
    assert marker not in str(error.value)


@pytest.mark.parametrize("commands", [[], [{"id": "test", "argv": ["python", "-m", "pytest"]}],
                                     [{"id": "test", "argv": ["npm", "run", "missing"]}]])
def test_unsupported_verification_is_refused_before_any_repository_creation(commands):
    with pytest.raises(ValueError, match="VERIFICATION"):
        compile_bundle(commands=commands)


def test_private_requests_are_refused_explicitly():
    request = copy.deepcopy(REQUEST)
    request["visibility"] = "private"
    with pytest.raises(ValueError, match="public repositories"):
        compile_bundle(request)


def test_seed_secret_screen_also_protects_the_legacy_materializer():
    from materialize import materialize
    with pytest.raises(ValueError, match="SECRET_SCREEN"):
        materialize({"projectId": "demo", "verificationCommands": [], "commitlore": {"mode": "preferred"}},
                    seed="password=synthetic-secret-123456789", remote_owner="owner")


def test_staged_skill_compiles_the_same_plan_with_all_dependencies(tmp_path):
    from stage_skill import stage_skill
    staged = stage_skill()
    assert (staged / "SKILL.md").read_bytes() == (ROOT / "SKILL.md").read_bytes()
    assert not (staged / "templates/kit").exists()
    assert not (staged / "scripts/authorize.py").exists()
    assert not (staged / "scripts/publish.py").exists()
    request, verification = tmp_path / "request.json", tmp_path / "verification.json"
    request.write_text(json.dumps(REQUEST))
    verification.write_text(json.dumps(COMMANDS))
    done = subprocess.run([sys.executable, str(staged / "scripts/bootstrap.py"), "--request", str(request),
                           "--verification", str(verification), "--operation-id", "11111111-2222-3333-4444-555555555555",
                           "--acp-src", str(ACP)], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == compile_bundle()
    source = json.loads((staged / "SOURCE.json").read_text())
    for relative, expected in source["files"].items():
        assert expected == "sha256:" + hashlib.sha256((staged / relative).read_bytes()).hexdigest()


@pytest.mark.parametrize("ci_values", [{"TEST_CMD": "true"}, {"INSTALL_CMD": "npm ci"}, {"BUILD_CMD": "true"}])
def test_ci_cannot_claim_verification_without_running_seeded_commands(ci_values):
    with pytest.raises(ValueError, match="VERIFICATION"):
        importlib.import_module("bootstrap").compile_bootstrap_plan(
            REQUEST, COMMANDS, operation_id="11111111-2222-3333-4444-555555555555", acp_src=ACP, ci_values=ci_values)


@pytest.mark.parametrize("backlog", [
    {"labels": [{"name": "same", "color": "aabbcc", "description": "d"}] * 2},
    {"labels": []}, {"milestones": []},
])
def test_invalid_backlog_links_are_refused(backlog):
    request = copy.deepcopy(REQUEST)
    request["genesis"] = backlog
    with pytest.raises(ValueError, match="GENESIS_BACKLOG"):
        compile_bundle(request)


# PR #58 review r1 (RF58-R1-01): Buzz/Nostr private keys must never reach public genesis content.
SYNTHETIC_NSEC = "nsec1" + "qpzry9x8gf2tvdw0s3jn54khce6mua7l" * 2


@pytest.mark.parametrize("field", ["seed", "issue", "milestone", "owner_instruction"])
def test_buzz_private_keys_are_refused_everywhere_genesis_text_goes(field):
    request = copy.deepcopy(REQUEST)
    marker = f"key {SYNTHETIC_NSEC[:63]} end"
    if field == "seed":
        request["seed"] = "An editor. " + marker
    elif field == "owner_instruction":
        request["productIntent"] = {"summary": "s", "references": [], "ownerInstructions": [marker]}
    else:
        request["genesis"] = {
            "labels": [{"name": "enhancement", "color": "a2eeef", "description": "d"}],
            "milestones": [{"key": "m", "title": "m", "description": marker if field == "milestone" else "d"}],
            "issues": [{"key": "i", "title": "i", "body": marker if field == "issue" else "b",
                        "labels": ["enhancement"], "milestoneKey": "m"}]}
    with pytest.raises(ValueError, match="SECRET_SCREEN") as error:
        compile_bundle(request)
    assert SYNTHETIC_NSEC[:20] not in str(error.value)


# RF58-R1-02: explicitly empty required collections must not erase the developable backlog.
@pytest.mark.parametrize("backlog", [
    {"labels": [], "milestones": [], "issues": []}, {"issues": []},
    {"labels": [{"name": "x", "color": "aabbcc", "description": "d"}], "milestones": [], "issues": [
        {"key": "i", "title": "i", "body": "b", "labels": ["x"], "milestoneKey": None}]},
    {"labels": [], "milestones": [{"key": "m", "title": "m", "description": "d"}], "issues": [
        {"key": "i", "title": "i", "body": "b", "labels": [], "milestoneKey": "m"}]},
])
def test_empty_required_backlog_collections_are_refused(backlog):
    request = copy.deepcopy(REQUEST)
    request["genesis"] = backlog
    with pytest.raises(ValueError, match="GENESIS_BACKLOG"):
        compile_bundle(request)


def test_genesis_goes_through_a_pr_and_never_pushes_the_default_branch():
    """Owner 2026-10-22: "초기 파일도 PR로"; the standing no-default-branch-push rule stands."""
    plan = compile_bundle()
    ops = plan["githubOperations"]
    ids = [op["operationId"] for op in ops]
    assert ops[0]["desiredState"] == {"visibility": "public", "autoInit": True}
    assert not [op for op in ops if op["resourceType"] == "branch" and op["resourceIdentity"].endswith("#main")]
    assert ids.index("protect-main") < ids.index("push-genesis-branch") < ids.index("create-dev")
    assert ids.index("create-dev") < ids.index("protect-dev") < ids.index("set-default-branch")
    assert plan["genesis"]["branches"] == {"main": "main", "integration": "dev", "genesis": "genesis"}
