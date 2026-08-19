"""The pipeline the documentation describes has to be runnable as documented.

`apply` and `publish` were library-only for a while. Every test passed, because every test
called them as Python functions — the same reason `plan.py` could ship without a way to supply
CI values and nobody noticed. A published skill whose middle stages have no command line is a
document describing something other than the thing that ships.

Then the command line existed and its only real path was dead. Approval became a document the
approver makes, `apply_plan` grew a required `authorization` argument, and `apply.py` was never
taught to supply one — so every non-dry invocation refused with AUTHORIZATION_MISSING before it
read anything. The test here covered `--dry-run`, which returns before the approval gate is
reached. **A stage is covered at the depth the test enters it, not at the depth it is named.**
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
OPERATION_ID = "11111111-2222-3333-4444-555555555555"

STAGES = ["plan.py", "authorize.py", "apply.py", "publish.py", "result.py"]
REMOTE = "git@github.com:MongLong0214/demo.git"

REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-cli", "seed": "a demo project",
    "bootstrapProfile": "STANDARD", "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": "demo", "stack": "node"}],
    "visibility": "private", "remoteOwner": "MongLong0214", "origin": {"channel": "cli"},
}
VERIFICATION = [
    {"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
     "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True},
]
CI_VALUES = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
             "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}


def run(argv, **kwargs):
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, **kwargs)


@pytest.mark.parametrize("stage", STAGES)
def test_every_pipeline_stage_answers_on_the_command_line(stage):
    done = run([str(SCRIPTS / stage), "--help"])
    assert done.returncode == 0, f"{stage} --help exited {done.returncode}: {done.stderr[-400:]}"
    assert "usage:" in done.stdout


def test_the_pipeline_runs_end_to_end_through_its_command_line(tmp_path):
    """plan → apply → publish, each as a process, with the file set coming from the plan."""
    (tmp_path / "request.json").write_text(json.dumps(REQUEST), encoding="utf-8")
    (tmp_path / "verification.json").write_text(json.dumps(VERIFICATION), encoding="utf-8")
    (tmp_path / "ci.json").write_text(json.dumps(CI_VALUES), encoding="utf-8")

    compiled = run([str(SCRIPTS / "plan.py"),
                    "--request", str(tmp_path / "request.json"),
                    "--verification", str(tmp_path / "verification.json"),
                    "--ci-values", str(tmp_path / "ci.json"),
                    "--operation-id", OPERATION_ID])
    assert compiled.returncode == 0, compiled.stderr[-600:]
    document = json.loads(compiled.stdout)
    assert document["unresolvedGaps"] == [], document["unresolvedGaps"]
    (tmp_path / "compiled.json").write_text(compiled.stdout, encoding="utf-8")

    staged = run([str(SCRIPTS / "apply.py"), "--plan", str(tmp_path / "compiled.json"),
                  "--ledger", str(tmp_path / "receipts.json"), "--phase", "after-files", "--dry-run"])
    assert staged.returncode == 0, staged.stderr[-600:]
    would = json.loads(staged.stdout)["wouldApply"]
    assert [op["operationId"] for op in would] == ["set-default-branch:demo", "create-ruleset:demo"]
    # The dry run has to show the effect, not just the name — that is the thing under approval.
    by_id = {op["operationId"]: op for op in would}
    assert by_id["create-ruleset:demo"]["desiredState"]["enforcement"] == "active"
    assert by_id["set-default-branch:demo"]["desiredState"]["defaultBranch"] == "dev"

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    # The publisher refuses a remote it cannot bind to the planned repository, so the test does
    # not get an escape hatch in production code — it gives git a rewrite rule instead. The
    # command sees the approved GitHub URL and the bytes land in a local bare repository.
    rewritten = {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
        "GIT_CONFIG_VALUE_0": REMOTE,
    }
    published = run([str(SCRIPTS / "publish.py"), "--plan", str(tmp_path / "compiled.json"),
                     "--workdir", str(tmp_path / "work"), "--remote-url", REMOTE,
                     "--ledger", str(tmp_path / "receipts.json"),
                     "--author-name", "Repo Factory", "--author-email", "factory@example.invalid"],
                    env=rewritten)
    assert published.returncode == 0, published.stderr[-600:]
    heads = json.loads(published.stdout)
    assert heads["repositoryIdentity"] == "github:MongLong0214/demo"
    assert set(heads["remoteHeads"].values()) == {heads["head"]}

    landed = subprocess.run(["git", f"--git-dir={bare}", "ls-tree", "-r", "--name-only", "dev"],
                            capture_output=True, text=True, check=True)
    assert sorted(landed.stdout.split()) == sorted(document["files"]), (
        "the published tree is not the planned set"
    )

    # The genesis push leaves the receipt the later phase reads to know the workflow the ruleset
    # requires is actually in the repository. Without it that phase refuses rather than running
    # out of order, so a publish that records nothing strands the bootstrap.
    ledger = json.loads((tmp_path / "receipts.json").read_text(encoding="utf-8"))
    genesis = [row for row in ledger if row["resourceType"] == "genesis-commit"]
    assert [row["operationId"] for row in genesis] == ["publish:github:MongLong0214/demo"]
    assert genesis[0]["verified"] is True
    assert genesis[0]["head"] == heads["head"]


# A gh that only knows how to not-have a repository, create one, and then have it. The point is
# to reach `apply_plan` as a process — the approval gate refuses before any of this is called,
# which is exactly what nothing was measuring.
GH_STUB = """#!/bin/sh
STATE="$GH_STUB_STATE"
case "$1 $2" in
  "repo create")
    printf '%s\\n' "$3" >> "$STATE/created"; exit 0 ;;
