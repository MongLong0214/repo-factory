"""What the published documents tell a reader to run must be the thing that ships.

`SKILL.md` is the skill's entrypoint: invoking the skill loads it, so it decides what actually
gets executed. It described the retired product — S/M/L tiers, an operating kernel copied into
the generated repository, an autopilot loop — while `scripts/` had become a bootstrap compiler.
Reading the source and running the skill led to two different products, and no check compared
them, because every check was pointed at the code.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

SKILL = Path(__file__).resolve().parent.parent
DOCUMENTS = ["SKILL.md", "README.md"]

# Entrypoints of the product that was retired. They may be named in prose — saying "do not run
# these" requires naming them — but a fenced command block is an instruction to run.
RETIRED_ENTRYPOINTS = [
    "phase-gate.py", "create-issues.py", "verify-citations.py",
    "install-governance.py", "run-canary.py", "autopilot.py", "merge-broker.py",
]

PIPELINE = ["plan.py", "apply.py", "publish.py", "result.py"]

COMMAND_BLOCK = re.compile(r"```(?:bash|sh|console)\n(.*?)```", re.DOTALL)
SCRIPT_CALL = re.compile(r"scripts/([A-Za-z0-9_\-]+\.py)")


def command_blocks(document: str) -> List[str]:
    return COMMAND_BLOCK.findall((SKILL / document).read_text(encoding="utf-8"))


@pytest.mark.parametrize("document", DOCUMENTS)
def test_no_command_block_tells_a_reader_to_run_the_retired_product(document):
    offenders: List[Tuple[str, str]] = []
    for block in command_blocks(document):
        for entrypoint in RETIRED_ENTRYPOINTS:
            if entrypoint in block:
                offenders.append((entrypoint, block.strip().splitlines()[0]))
    assert not offenders, (
        f"{document} has runnable blocks invoking the retired product: {offenders}. "
        "Naming them in prose to say they are retired is fine; a command block is an instruction."
    )


@pytest.mark.parametrize("document", DOCUMENTS)
def test_every_script_a_command_block_names_exists_and_runs(document):
    named = {name for block in command_blocks(document) for name in SCRIPT_CALL.findall(block)}
    assert named, f"{document} shows no command that runs anything in scripts/"
    for name in sorted(named):
        path = SKILL / "scripts" / name
        assert path.exists(), f"{document} runs scripts/{name}, which does not exist"
        done = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True)
        assert done.returncode == 0, (
            f"{document} runs scripts/{name}, which has no working command line "
            f"(exit {done.returncode}): {done.stderr[-300:]}"
        )


@pytest.mark.parametrize("document", DOCUMENTS)
def test_the_pipeline_a_reader_would_follow_is_the_one_that_ships(document):
    named = {name for block in command_blocks(document) for name in SCRIPT_CALL.findall(block)}
    missing = [stage for stage in PIPELINE if stage not in named and document == "SKILL.md"]
    assert not missing, (
        f"SKILL.md does not show how to run {missing}. A stage with no shown command is a stage "
        "a reader reaches by reading the source, which is how the document and the product drift."
    )


def test_the_skill_description_does_not_promise_the_retired_runtime():
    """The frontmatter is what a catalogue shows before anyone opens the file."""
    head = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
    for promise in ["autonomous", "autopilot", "merge-broker", "governance kernel", "Phase 0–4"]:
        assert promise.lower() not in head.lower(), (
            f"the skill description still promises {promise!r}, which the pipeline does not do"
        )
    assert "genesis" in head.lower(), "the description does not say where the factory stops"


# --- the boundary the documents claim ---------------------------------------------------

PIPELINE_MODULES = ["plan", "apply", "publish", "result", "materialize", "render_ci",
                    "canonical", "github_port"]
RETIRED_MODULES = {"phase_gate", "phase-gate", "create_issues", "create-issues",
                   "verify_citations", "verify-citations", "install_governance",
                   "install-governance", "run_canary", "run-canary", "autopilot",
                   "merge_broker", "merge-broker"}


def _imports(module: str) -> set:
    import ast
    tree = ast.parse((SKILL / "scripts" / f"{module}.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("module", PIPELINE_MODULES)
def test_the_pipeline_does_not_reach_into_the_retired_product(module):
    """Both documents state this boundary. A statement in a document that nothing checks is a
    statement that stops being true without anyone finding out — which is how they came to
    describe a different product in the first place."""
    reached = _imports(module) & RETIRED_MODULES
    assert not reached, (
        f"scripts/{module}.py imports {sorted(reached)} from the retired product, and both "
        "SKILL.md and README.md tell a reader the pipeline does not."
    )
