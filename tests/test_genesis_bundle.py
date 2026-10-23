"""Exercise the RF -> ACP boundary without any remote writes."""
import base64
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
ACP = Path(os.environ["ACP_SRC"]) if os.environ.get("ACP_SRC") else None
REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-genesis",
    "seed": "An animation editor inspired by https://evergif.com/ — preserve the owner's idea.",
    "bootstrapProfile": "SIMPLE", "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "animation-editor", "stack": "node"}],
    "visibility": "public", "origin": {"channel": "cli"},
}
COMMANDS = [{"id": "test", "argv": ["npm", "test"]}]


def compile_bundle(request=None, commands=None):
    require_acp()
    return importlib.import_module("bootstrap").compile_bootstrap_plan(
        copy.deepcopy(request or REQUEST), copy.deepcopy(COMMANDS if commands is None else commands),
        operation_id="11111111-2222-3333-4444-555555555555", acp_src=ACP)


def acp_check(plan):
    require_acp()
    done = subprocess.run(
        [str(ACP.parent / "node_modules/.bin/tsx"), str(ROOT / "tests/fixtures/acp-genesis-check.mts")],
        input=json.dumps(plan), text=True, capture_output=True,
        env={**os.environ, "ACP_SRC": str(ACP)}, timeout=60, check=True)
    return json.loads(done.stdout)


def require_acp():
    if ACP is None:
        pytest.fail("ACP_SRC is required for genesis contract tests; set it to the pinned ACP src directory")
    if not (ACP / "bootstrap/genesis-bundle.ts").is_file() or not (ACP.parent / "node_modules/.bin/tsx").is_file():
        pytest.fail("ACP_SRC must contain the pinned genesis-bundle.ts and installed tsx")


def public_texts(plan):
    bundle = plan["genesis"]
    return {
        **{entry["path"]: base64.b64decode(entry["contentBase64"]).decode("utf-8")
           for entry in bundle["files"]},
        **{key: json.dumps(bundle[key], ensure_ascii=False)
           for key in ("commitMessage", "labels", "issues", "milestones")},
    }


@pytest.mark.parametrize("profile", ["SIMPLE", "STANDARD", "GUARDED"])
def test_emitted_text_references_only_files_present_in_the_bundle(profile):
    request = {**copy.deepcopy(REQUEST), "bootstrapProfile": profile}
    plan = compile_bundle(request)
    files = {entry["path"] for entry in plan["genesis"]["files"]}
    # Include prose, manifest/CI paths, command arguments and source imports. Branch
    # globs and remote action identifiers are not repository file references.
    path_pattern = r"(?<![\w./:-])(?:\.{1,2}/)?(?:\.?[\w-]+/)*[\w.-]+\.(?:md|json|ya?ml|[cm]?js|py|toml|go|rs)\b"
    for source, text in public_texts(plan).items():
        for match in re.finditer(path_pattern, text):
            target = match.group()
            if target.startswith(("./", "../")):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
            assert target in files, f"{source} references absent {target}"
    if profile == "STANDARD":
        prd = public_texts(plan)["docs/PRD.md"]
        assert "docs/ACCEPTANCE.md" not in prd
        assert "issue bodies" in prd and "milestone descriptions" in prd
        assert "npm test" in prd
    elif profile == "GUARDED":
        assert "`docs/ACCEPTANCE.md`" in public_texts(plan)["docs/PRD.md"]