esac
if [ "$1" = "api" ]; then
  path="$2"; [ "$path" = "--method" ] && exit 0
  repo=$(printf '%s' "$path" | sed -n 's#^repos/\\([^/]*/[^/]*\\)$#\\1#p')
  if [ -n "$repo" ] && grep -qx "$repo" "$STATE/created" 2>/dev/null; then
    printf '{"default_branch":"main","private":true,"node_id":"R_stub"}\\n'; exit 0
  fi
  echo "gh: Not Found (HTTP 404)" >&2; exit 1
fi
echo "gh stub: unhandled $*" >&2; exit 1
"""


def _compile(tmp_path, operation_id=OPERATION_ID, name="compiled.json"):
    (tmp_path / "request.json").write_text(json.dumps(REQUEST), encoding="utf-8")
    (tmp_path / "verification.json").write_text(json.dumps(VERIFICATION), encoding="utf-8")
    (tmp_path / "ci.json").write_text(json.dumps(CI_VALUES), encoding="utf-8")
    done = run([str(SCRIPTS / "plan.py"),
                "--request", str(tmp_path / "request.json"),
                "--verification", str(tmp_path / "verification.json"),
                "--ci-values", str(tmp_path / "ci.json"),
                "--operation-id", operation_id])
    assert done.returncode == 0, done.stderr[-600:]
    (tmp_path / name).write_text(done.stdout, encoding="utf-8")
    return tmp_path / name


def _gh_stub(tmp_path):
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    (tmp_path / "ghstate").mkdir(exist_ok=True)
    return stub, {**os.environ, "GH_STUB_STATE": str(tmp_path / "ghstate")}


def test_apply_writes_on_the_command_line_when_an_approval_receipt_is_supplied(tmp_path):
    """The documented invocation, run as a process, past the approval gate and into a write.

    Without this the whole non-dry command line could be dead and every test still passed:
    `--dry-run` returns before `apply_plan`, and every other caller was Python.
    """
    plan = _compile(tmp_path)
    stub, env = _gh_stub(tmp_path)

    issued = run([str(SCRIPTS / "authorize.py"), "--plan", str(plan),
                  "--authority", "HERMES", "--actor", "hermes:ceo"])
    assert issued.returncode == 0, issued.stderr[-600:]
    (tmp_path / "auth.json").write_text(issued.stdout, encoding="utf-8")

    applied = run([str(SCRIPTS / "apply.py"), "--plan", str(plan),
                   "--ledger", str(tmp_path / "receipts.json"), "--phase", "before-files",
                   "--gh", str(stub), "--authorization", str(tmp_path / "auth.json")], env=env)
    assert applied.returncode == 0, applied.stderr[-800:]
    outcome = json.loads(applied.stdout)
    assert outcome["completed"] is True
    assert [r["operationId"] for r in outcome["receipts"]] == ["create-repository:demo"]
    assert outcome["receipts"][0]["verified"] is True


def test_apply_refuses_on_the_command_line_when_nothing_approved_the_plan(tmp_path):
    plan = _compile(tmp_path)
    stub, env = _gh_stub(tmp_path)
    applied = run([str(SCRIPTS / "apply.py"), "--plan", str(plan),
                   "--ledger", str(tmp_path / "receipts.json"), "--phase", "before-files",
                   "--gh", str(stub)], env=env)
    assert applied.returncode == 1
    assert json.loads(applied.stderr)["error"] == "AUTHORIZATION_MISSING"
    assert not (tmp_path / "ghstate" / "created").exists(), "it refused after writing"


def test_apply_refuses_a_receipt_issued_over_a_different_plan(tmp_path):
    """Presence is not the check. The receipt names a digest, and that digest has to be this one."""
    mine = _compile(tmp_path, name="mine.json")
    other = _compile(tmp_path, operation_id="99999999-8888-7777-6666-555555555555",
                     name="other.json")
    stub, env = _gh_stub(tmp_path)

    issued = run([str(SCRIPTS / "authorize.py"), "--plan", str(other),
                  "--authority", "HERMES", "--actor", "hermes:ceo"])
    assert issued.returncode == 0, issued.stderr[-600:]
    (tmp_path / "other-auth.json").write_text(issued.stdout, encoding="utf-8")

    applied = run([str(SCRIPTS / "apply.py"), "--plan", str(mine),
                   "--ledger", str(tmp_path / "receipts.json"), "--phase", "before-files",
                   "--gh", str(stub), "--authorization", str(tmp_path / "other-auth.json")],
                  env=env)
    assert applied.returncode == 1
    assert json.loads(applied.stderr)["error"] == "AUTHORIZATION_MISSING"
    assert not (tmp_path / "ghstate" / "created").exists(), "it refused after writing"


def test_the_approval_receipt_binds_to_the_plan_it_was_issued_over(tmp_path):
    plan = _compile(tmp_path)
    issued = run([str(SCRIPTS / "authorize.py"), "--plan", str(plan),
                  "--authority", "OWNER", "--actor", "owner:isaac",
                  "--session-id", "s-1", "--binding-generation", "3"])
    assert issued.returncode == 0, issued.stderr[-600:]
    receipt = json.loads(issued.stdout)
    document = json.loads(plan.read_text(encoding="utf-8"))
    # The digest the approver signs off on is the one the compiler already showed them.
    assert receipt["planDigest"] == document["diffSummary"]["planDigest"]
    assert receipt["bootstrapOperationId"] == OPERATION_ID
    assert receipt["approvedBy"] == {"actor": "owner:isaac", "sessionId": "s-1",
                                     "bindingGeneration": 3}
    assert receipt["approvedAt"].endswith("Z")

    hermes = run([str(SCRIPTS / "authorize.py"), "--plan", str(plan),
                  "--authority", "HERMES", "--actor", "hermes:ceo"])
    assert hermes.returncode == 0, hermes.stderr[-600:]
    # The authority is the approver's to state, not the tool's to choose. Issuing OWNER for a
    # HERMES approval would let the weaker approval satisfy a plan that asked for the owner,
    # and OWNER outranks HERMES so the gate downstream would never notice.
    assert json.loads(hermes.stdout)["authority"] == "HERMES"


def test_publish_refuses_a_plan_document_that_carries_no_files(tmp_path):
    (tmp_path / "empty.json").write_text(json.dumps({"planCore": {}}), encoding="utf-8")
    done = run([str(SCRIPTS / "publish.py"), "--plan", str(tmp_path / "empty.json"),
                "--workdir", str(tmp_path / "w"), "--remote-url", "https://example.invalid/x.git",
                "--author-name", "a", "--author-email", "b@example.invalid"])
    assert done.returncode == 2
    assert "files" in done.stderr
