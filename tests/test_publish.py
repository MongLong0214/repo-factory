"""The genesis commit is the planned set, and nothing else (PRD §7 Phase G, §8)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

import publish as publish_module  # noqa: E402
from apply import authorized_plan_receipt  # noqa: E402
from plan import compile_plan  # noqa: E402
from publish import CommitLoreRefusal, PublishError, publish_files, publish_receipt  # noqa: E402

FILES = {"README.md": "# demo\n", ".agent-control-plane/project.json": "{}\n"}
IDENTITY = "github:MongLong0214/demo"
REMOTE = "git@github.com:MongLong0214/demo.git"


def doctor_report(checks=None, **summary):
    return json.dumps({"schema": "commitlore_doctor.v2", "status": "ok", "summary": summary,
                       "checks": checks or []})


def doctor_observation(profile, report):
    def run(argv, cwd):
        if argv[1] == "doctor":
            return 0, json.dumps(report), ""
        return 0, "ready", ""

    return publish_module.observe_commitlore(profile, Path("."), run)


def plan_for(files: Dict[str, str], profile: str = "SIMPLE") -> Dict[str, object]:
    """The plan states the bytes, not only the paths. The publisher is bound to both."""
    return {
        "bootstrapProfile": profile,
        "repositories": [{"role": "primary", "identity": IDENTITY, "visibility": "public"}],
        "files": [
            {"path": path,
             "contentDigest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
             "mode": "100644"}
            for path, content in sorted(files.items())
        ],
    }


def local_runner(pushes: List[List[str]], remote_heads: Dict[str, str] = None):
    """Runs git for real, but turns a push into a recorded no-op and answers `ls-remote` as a
    remote that accepted it would. `remote_heads` overrides that, which is how a push that
    reported success without moving the remote gets represented."""
    def run(argv: List[str], cwd: Path) -> Tuple[int, str, str]:
        if argv[0] == "commitlore":
            return 0, doctor_report() if argv[1] == "doctor" else "ready", ""
        if argv[:2] == ["git", "push"]:
            pushes.append(argv)
            return 0, "", ""
        if argv[:2] == ["git", "ls-remote"]:
            done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd),
                                  capture_output=True, text=True, timeout=60)
            head = done.stdout.strip()
            heads = remote_heads if remote_heads is not None else {"main": head, "dev": head}
            return 0, "".join(f"{sha}\trefs/heads/{ref}\n" for ref, sha in heads.items()), ""
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout, done.stderr
    return run


def publish(workdir: Path, files: Dict[str, str], pushes: List[List[str]],
            *, plan: Dict[str, object] = None, remote_url: str = REMOTE,
            remote_heads: Dict[str, str] = None, runner=None):
    return publish_files(files, plan=plan if plan is not None else plan_for(FILES),
                         repository_identity=IDENTITY,
                         workdir=workdir, remote_url=remote_url,
                         author_name="Test", author_email="test@example.com",
                         message="feat: genesis",
                         runner=runner or local_runner(pushes, remote_heads))


def test_it_publishes_exactly_the_planned_paths(tmp_path):
    pushes: List[List[str]] = []

    result = publish(tmp_path / "tree", FILES, pushes)

    assert result["committedPaths"] == sorted(FILES)
    assert len(result["head"]) == 40
    assert result["branches"] == ["main", "dev"]


def test_a_non_empty_target_is_refused_before_anything_is_written(tmp_path):
    # `git add -A` takes whatever is there. One left-over file in the genesis commit and the
    # plan's contentDigest set no longer names the bytes that landed.
    workdir = tmp_path / "tree"
    workdir.mkdir()
    (workdir / "left-over.txt").write_text("from a previous run\n")

    with pytest.raises(PublishError, match="not empty"):
        publish(workdir, FILES, [])


def test_an_unplanned_file_stops_the_push_rather_than_being_reported_after_it(tmp_path):
    # The check has to sit between commit and push. After the push, "they differed" is a
    # statement about a remote that already holds the unplanned bytes.
    pushes: List[List[str]] = []
    workdir = tmp_path / "tree"

    def sneaky(argv: List[str], cwd: Path):
        if argv[:3] == ["git", "add", "-A"]:
            (workdir / "unplanned.txt").write_text("smuggled\n")
        if argv[:2] == ["git", "push"] or argv[:3] == ["git", "remote", "add"]:
            pushes.append(argv)
            return 0, "", ""
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout, done.stderr

    with pytest.raises(PublishError, match="unplanned"):
        publish_files(FILES, plan=plan_for(FILES), repository_identity=IDENTITY,
                      workdir=workdir, remote_url=REMOTE,
                      author_name="Test", author_email="test@example.com",
                      message="feat: genesis", runner=sneaky)

    assert pushes == [], "nothing may reach the remote once the commit is not the planned set"


def test_a_planned_path_that_escapes_the_target_is_refused(tmp_path):
    escaping = {"../outside.txt": "no\n"}
    with pytest.raises(PublishError, match="escapes"):
        publish(tmp_path / "tree", escaping, [], plan=plan_for(escaping))


def test_main_is_pushed_before_dev(tmp_path):
    # Otherwise the first push sets the default branch to dev and the repository has no release
    # history until main arrives.
    pushes: List[List[str]] = []

    publish(tmp_path / "tree", FILES, pushes)

    ordered = [p[-1] for p in pushes if p[:2] == ["git", "push"]]
    assert ordered == ["main", "dev"]


# --- the plan binds the bytes and the destination, not only the path set -----------------

def test_content_rewritten_between_the_plan_and_the_commit_is_refused(tmp_path):
    """The path set can match while the bytes do not. A global git filter, autocrlf, or a hook
    is enough, and `git ls-files` cannot see any of them."""
    pushes: List[List[str]] = []
    workdir = tmp_path / "tree"

    def rewriting(argv: List[str], cwd: Path):
        if argv[:3] == ["git", "add", "-A"]:
            (workdir / "README.md").write_text("# something else\n")
        if argv[:2] == ["git", "push"] or argv[:3] == ["git", "remote", "add"]:
            pushes.append(argv)
            return 0, "", ""
        if argv[:2] == ["git", "ls-remote"]:
            return 0, "", ""
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout, done.stderr

    with pytest.raises(PublishError, match="not the planned bytes"):
        publish_files(FILES, plan=plan_for(FILES), repository_identity=IDENTITY,
                      workdir=workdir, remote_url=REMOTE,
                      author_name="Test", author_email="test@example.com",
                      message="feat: genesis", runner=rewriting)

    assert pushes == [], "nothing may reach the remote once the bytes are not the planned bytes"


def test_a_remote_that_is_not_the_planned_repository_is_refused(tmp_path):
    with pytest.raises(PublishError, match="the plan approved"):
        publish(tmp_path / "tree", FILES, [], remote_url="git@github.com:someone-else/demo.git")


def test_a_remote_this_publisher_cannot_read_is_refused_rather_than_assumed(tmp_path):
    """"Could not tell" and "matches" are different facts. Writing them as one value is how an
    unchecked destination reads as a checked one."""
    with pytest.raises(PublishError, match="cannot tell"):
        publish(tmp_path / "tree", FILES, [], remote_url="git@example:demo.git")


def test_a_push_that_did_not_move_the_remote_is_refused(tmp_path):
    """`git push` exiting 0 says the command did not fail. Whether the remote ref points at this
    commit is something only the remote can answer."""
    with pytest.raises(PublishError, match="does not carry the genesis commit"):
        publish(tmp_path / "tree", FILES, [], remote_heads={"main": "b" * 40, "dev": "b" * 40})


def test_the_result_states_which_repository_and_which_remote_heads(tmp_path):
    result = publish(tmp_path / "tree", FILES, [])

    assert result["repositoryIdentity"] == IDENTITY
    assert set(result["remoteHeads"]) == {"main", "dev"}
    assert all(head == result["head"] for head in result["remoteHeads"].values())


def absent_commitlore(pushes):
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[0] == "commitlore":
            raise FileNotFoundError("commitlore is not installed")
        return git_runner(argv, cwd)
    return run


# --- RF-S19: SIMPLE optional CommitLore absence is an explicit warning -----------------

def test_simple_missing_commitlore_warns_and_continues_with_a_receipt(tmp_path):
    pushes = []
    plan = plan_for(FILES, "SIMPLE")
    heads = publish(tmp_path / "tree", FILES, pushes, plan=plan,
                    runner=absent_commitlore(pushes))
    assert [p[-1] for p in pushes if p[:2] == ["git", "push"]] == ["main", "dev"]
    assert heads["commitlore"]["outcome"] == "WARN"
    assert "not installed" in heads["commitlore"]["detail"]
    receipt = publish_receipt({"bootstrapOperationId": "op", "requestDigest": "sha256:demo"},
                              heads, clock=lambda: "2026-08-19T10:00:00Z")
    assert receipt["commitlore"] == heads["commitlore"]
    assert receipt["commitlore"]["scope"] == "genesis-checkout"


def test_simple_success_is_pass_and_failure_is_never_pass(tmp_path):
    success = publish(tmp_path / "success", FILES, [], plan=plan_for(FILES, "SIMPLE"))
    assert success["commitlore"]["outcome"] == "PASS"
    pushes = []
    failure = publish(tmp_path / "failure", FILES, pushes, plan=plan_for(FILES, "SIMPLE"),
                      runner=absent_commitlore(pushes))
    assert failure["commitlore"]["outcome"] != "PASS"


def test_doctor_sees_origin_before_first_push_and_keeps_its_warning(tmp_path):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            code, origin, err = run(["git", "remote", "get-url", "origin"], cwd)
            if code != 0:
                return 2, "", f"no remote is configured: {err}"
            assert origin.strip() == REMOTE
            assert pushes == []
            return 0, doctor_report([{"id": "notes-refspec", "status": "warn",
                                      "detail": "check local notes fetch"}], padding="x" * 350), ""
        return git_runner(argv, cwd)

    heads = publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, "STANDARD"), runner=run)
    assert heads["commitlore"]["outcome"] == "PASS"
    assert heads["commitlore"]["scope"] == "genesis-checkout"
    assert heads["commitlore"]["warnings"] == ["notes-refspec: check local notes fetch"]
    assert heads["commitlore"]["detail"] == (
        "commitlore doctor passed: " + doctor_report(
            [{"id": "notes-refspec", "status": "warn", "detail": "check local notes fetch"}],
            padding="x" * 350)[-300:])
    assert [p[-1] for p in pushes] == ["main", "dev"]


@pytest.mark.parametrize("profile,outcome", [("SIMPLE", "WARN"), ("GUARDED", "BLOCK")])
@pytest.mark.parametrize("command", ["init", "doctor"])
def test_commitlore_timeout_uses_profile_policy(tmp_path, profile, outcome, command):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", command]:
            raise subprocess.TimeoutExpired(argv, 180)
        return git_runner(argv, cwd)

    if outcome == "BLOCK":
        with pytest.raises(CommitLoreRefusal, match="COMMITLORE_BLOCK") as caught:
            publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, profile), runner=run)
        observed = caught.value.observation
        assert pushes == []
    else:
        heads = publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, profile), runner=run)
        observed = heads["commitlore"]
        assert [p[-1] for p in pushes] == ["main", "dev"]
    assert observed == {"outcome": outcome, "scope": "genesis-checkout", "warnings": [],
                        "detail": f"commitlore {command} timed out after 180s"}


@pytest.mark.parametrize("failing_command", ["init", "doctor"])
def test_simple_nonzero_commitlore_command_is_a_warning(tmp_path, failing_command):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", failing_command]:
            if failing_command == "doctor":
                return 2, doctor_report([{"id": "failed-check", "status": "fail",
                                          "detail": "observed failure"}]), ""
            return 2, "", "observed failure"
        return git_runner(argv, cwd)

    heads = publish(tmp_path / failing_command, FILES, pushes,
                    plan=plan_for(FILES, "SIMPLE"), runner=run)
    observed = heads["commitlore"]
    assert observed["outcome"] == "WARN"
    assert observed["scope"] == "genesis-checkout"
    assert observed["warnings"] == (["failed-check: observed failure"] if failing_command == "doctor" else [])
    assert observed["detail"] == ("commitlore doctor failed (2): " + doctor_report(
        [{"id": "failed-check", "status": "fail", "detail": "observed failure"}])
        if failing_command == "doctor" else "commitlore init failed (2): observed failure")


@pytest.mark.parametrize("profile,exit_code,expected", [
    ("STANDARD", 0, "PASS"), ("SIMPLE", 1, "WARN"),
    ("STANDARD", 1, "REVISE"), ("GUARDED", 1, "BLOCK"),
])
def test_doctor_warning_keeps_its_line_and_exit_code_policy(tmp_path, profile, exit_code, expected):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            return exit_code, doctor_report([{"id": "notes-refspec", "status": "warn",
                                              "detail": "notes fetch has no remote notes yet"}]), ""
        return git_runner(argv, cwd)

    if expected in ("REVISE", "BLOCK"):
        with pytest.raises(CommitLoreRefusal) as caught:
            publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, profile), runner=run)
        observation = caught.value.observation
        assert pushes == []
    else:
        heads = publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, profile), runner=run)
        observation = heads["commitlore"]
    assert observation["outcome"] == expected
    assert observation["scope"] == "genesis-checkout"
    assert observation["warnings"] == ["notes-refspec: notes fetch has no remote notes yet"]
    if exit_code == 0:
        receipt = publish_receipt({"bootstrapOperationId": "op", "requestDigest": "sha256:demo"},
                                  heads, clock=lambda: "2026-08-19T10:00:00Z")
        assert receipt["commitlore"]["warnings"] == observation["warnings"]


def test_doctor_preserves_all_fifteen_structured_warnings(tmp_path):
    git_runner = local_runner([])

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            return 0, doctor_report([{"id": f"check-{index}", "status": "warn",
                                      "detail": f"warning {index} " + "x" * 350}
                                     for index in range(15)]), ""
        return git_runner(argv, cwd)

    heads = publish(tmp_path / "tree", FILES, [], plan=plan_for(FILES, "SIMPLE"), runner=run)
    assert heads["commitlore"]["outcome"] == "PASS"
    assert heads["commitlore"]["warnings"] == [f"check-{index}: warning {index} " + "x" * 350
                                                for index in range(15)]
    assert "check-11" not in heads["commitlore"]["detail"]


def test_doctor_skipped_check_and_degraded_report_remain_visible(tmp_path):
    git_runner = local_runner([])

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            report = json.loads(doctor_report([
                {"id": "inject-version", "status": "skipped", "skipReason": "version_unreadable",
                 "title": "PreToolUse hook version", "detail": "could not read the hook version"},
            ]))
            report["status"] = "degraded"
            return 0, json.dumps(report), ""
        return git_runner(argv, cwd)

    heads = publish(tmp_path / "tree", FILES, [], plan=plan_for(FILES, "STANDARD"), runner=run)
    assert heads["commitlore"]["outcome"] == "PASS"
    assert heads["commitlore"]["reportStatus"] == "degraded"
    assert heads["commitlore"]["warnings"] == ["inject-version: skipped (version_unreadable)"]


def test_doctor_skipped_check_without_reason_uses_title(tmp_path):
    git_runner = local_runner([])

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            return 0, doctor_report([{"id": "inject-version", "status": "skipped",
                                      "title": "PreToolUse hook version"}]), ""
        return git_runner(argv, cwd)

    heads = publish(tmp_path / "tree", FILES, [], runner=run)
    assert heads["commitlore"]["warnings"] == ["inject-version: skipped (PreToolUse hook version)"]


def test_doctor_zero_warning_summary_is_not_a_finding(tmp_path):
    git_runner = local_runner([])

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "doctor"]:
            return 0, doctor_report([{"id": "ready", "status": "ok", "detail": "all set"}],
                                    warn=0, text="0 warnings"), ""
        return git_runner(argv, cwd)

    heads = publish(tmp_path / "tree", FILES, [], runner=run)
    assert heads["commitlore"]["outcome"] == "PASS"
    assert heads["commitlore"]["warnings"] == []


@pytest.mark.parametrize("profile,expected", [("GUARDED", "BLOCK"), ("SIMPLE", "WARN")])
def test_failed_doctor_report_with_zero_process_exit_uses_profile_policy(profile, expected):
    report = json.loads(doctor_report([{"id": "notes-refspec", "status": "warn",
                                        "detail": "notes are not shared"}]))
    report.update(status="failed", exitCode=0)

    observed = doctor_observation(profile, report)

    assert observed == {"outcome": expected, "scope": "genesis-checkout",
                        "warnings": ["notes-refspec: notes are not shared"],
                        "reportStatus": "failed",
                        "detail": "commitlore doctor report status is failed while the process exited 0"}


def test_failed_check_in_degraded_doctor_report_with_zero_process_exit_uses_failure_policy():
    report = json.loads(doctor_report([
        {"id": "notes-refspec", "status": "warn", "detail": "notes are not shared"},
        {"id": "hook", "status": "fail", "detail": "hook is broken"},
        {"id": "optional", "status": "skipped", "skipReason": "not_applicable"},
    ]))
    report.update(status="degraded", exitCode=0)

    observed = doctor_observation("GUARDED", report)

    assert observed == {"outcome": "BLOCK", "scope": "genesis-checkout",
                        "warnings": ["notes-refspec: notes are not shared", "hook: hook is broken",
                                     "optional: skipped (not_applicable)"],
                        "reportStatus": "degraded",
                        "detail": "commitlore doctor a check has status fail while the process exited 0"}


@pytest.mark.parametrize("report_exit_code", [1, False])
def test_inconsistent_doctor_report_exit_code_with_zero_process_exit_uses_failure_policy(report_exit_code):
    report = json.loads(doctor_report())
    report["exitCode"] = report_exit_code

    observed = doctor_observation("SIMPLE", report)

    assert observed == {"outcome": "WARN", "scope": "genesis-checkout", "warnings": [],
                        "reportStatus": "ok",
                        "detail": (f"commitlore doctor report exitCode is {report_exit_code!r} "
                                   "while the process exited 0")}


def test_unknown_doctor_report_status_uses_failure_policy():
    report = json.loads(doctor_report())
    report["status"] = "healthy"

    observed = doctor_observation("GUARDED", report)

    assert observed == {"outcome": "BLOCK", "scope": "genesis-checkout", "warnings": [],
                        "detail": "commitlore doctor returned an invalid JSON report: invalid status"}


def test_ok_doctor_report_with_zero_process_exit_passes():
    report = json.loads(doctor_report([{"id": "ready", "status": "ok"}]))
    report["exitCode"] = 0

    observed = doctor_observation("GUARDED", report)

    assert observed["outcome"] == "PASS"
    assert observed["reportStatus"] == "ok"
    assert observed["warnings"] == []


# --- RF-S20: STANDARD required CommitLore absence requires bootstrap revision -----------

def test_standard_missing_commitlore_refuses_before_push_for_revision(tmp_path):
    pushes = []
    with pytest.raises(CommitLoreRefusal, match="COMMITLORE_REVISE.*bootstrap revision required") as caught:
        publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, "STANDARD"),
                runner=absent_commitlore(pushes))
    assert caught.value.observation["outcome"] == "REVISE"
    assert pushes == []


# --- RF-S21: GUARDED required Decision Memory absence blocks publication ---------------

def test_guarded_missing_commitlore_refuses_before_push_as_blocking(tmp_path):
    pushes = []
    with pytest.raises(CommitLoreRefusal, match="COMMITLORE_BLOCK.*blocking") as caught:
        publish(tmp_path / "tree", FILES, pushes, plan=plan_for(FILES, "GUARDED"),
                runner=absent_commitlore(pushes))
    assert caught.value.observation["outcome"] == "BLOCK"
    assert pushes == []


@pytest.mark.parametrize("profile,mode", [("SIMPLE", "preferred"),
                                           ("STANDARD", "required"),
                                           ("GUARDED", "required")])
def test_real_commitlore_leaves_genesis_tracked_files_and_blobs_unchanged(tmp_path, profile, mode):
    if shutil.which("commitlore") is None:
        pytest.skip("commitlore executable is unavailable; injected-runner tests cover the stage")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    rewritten = {**os.environ, "GIT_CONFIG_COUNT": "1",
                 "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf", "GIT_CONFIG_VALUE_0": REMOTE}
    request = {"schema": "repo-factory.bootstrap-request.v1", "runId": "clone",
               "seed": "a demo", "bootstrapProfile": profile, "priority": "NORMAL",
               "repositories": [{"role": "primary", "name": "demo"}],
               "visibility": "public", "origin": {"channel": "cli"}}
    verification = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary",
                     "cwd": ".", "timeoutSeconds": 600, "envAllowlist": ["CI"],
                     "network": "deny", "required": True}]
    compiled = compile_plan(request, verification, stack="node",
                            ci_values={"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22",
                                       "INSTALL_CMD": "npm install", "TEST_CMD": "npm test",
                                       "BUILD_CMD": "node --check index.js"},
                            operation_id="11111111-2222-3333-4444-555555555555")
    files = dict(compiled["files"])

    def run(argv, cwd):
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              timeout=180, env=rewritten)
        return done.returncode, done.stdout, done.stderr

    workdir = tmp_path / "tree"
    heads = publish(workdir, files, [], plan=plan_for(files, profile), runner=run)
    assert heads["commitlore"]["outcome"] == "PASS", heads["commitlore"]
    receipt = publish_receipt(compiled["planCore"], heads,
                              clock=lambda: "2026-08-19T10:00:00Z")
    assert receipt["commitlore"]["scope"] == "genesis-checkout"
    tracked = subprocess.run(["git", "ls-files"], cwd=workdir, capture_output=True,
                             text=True, check=True).stdout.splitlines()
    assert tracked == sorted(files)
    for path in tracked:
        blob = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=workdir,
                              capture_output=True, check=True).stdout
        assert hashlib.sha256(blob).hexdigest() == hashlib.sha256(files[path].encode()).hexdigest()
        assert (workdir / path).read_bytes() == blob
    assert subprocess.run(["git", "status", "--porcelain"], cwd=workdir,
                          capture_output=True, text=True, check=True).stdout == ""
    clone = tmp_path / "fresh-clone"
    subprocess.run(["git", "clone", "-q", "-b", "dev", str(bare), str(clone)], check=True)
    manifest = json.loads((clone / ".agent-control-plane/project.json").read_text(encoding="utf-8"))
    assert manifest["commitlore"]["mode"] == mode
    agents = (clone / "AGENTS.md").read_text(encoding="utf-8")
    assert "## CommitLore" in agents
    assert f"`{mode}`" in agents
    assert "commitlore init --mcp-scope none" in agents
    assert "commitlore doctor" in agents
    assert agents.index("commitlore init --mcp-scope none") < agents.index("commitlore doctor")
    assert "commit trailers" in agents
    assert heads["commitlore"]["scope"] == "genesis-checkout"
    assert subprocess.run(["git", "ls-files"], cwd=clone, capture_output=True,
                          text=True, check=True).stdout.splitlines() == sorted(files)
    assert (workdir / ".git/hooks/commit-msg").is_file()
    assert not (clone / ".git/hooks/commit-msg").exists()
    source_config = (workdir / ".git/config").read_text(encoding="utf-8")
    clone_config = (clone / ".git/config").read_text(encoding="utf-8")
    assert source_config != clone_config
    assert REMOTE in source_config
    assert REMOTE not in clone_config


def test_publish_refuses_a_receipt_for_another_digest_before_any_git_command(tmp_path, monkeypatch, capsys):
    core = {**plan_for(FILES, "STANDARD"), "bootstrapOperationId": "op-1",
            "authorization": "OWNER"}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"planCore": core, "files": FILES}), encoding="utf-8")
    other = {**core, "bootstrapProfile": "SIMPLE"}
    approval = authorized_plan_receipt(other, authority="OWNER", actor="owner:test",
                                       approved_at="2026-08-19T09:00:00Z")
    auth_path = tmp_path / "authorization.json"
    auth_path.write_text(json.dumps(approval), encoding="utf-8")
    calls = []

    def run(argv, cwd, env=None):
        calls.append(argv)
        raise AssertionError("git must not run before authorization")

    monkeypatch.setitem(publish_module.publish_files.__kwdefaults__, "runner", run)
    assert publish_module.main(["--plan", str(plan_path), "--authorization", str(auth_path),
                                "--workdir", str(tmp_path / "work"), "--remote-url", REMOTE,
                                "--ledger", str(tmp_path / "receipts.json"),
                                "--author-name", "Test", "--author-email", "test@example.com"]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "AUTHORIZATION_MISSING"
    assert calls == []
    assert not (tmp_path / "work").exists()


def test_commitlore_file_effect_refuses_publication(tmp_path):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", "init"]:
            (cwd / "README.md").write_text("changed after genesis\n")
            return 0, "", ""
        return git_runner(argv, cwd)

    with pytest.raises(PublishError, match="changed the generated checkout"):
        publish(tmp_path / "tree", FILES, pushes, runner=run)
    assert pushes == []
