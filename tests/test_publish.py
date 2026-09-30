"""The genesis commit is the planned set, and nothing else (PRD §7 Phase G, §8)."""
from __future__ import annotations

import subprocess
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from publish import CommitLoreRefusal, PublishError, publish_files, publish_receipt  # noqa: E402

import hashlib

FILES = {"README.md": "# demo\n", ".agent-control-plane/project.json": "{}\n"}
IDENTITY = "github:MongLong0214/demo"
REMOTE = "git@github.com:MongLong0214/demo.git"


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
            return 0, "ready", ""
        if argv[:2] == ["git", "push"] or argv[:3] == ["git", "remote", "add"]:
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


def test_simple_success_is_pass_and_failure_is_never_pass(tmp_path):
    success = publish(tmp_path / "success", FILES, [], plan=plan_for(FILES, "SIMPLE"))
    assert success["commitlore"]["outcome"] == "PASS"
    pushes = []
    failure = publish(tmp_path / "failure", FILES, pushes, plan=plan_for(FILES, "SIMPLE"),
                      runner=absent_commitlore(pushes))
    assert failure["commitlore"]["outcome"] != "PASS"


@pytest.mark.parametrize("failing_command", ["init", "doctor"])
def test_simple_nonzero_commitlore_command_is_a_warning(tmp_path, failing_command):
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[:2] == ["commitlore", failing_command]:
            return 2, "", "observed failure"
        return git_runner(argv, cwd)

    heads = publish(tmp_path / failing_command, FILES, pushes,
                    plan=plan_for(FILES, "SIMPLE"), runner=run)
    assert heads["commitlore"] == {"outcome": "WARN",
                                    "detail": f"commitlore {failing_command} failed (2): observed failure"}


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


def test_real_commitlore_leaves_genesis_tracked_files_and_blobs_unchanged(tmp_path):
    if shutil.which("commitlore") is None:
        pytest.skip("commitlore executable is unavailable; injected-runner tests cover the stage")
    pushes = []
    git_runner = local_runner(pushes)

    def run(argv, cwd):
        if argv[0] == "commitlore":
            done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=90)
            return done.returncode, done.stdout, done.stderr
        return git_runner(argv, cwd)

    workdir = tmp_path / "tree"
    heads = publish(workdir, FILES, pushes, plan=plan_for(FILES), runner=run)
    assert heads["commitlore"]["outcome"] == "PASS", heads["commitlore"]
    tracked = subprocess.run(["git", "ls-files"], cwd=workdir, capture_output=True,
                             text=True, check=True).stdout.splitlines()
    assert tracked == sorted(FILES)
    for path in tracked:
        blob = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=workdir,
                              capture_output=True, check=True).stdout
        assert hashlib.sha256(blob).hexdigest() == hashlib.sha256(FILES[path].encode()).hexdigest()
        assert (workdir / path).read_bytes() == blob
    assert subprocess.run(["git", "status", "--porcelain"], cwd=workdir,
                          capture_output=True, text=True, check=True).stdout == ""


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