@pytest.mark.parametrize("structured", [False, True])
def test_verbatim_conversation_is_private_but_reaches_the_resident_handoff(structured):
    request = copy.deepcopy(REQUEST)
    request["bootstrapProfile"] = "STANDARD"
    request["seed"] = "PRIVATE-CONVERSATION-눈사람-雪: 내 말 그대로. https://example.org/product"
    if structured:
        request["productIntent"] = {
            "summary": "A README verification tool.", "references": ["https://example.org/product"],
            "ownerInstructions": ["Check the whole README (owner 2026-10-10).",
                                  "Report user-visible mismatches without leaking the parent environment.",
                                  "Create through ACP and Repo Factory; no manual room, identity or CTO."]}
    original = copy.deepcopy(request)
    plan = compile_bundle(request)
    for location, text in public_texts(plan).items():
        assert request["seed"] not in text, location
        assert "PRIVATE-CONVERSATION" not in text and "눈사람" not in text, location
        assert "owner 2026-10-10" not in text and "no manual room" not in text, location
    if structured:
        for location in ("README.md", "docs/PRD.md", "issues", "milestones"):
            assert "A README verification tool." in public_texts(plan)[location]
            assert "Check the whole README" in public_texts(plan)[location]
            assert "user-visible mismatches" in public_texts(plan)[location]
    assert request == original
    assert request["seed"] in plan["summary"]
    assert request["seed"] in plan["genesis"]["productIntent"]["summary"]
    checked = acp_check(plan)
    handoff = checked["outputs"]["value"]["handoff"]
    decision = json.loads(handoff["recentDecisions"][0])
    assert request["seed"] in decision["productIntent"]["summary"]
    assert "https://example.org/product" in decision["productIntent"]["references"]
    if structured:
        assert decision["productIntent"]["ownerInstructions"] == request["productIntent"]["ownerInstructions"]


PRIVATE_MARKERS = [
    "/Users/private-owner/project", "/home/private-owner/project", "/tmp/private-runtime/project",
    "C:\\Users\\private-owner\\project", "~/private-project",
    "run_284fdfe39d4a405b9b763b10", "session-abcdef012345", "actor_id: private-owner",
    "11111111-2222-3333-4444-555555555555", "1718881034", "model: gpt-6",
    "runtime: private-provider", "environment: private-machine",
]


@pytest.mark.parametrize("marker", PRIVATE_MARKERS)
def test_private_values_in_seed_never_reach_public_text(marker):
    request = {**copy.deepcopy(REQUEST), "seed": "Owner conversation: " + marker}
    plan = compile_bundle(request)
    assert marker in plan["genesis"]["productIntent"]["summary"]
    assert all(marker not in text for text in public_texts(plan).values())


@pytest.mark.parametrize("surface", ["summary", "reference", "instruction", "commit", "issue", "milestone", "label"])
@pytest.mark.parametrize("marker", PRIVATE_MARKERS)
def test_explicit_public_text_refuses_private_values_without_echoing_them(surface, marker):
    request = copy.deepcopy(REQUEST)
    request["productIntent"] = {"summary": "A README tool.", "references": [], "ownerInstructions": []}
    if surface == "summary":
        request["productIntent"]["summary"] = marker
    elif surface == "reference":
        request["productIntent"]["references"] = [marker]
    elif surface == "instruction":
        request["productIntent"]["ownerInstructions"] = ["Keep the product simple. " + marker]
    else:
        request["genesis"] = {
            "commitMessage": marker if surface == "commit" else "chore: initialize",
            "labels": [{"name": "enhancement", "color": "a2eeef",
                        "description": marker if surface == "label" else "Product development"}],
            "milestones": [{"key": "m", "title": "First increment",
                            "description": marker if surface == "milestone" else "Implement the product."}],
            "issues": [{"key": "i", "title": "Implement the product", "labels": ["enhancement"],
                        "milestoneKey": "m", "body": marker if surface == "issue" else "Implement the product."}]}
    with pytest.raises(ValueError, match="PUBLIC_TEXT") as caught:
        compile_bundle(request)
    assert marker not in str(caught.value)


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
    assert checked["normalization"]["allowed"] is True, checked
    assert checked["normalization"]["genesis"] == bundle
    assert checked["manifest"] == manifest
    assert checked["manifestDigest"] == plan["projectManifestDigest"]
    assert checked["genesisDigest"] == plan["genesisDigest"]
    assert not any("autopilot" in p or "merge-broker" in p or "governance" in p for p in files)


