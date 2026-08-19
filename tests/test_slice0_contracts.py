"""Slice 0 — the contracts Repo Factory speaks, and the ones it deliberately does not restate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SKILL = Path(__file__).resolve().parent.parent
SCHEMAS = SKILL / "schemas"
PROFILES = SKILL / "profiles"

# PRD §2.2. Each of these has one canonical implementation in the control plane; a schema file
# here would be the second, and two statements of one contract agree only until one changes.
CONTROL_PLANE_OWNED = [
    "project-manifest",
    "repo-factory-result",
    "external-write-receipt",
    "candidate-snapshot",
    "verification-command",
    "github-gate-evidence",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["bootstrap-profile", "bootstrap-request", "bootstrap-plan"])
def test_schema_is_a_valid_draft_2020_12_schema(name: str) -> None:
    Draft202012Validator.check_schema(load(SCHEMAS / f"{name}.schema.json"))


@pytest.mark.parametrize("profile", ["simple", "standard", "guarded"])
def test_profile_validates_against_the_profile_schema(profile: str) -> None:
    validator = Draft202012Validator(load(SCHEMAS / "bootstrap-profile.schema.json"))
    errors = sorted(validator.iter_errors(load(PROFILES / f"{profile}.json")), key=str)
    assert not errors, "\n".join(str(e) for e in errors)


def test_no_schema_here_restates_a_control_plane_contract() -> None:
    # The check is on filenames rather than content because that is where the mistake happens:
    # someone reads PRD §2.2's list, sees a name with no file, and writes the file.
    present = {p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.schema.json")}
    duplicated = present.intersection(CONTROL_PLANE_OWNED)
    assert not duplicated, (
        f"these contracts are implemented in the control plane and must not be restated here: "
        f"{sorted(duplicated)} — see schemas/README.md"
    )


def test_readme_records_where_each_shared_contract_actually_lives() -> None:
    # Without this the table rots into a list of names, which is the state that made
    # GitHubGateEvidence look absent in the first place.
    readme = (SCHEMAS / "README.md").read_text(encoding="utf-8")
    for contract in ["ProjectManifest", "RepoFactoryResult", "ExternalWriteReceipt",
                     "CandidateSnapshot", "VerificationCommand", "GitHubGateEvidence"]:
        assert contract in readme, f"{contract} has no recorded canonical location"
    assert "GatePayload" in readme, "the GitHubGateEvidence alias is the one that gets redefined"


def test_a_profile_cannot_require_an_artifact_it_also_calls_optional() -> None:
    for profile in ["simple", "standard", "guarded"]:
        data = load(PROFILES / f"{profile}.json")
        overlap = set(data["required"]).intersection(data["optional"])
        assert not overlap, f"{profile}: {sorted(overlap)} is both required and optional"


def test_every_profile_requires_the_artifacts_no_profile_may_omit() -> None:
    # PRD §6.1 lists these as required at the *smallest* profile, so a larger one dropping any of
    # them would be a reduction disguised as a profile choice.
    floor = {
        "portable-project-manifest",
        "readme",
        "agents",
        "stack-specific-ci",
        "verification-commands",
        "branch-contract",
        "repo-factory-result",
    }
    for profile in ["simple", "standard", "guarded"]:
        missing = floor.difference(load(PROFILES / f"{profile}.json")["required"])
        assert not missing, f"{profile} drops {sorted(missing)}"


def test_commitlore_failure_handling_hardens_with_the_profile() -> None:
    # PRD §18: preferred/WARN, required/REVISE, required/BLOCK. A profile that keeps the default
    # but softens the failure would read as configured while behaving as optional.
    expected = {
        "simple": ("preferred", "WARN"),
        "standard": ("required", "REVISE"),
        "guarded": ("required", "BLOCK"),
    }
    for profile, (default, on_failure) in expected.items():
        commitlore = load(PROFILES / f"{profile}.json")["commitlore"]
        assert (commitlore["default"], commitlore["onFailure"]) == (default, on_failure)
