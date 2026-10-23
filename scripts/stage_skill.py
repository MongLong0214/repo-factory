#!/usr/bin/env python3
"""Prepare the canonical skill and runtime dependencies inside this worktree only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("bootstrap.py", "acp_bridge.mts", "plan.py", "canonical.py", "materialize.py",
           "render_ci.py", "secret_screen.py", "stage_skill.py")


def stage_skill(destination=None):
    destination = Path(destination) if destination is not None else ROOT / ".installed-skill-staging"
    if destination.is_symlink():
        raise ValueError("staging destination must not be a symlink")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    for name in ("SKILL.md", "README.md", "LICENSE", "pyproject.toml"):
        shutil.copy2(ROOT / name, destination / name)
    for directory in ("profiles", "templates/ci", "contracts"):
        shutil.copytree(ROOT / directory, destination / directory)
    (destination / "schemas").mkdir()
    for name in ("bootstrap-request.schema.json", "bootstrap-plan.schema.json", "bootstrap-profile.schema.json", "lean-review.schema.json"):
        shutil.copy2(ROOT / "schemas" / name, destination / "schemas" / name)
    (destination / "scripts").mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / name, destination / "scripts" / name)
    source = {
        "repositoryHead": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "files": {str(path.relative_to(destination)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(destination.rglob("*")) if path.is_file()},
    }
    (destination / "SOURCE.json").write_text(json.dumps(source, indent=2, sort_keys=True) + "\n")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(stage_skill())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
