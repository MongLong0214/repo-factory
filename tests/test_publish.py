"""The genesis commit is the planned set, and nothing else (PRD §7 Phase G, §8)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from publish import PublishError, publish_files  # noqa: E402

FILES = {"README.md": "# demo\n", ".agent-control-plane/project.json": "{}\n"}


def local_runner(pushes: List[List[str]]):
    """Runs git for real, but turns a push into a recorded no-op — there is no remote here."""
    def run(argv: List[str], cwd: Path) -> Tuple[int, str, str]:
        if argv[:2] == ["git", "push"] or argv[:3] == ["git", "remote", "add"]:
            pushes.append(argv)
            return 0, "", ""
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout, done.stderr
    return run


def publish(workdir: Path, files: Dict[str, str], pushes: List[List[str]]):
    return publish_files(files, workdir=workdir, remote_url="git@example:demo.git",
                         author_name="Test", author_email="test@example.com",
                         message="feat: genesis", runner=local_runner(pushes))


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
        publish_files(FILES, workdir=workdir, remote_url="git@example:demo.git",
                      author_name="Test", author_email="test@example.com",
                      message="feat: genesis", runner=sneaky)

    assert pushes == [], "nothing may reach the remote once the commit is not the planned set"


def test_a_planned_path_that_escapes_the_target_is_refused(tmp_path):
    with pytest.raises(PublishError, match="escapes"):
        publish(tmp_path / "tree", {"../outside.txt": "no\n"}, [])


def test_main_is_pushed_before_dev(tmp_path):
    # Otherwise the first push sets the default branch to dev and the repository has no release
    # history until main arrives.
    pushes: List[List[str]] = []

    publish(tmp_path / "tree", FILES, pushes)

    ordered = [p[-1] for p in pushes if p[:2] == ["git", "push"]]
    assert ordered == ["main", "dev"]
