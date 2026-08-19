"""The pipeline the documentation describes has to be runnable as documented.

`apply` and `publish` were library-only for a while. Every test passed, because every test
called them as Python functions — the same reason `plan.py` could ship without a way to supply
CI values and nobody noticed. A published skill whose middle stages have no command line is a
document describing something other than the thing that ships.
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

STAGES = ["plan.py", "apply.py", "publish.py", "result.py"]
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
    assert [op["operationId"] for op in would] == ["create-ruleset:demo"]
    # The dry run has to show the effect, not just the name — that is the thing under approval.
    assert would[0]["desiredState"]["enforcement"] == "active"

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


def test_publish_refuses_a_plan_document_that_carries_no_files(tmp_path):
    (tmp_path / "empty.json").write_text(json.dumps({"planCore": {}}), encoding="utf-8")
    done = run([str(SCRIPTS / "publish.py"), "--plan", str(tmp_path / "empty.json"),
                "--workdir", str(tmp_path / "w"), "--remote-url", "https://example.invalid/x.git",
                "--author-name", "a", "--author-email", "b@example.invalid"])
    assert done.returncode == 2
    assert "files" in done.stderr