def test_produced_plan_passes_acp_plan_submission_validator():
    plan = compile_bundle()
    checked = acp_check(plan)
    assert checked["submission"]["allowed"] is True, checked
    assert checked["submission"]["value"]["genesis"] == plan["genesis"]
    assert checked["submission"]["value"]["genesisDigest"] == plan["genesisDigest"]


# Green-only with RF 006e261 + Unit1: RF already emitted this PR sequence. These acceptance
# and operation-mutation checks cover ACP's contract; the old lock fails the separate pin check.
def test_six_operation_plan_reaches_acp_producer_preflight_through_submission():
    plan = compile_bundle()
    checked = acp_check(plan)
    assert plan["githubOperations"] == checked["publicOperations"]
    assert checked["outputs"]["allowed"] is True, checked
    assert checked["producerSchemaAllowed"] is True, checked
    assert checked["preflight"]["allowed"] is True, checked
    operations = checked["preflight"]["value"]["operations"]
    assert [op["resourceType"] for op in operations] == [
        "repository", "branch-protection", "branch",
        "genesis-bootstrap", "genesis-bootstrap", "genesis-bootstrap",
        "branch", "branch-protection", "setting",
        "genesis-resources", "genesis-resources", "genesis-resources"]
    assert [op["desiredState"]["action"] for op in operations[3:6]] == ["open", "verify", "merge"]
    assert operations[2]["resourceIdentity"].endswith("#genesis")
    assert operations[6]["resourceIdentity"].endswith("#dev")
    for kind, operation in zip(("labels", "milestones", "issues"), operations[9:]):
        assert operation["desiredState"][kind] == plan["genesis"][kind]
        assert operation["desiredState"][kind]


def test_acp_refuses_a_direct_push_to_main():
    plan = compile_bundle()
    plan["githubOperations"][2]["resourceIdentity"] = plan["repositoryIntent"][0] + "#main"
    checked = acp_check(plan)
    assert checked["submission"]["allowed"] is False, checked
    assert checked["submission"]["evidence"]["refusal"] == "GENESIS_INVALID"
    assert checked["preflight"]["allowed"] is False, checked


def test_acp_refuses_a_missing_genesis_pr_step():
    # Mutate ACP's expansion: the six public operations intentionally omit ACP-owned PR steps.
    submitted = acp_check(compile_bundle())["submission"]["value"]
    submitted["githubOperations"] = [op for op in submitted["githubOperations"]
                                      if op["operationId"] != "acp-genesis-merge"]
    checked = acp_check(submitted)
    assert checked["submission"]["allowed"] is False, checked
    assert checked["submission"]["evidence"]["refusal"] == "GENESIS_INVALID"
    assert checked["preflight"]["allowed"] is False, checked
    assert checked["preflight"]["evidence"]["refusal"] == "UNSUPPORTED_PLAN_SHAPE"


def test_acp_refuses_a_private_key_in_genesis_file_bytes():
    plan = compile_bundle()
    secret = b"-----BEGIN PRIVATE KEY-----\nsynthetic-contract-fixture\n-----END PRIVATE KEY-----\n"
    plan["genesis"]["files"].append({
        "path": "fixture.pem", "mode": "100644", "contentBase64": base64.b64encode(secret).decode("ascii"),
        "sha256": "sha256:" + hashlib.sha256(secret).hexdigest()})
    checked = acp_check(plan)
    assert checked["normalization"]["allowed"] is False, checked
    assert checked["submission"]["allowed"] is False, checked
    assert checked["submission"]["evidence"]["refusal"] == "GENESIS_INVALID"
    assert "BEGIN PRIVATE KEY" not in json.dumps(checked)
    # Exercise RF's production bridge too: schema-only parsing used to accept these bytes.
    with pytest.raises(ValueError, match="ACP_SCHEMA"):
        importlib.import_module("bootstrap").acp_normalize(
            {"projectManifest": plan["projectManifest"], "genesis": plan["genesis"]}, ACP)


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
    staged = stage_skill(tmp_path / "staged-skill")
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
