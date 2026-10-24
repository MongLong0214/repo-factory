"""RF-S08 — the Node template installs a declared dependency, by the commands it renders.

The generated skeleton declares no dependency, so `npm install` on it installs nothing and
"actually installs" was never shown (the 2026-08-19 run printed "up to date, audited 1
package"). Here the generated tree gets one pinned dependency and its lock from a fixture, and
the rendered workflow's own `run:` steps execute in order, as Actions runs them, under an
isolated HOME and npm cache with the network turned off. The dependency has to be absent before
the install step, and present at the pinned version and running after it.

Runtimes: every directory in RF_S08_NODE_BIN_DIRS (os.pathsep-separated, each holding `node`
and `npm`), or the `node` on PATH when it is unset. A configured value with an unusable entry,
including an empty one, stops collection; it never runs as fewer runtimes or as a skip.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from xml.etree import ElementTree
from unittest.mock import patch

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from materialize import CI_PATH, SKELETONS  # noqa: E402
from plan import compile_plan  # noqa: E402

FIXTURE = SKILL / "tests" / "fixtures" / "node-install-witness"
DEPENDENCY, PINNED = "rf-witness-dep", "1.0.0"
TARBALL = f"{DEPENDENCY}-{PINNED}.tgz"
REQUEST = {
    "schema": "repo-factory.bootstrap-request.v1", "runId": "run-s08",
    "seed": "a node project whose install is witnessed", "bootstrapProfile": "SIMPLE",
    "priority": "NORMAL", "repositories": [{"role": "primary", "name": "rf-s08-witness", "stack": "node"}],
    "visibility": "public", "origin": {"channel": "cli"},
}
VERIFICATION = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
                 "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True}]


def usable_runtime(entry: str) -> bool:
    return bool(entry) and os.path.isabs(entry) and all(
        (Path(entry) / tool).is_file() and os.access(Path(entry) / tool, os.X_OK) for tool in ("node", "npm"))


def node_bin_dirs():
    """A configured list is checked here, before parametrization. Parametrizing over an empty list
    does not fail: pytest skips the test and exits 0, so `RF_S08_NODE_BIN_DIRS=:` reported success
    with no install witnessed. An unusable entry is refused, not dropped — dropping one is how a
    two-runtime witness quietly becomes a one-runtime witness."""
    if "RF_S08_NODE_BIN_DIRS" in os.environ:
        configured = os.environ["RF_S08_NODE_BIN_DIRS"]
        entries = configured.split(os.pathsep)
        unusable = [entry for entry in entries if not usable_runtime(entry)]
        if unusable:
            raise RuntimeError(
                f"RF_S08_NODE_BIN_DIRS={configured!r} has unusable entries {unusable!r}: each "
                f"entry must be an absolute directory holding executable node and npm")
        return [Path(entry) for entry in entries]
    node = shutil.which("node")
    return [Path(node).parent] if node else [None]


RUNTIMES = node_bin_dirs()


def generated_tree(root: Path, lock: dict = None):
    """The product's files for a SIMPLE node request, plus the fixture. The skeleton is not edited
    beyond declaring the one dependency the fixture brings."""
    original = SKELETONS["node"]
    def with_dependency(*args):
        emitted = original(*args)
        package = json.loads(emitted["package.json"])
        assert "dependencies" not in package, "the skeleton now declares dependencies; revisit this fixture"
        package["dependencies"] = {DEPENDENCY: f"file:vendor/{TARBALL}"}
        emitted["package.json"] = json.dumps(package, indent=2, sort_keys=True) + "\n"
        return emitted
    # Declare the dependency before materialization so the generator chooses the install step.
    with patch.dict(SKELETONS, {"node": with_dependency}):
        compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION,
                                operation_id="11111111-2222-3333-4444-555555555555")
    assert compiled["unresolvedGaps"] == []
    tree = root / "tree"
    for path, text in compiled["files"].items():
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_text(text, encoding="utf-8")
    (tree / "vendor").mkdir()
    shutil.copy2(FIXTURE / TARBALL, tree / "vendor" / TARBALL)
    lock = lock or json.loads((FIXTURE / "package-lock.json").read_text(encoding="utf-8"))
    (tree / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(FIXTURE / "dependency.test.js", tree / "test" / "dependency.test.js")
    workflow = (tree / CI_PATH).read_text(encoding="utf-8")
    matrix_job = workflow.split("\n  project-ci-gate:", 1)[0]
    return tree, workflow, re.findall(r"^\s+run: (.+)$", matrix_job, re.MULTILINE)


def isolated(root: Path, bin_dir: Path) -> dict:
    """Nothing from the host but the runtime: HOME, npm cache and npm config live under `root`,
    and `offline` keeps npm off the registry, which this dependency does not need."""
    home, cache = root / "home", root / "npm-cache"
    home.mkdir()
    cache.mkdir()
    return {"PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]), "HOME": str(home),
            "npm_config_cache": str(cache), "npm_config_userconfig": str(home / ".npmrc"),
            "npm_config_globalconfig": str(home / "global-npmrc"), "npm_config_offline": "true",
            "npm_config_update_notifier": "false", "CI": "true"}


def run_job(tree: Path, env: dict, steps):
    """Actions semantics for `run:` steps: one `bash -eo pipefail` per step, in order, and the first
    failure ends the job."""
    bash = shutil.which("bash", path=env["PATH"])
    ran = []
    for command in steps:
        done = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", command],
                              cwd=tree, env=env, capture_output=True, text=True, timeout=300)
        ran.append((command, done.returncode, done.stdout + done.stderr))
        if done.returncode != 0:
            break
    return ran


def resolves(tree: Path, env: dict) -> bool:
    probe = f"import('{DEPENDENCY}').then(() => process.exit(0), () => process.exit(3))"
    return subprocess.run([str(Path(env["PATH"].split(os.pathsep)[0]) / "node"), "-e", probe],
                          cwd=tree, env=env, capture_output=True, timeout=60).returncode == 0


# --- RF-S08: the rendered install step installs a pinned dependency on each runtime --------

def test_the_fixture_dependency_is_the_source_beside_it_and_the_lock_pins_its_bytes():
    with tarfile.open(FIXTURE / TARBALL) as archive:
        packed = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    assert packed == {f"package/{name}": (FIXTURE / DEPENDENCY / name).read_bytes()
                      for name in ("index.js", "package.json")}
    entry = json.loads((FIXTURE / "package-lock.json").read_text())["packages"][f"node_modules/{DEPENDENCY}"]
    digest = base64.b64encode(hashlib.sha512((FIXTURE / TARBALL).read_bytes()).digest()).decode()
    assert entry == {"version": PINNED, "resolved": f"file:vendor/{TARBALL}", "integrity": f"sha512-{digest}"}


@pytest.mark.parametrize("bin_dir", RUNTIMES)
def test_the_rendered_steps_install_the_pinned_dependency_and_run_it(bin_dir, tmp_path):
    assert bin_dir is not None, "RF-S08 needs a node runtime: put node on PATH or set RF_S08_NODE_BIN_DIRS"
    tree, _, steps = generated_tree(tmp_path)
    env = isolated(tmp_path, bin_dir)
    assert steps == ["npm install", "npm test", "node --check index.js"]

    assert not (tree / "node_modules").exists()
    assert not resolves(tree, env)

    ran = run_job(tree, env, steps)
    assert [(command, code) for command, code, _ in ran] == [(step, 0) for step in steps], ran[-1][2][-2000:]
    installed = json.loads((tree / "node_modules" / DEPENDENCY / "package.json").read_text())
    assert installed["version"] == PINNED
    assert resolves(tree, env)
    # The dependency's own code ran under `npm test`: the skeleton's test and the fixture's.
    assert re.search(r"^(?:#|ℹ) pass 2$", ran[1][2], re.MULTILINE), ran[1][2][-2000:]
    assert re.search(r"^(?:#|ℹ) fail 0$", ran[1][2], re.MULTILINE)
    # Installed from the lock, not re-resolved; and npm wrote to the isolated cache, not the host's.
    assert json.loads((tree / "package-lock.json").read_text()) == json.loads(
        (FIXTURE / "package-lock.json").read_text())
    assert (tmp_path / "npm-cache" / "_logs").is_dir()


@pytest.mark.parametrize("bin_dir", RUNTIMES)
def test_a_failed_install_is_not_followed_by_steps_that_count_as_success(bin_dir, tmp_path):
    assert bin_dir is not None, "RF-S08 needs a node runtime: put node on PATH or set RF_S08_NODE_BIN_DIRS"
    lock = json.loads((FIXTURE / "package-lock.json").read_text())
    entry = lock["packages"][f"node_modules/{DEPENDENCY}"]
    entry["integrity"] = "sha512-" + base64.b64encode(hashlib.sha512(b"other bytes").digest()).decode()
    tree, workflow, steps = generated_tree(tmp_path, lock)
    env = isolated(tmp_path, bin_dir)

    ran = run_job(tree, env, steps)
    assert [(command, code != 0) for command, code, _ in ran] == [("npm install", True)]
    assert "EINTEGRITY" in ran[0][2]
    assert not (tree / "node_modules" / DEPENDENCY).exists()
    # On Actions the job stops the same way: no step may run after a failure or ignore one.
    assert "continue-on-error" not in workflow
    assert not re.search(r"^\s+if:", workflow.split("\n  project-ci-gate:", 1)[0], re.MULTILINE)
    # And forcing the later steps does not manufacture a pass: the test step needs the dependency.
    forced = run_job(tree, env, steps[1:])
    assert forced[0][0] == "npm test" and forced[0][1] != 0


# --- RF-S08: a configured runtime list cannot shrink the witness to nothing ----------------

WITNESSES = ("test_the_rendered_steps_install_the_pinned_dependency_and_run_it",
             "test_a_failed_install_is_not_followed_by_steps_that_count_as_success")


@pytest.mark.parametrize("prefix", ["#", "ℹ"])
def test_install_witness_accepts_both_node_summary_formats(prefix, tmp_path, monkeypatch):
    original = run_job
    def with_summary_format(*args):
        return [(cmd, code, re.sub(r"^(?:#|ℹ) (pass|fail) (\d+)$",
                                  rf"{prefix} \1 \2", output, flags=re.MULTILINE))
                for cmd, code, output in original(*args)]
    monkeypatch.setitem(globals(), "run_job", with_summary_format)
    test_the_rendered_steps_install_the_pinned_dependency_and_run_it(RUNTIMES[0], tmp_path)


def run_witnesses(tmp_path: Path, configured: str):
    """The execution witnesses in a child pytest under one RF_S08_NODE_BIN_DIRS value, counted from
    its JUnit report: what ran, not what was collected."""
    report = tmp_path / "report.xml"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "RF_S08_NODE_BIN_DIRS": configured}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
         *[f"tests/test_node_install_witness.py::{name}" for name in WITNESSES],
         "--basetemp", str(tmp_path / "run"), "--junitxml", str(report)],
        cwd=SKILL, env=env, capture_output=True, text=True, timeout=900)
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for case in ElementTree.parse(report).getroot().iter("testcase"):
        outcome = {child.tag for child in case} & {"failure", "error", "skipped"}
        counts[{"failure": "failed", "error": "errors", "skipped": "skipped"}[outcome.pop()]
               if outcome else "passed"] += 1
    return done, counts


@pytest.mark.parametrize("configured", [
    os.pathsep, "", str(SKILL), "bin",
    os.pathsep.join([str(next(iter(RUNTIMES), None) or SKILL), str(SKILL / "no-such-runtime")]),
], ids=["separators-only", "empty", "no-node-here", "relative", "one-of-two-missing"])
def test_a_configured_runtime_list_with_an_unusable_entry_fails_instead_of_skipping(configured, tmp_path):
    done, counts = run_witnesses(tmp_path, configured)
    assert done.returncode != 0, (counts, done.stdout[-1500:])
    assert counts["passed"] == counts["skipped"] == 0, counts
    assert "RF_S08_NODE_BIN_DIRS" in done.stdout + done.stderr


def test_a_usable_runtime_list_runs_every_execution_witness_on_every_runtime(tmp_path):
    assert None not in RUNTIMES, "RF-S08 needs a node runtime: put node on PATH or set RF_S08_NODE_BIN_DIRS"
    done, counts = run_witnesses(tmp_path, os.pathsep.join(str(runtime) for runtime in RUNTIMES))
    assert done.returncode == 0, done.stdout[-1500:]
    assert counts == {"passed": len(WITNESSES) * len(RUNTIMES), "failed": 0, "errors": 0, "skipped": 0}
