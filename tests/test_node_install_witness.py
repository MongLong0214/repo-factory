"""RF-S08 — the Node template installs a declared dependency, by the commands it renders.

The generated skeleton declares no dependency, so `npm install` on it installs nothing and
"actually installs" was never shown (the 2026-08-19 run printed "up to date, audited 1
package"). Here the generated tree gets one pinned dependency and its lock from a fixture, and
the rendered workflow's own `run:` steps execute in order, as Actions runs them, under an
isolated HOME and npm cache with the network turned off. The dependency has to be absent before
the install step, and present at the pinned version and running after it.

Runtimes: every directory in RF_S08_NODE_BIN_DIRS (os.pathsep-separated, each holding `node`
and `npm`), or the `node` on PATH when it is unset.
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

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from materialize import CI_PATH  # noqa: E402
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


def node_bin_dirs():
    configured = os.environ.get("RF_S08_NODE_BIN_DIRS")
    if configured:
        return [Path(entry) for entry in configured.split(os.pathsep) if entry]
    node = shutil.which("node")
    return [Path(node).parent] if node else [None]


RUNTIMES = node_bin_dirs()


def generated_tree(root: Path, lock: dict = None):
    """The product's files for a SIMPLE node request, plus the fixture. The skeleton is not edited
    beyond declaring the one dependency the fixture brings."""
    compiled = compile_plan(copy.deepcopy(REQUEST), VERIFICATION,
                            operation_id="11111111-2222-3333-4444-555555555555")
    assert compiled["unresolvedGaps"] == []
    tree = root / "tree"
    for path, text in compiled["files"].items():
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_text(text, encoding="utf-8")
    package = json.loads((tree / "package.json").read_text(encoding="utf-8"))
    assert "dependencies" not in package, "the skeleton now declares dependencies; revisit this fixture"
    package["dependencies"] = {DEPENDENCY: f"file:vendor/{TARBALL}"}
    (tree / "package.json").write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (tree / "vendor").mkdir()
    shutil.copy2(FIXTURE / TARBALL, tree / "vendor" / TARBALL)
    lock = lock or json.loads((FIXTURE / "package-lock.json").read_text(encoding="utf-8"))
    (tree / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(FIXTURE / "dependency.test.js", tree / "test" / "dependency.test.js")
    workflow = (tree / CI_PATH).read_text(encoding="utf-8")
    return tree, workflow, re.findall(r"^\s+run: (.+)$", workflow, re.MULTILINE)


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
    assert re.search(r"^# pass 2$", ran[1][2], re.MULTILINE), ran[1][2][-2000:]
    assert re.search(r"^# fail 0$", ran[1][2], re.MULTILINE)
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
    assert not re.search(r"^\s+if:", workflow, re.MULTILINE)
    # And forcing the later steps does not manufacture a pass: the test step needs the dependency.
    forced = run_job(tree, env, steps[1:])
    assert forced[0][0] == "npm test" and forced[0][1] != 0
