"""What the scripts import must be what the project declares it needs.

`jsonschema` was imported at module scope by `scripts/plan.py` and declared only under the
`test` extra, while the comment in `pyproject.toml` said the runtime had no third-party
dependencies at all. Every test passed, because the tests install the test extra. The failure
lands only on a host that installed the project and not its tests:

    $ python3 scripts/plan.py --request ... --verification ...
    ModuleNotFoundError: No module named 'jsonschema'

Nothing compared the two lists, so the declaration and the imports drifted in silence. This
compares them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Set

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# The stdlib modules these scripts are allowed to reach for. Kept explicit rather than derived
# from `sys.stdlib_module_names`, which does not exist on the 3.9 this project still supports —
# a check that only runs on the newer interpreter is a check that is absent where it is needed.
STDLIB = {
    "__future__", "argparse", "ast", "base64", "collections", "contextlib", "copy", "dataclasses",
    "datetime", "enum", "functools", "hashlib", "importlib", "io", "itertools", "json", "os",
    "pathlib", "py_compile", "re", "shutil", "string", "subprocess", "sys", "tempfile", "textwrap",
    "time", "types", "typing", "unittest", "urllib", "uuid", "warnings", "xml",
}


def _declared_runtime_dependencies() -> Set[str]:
    """The distribution names in `[project] dependencies`, lowercased, version stripped."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^dependencies = (\[.*?\])", text, re.MULTILINE | re.DOTALL)
    assert match, "pyproject.toml has no [project] dependencies array to compare against"
    declared = ast.literal_eval(match.group(1))
    return {re.split(r"[<>=!~\[ ]", entry, 1)[0].strip().lower().replace("-", "_") for entry in declared}


def _first_party() -> Set[str]:
    """Anything shipped in this repository. A sibling script or a top-level package is not
    something pip installs, so it does not belong in the dependency comparison."""
    return {path.stem for path in SCRIPTS.glob("*.py")} | {
        entry.name for entry in ROOT.iterdir() if entry.is_dir() and not entry.name.startswith(".")
    }


def _module_scope_imports(path: Path) -> Set[str]:
    """Top-level imports only. An import inside a function is a runtime dependency of that
    call, not of loading the module, and this check is about what `python scripts/x.py` needs
    before it can do anything at all."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


SCRIPT_PATHS = sorted(SCRIPTS.glob("*.py"))


@pytest.mark.parametrize("script", SCRIPT_PATHS, ids=lambda p: p.name)
def test_every_module_scope_third_party_import_is_a_declared_dependency(script: Path):
    declared = _declared_runtime_dependencies()
    first_party = _first_party()
    undeclared = sorted(
        module
        for module in _module_scope_imports(script)
        if module not in STDLIB and module not in first_party and module.lower() not in declared
    )
    assert not undeclared, (
        f"{script.name} imports {undeclared} at module scope, and pyproject.toml does not declare "
        f"them as runtime dependencies. Installing this project without its test extra and running "
        f"the script raises ModuleNotFoundError."
    )


def test_the_declaration_does_not_name_something_no_script_imports():
    """The other direction. A dependency nothing imports is an install nobody needs, and it is
    also how a list stops describing the code it claims to describe."""
    imported = set()
    for script in SCRIPT_PATHS:
        imported.update(_module_scope_imports(script))
    unused = sorted(dep for dep in _declared_runtime_dependencies() if dep not in {m.lower() for m in imported})
    assert not unused, f"pyproject.toml declares {unused} as runtime dependencies but no script imports them"


def test_the_scripts_directory_is_not_empty():
    """Without this the parametrised check above would pass by having nothing to check."""
    assert len(SCRIPT_PATHS) >= 3, f"expected the compiler scripts, found {[p.name for p in SCRIPT_PATHS]}"
