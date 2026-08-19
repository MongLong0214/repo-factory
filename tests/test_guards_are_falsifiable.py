"""Every guard here is broken on purpose, and a named test must notice.

Four times in one day a test passed that could not have failed. The shapes differed — a guard
validated by an external validator instead of by the code under test, a durability property
checked on the success path, a decision asserted against a hand-built input rather than the
compiler's output — but the structure was identical: **the test supplied the thing it was
supposed to observe.**

Each time, mutation found it and I found it by remembering to mutate. Memory is what failed
four times, so this stops being something to remember. A guard added without a test that dies
when the guard is removed now fails CI, which is the only version of this rule that holds.

The table is the point. Adding a guard means adding a row; a row with no killing test is a
failure, not an omission.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

SKILL = Path(__file__).resolve().parent.parent
# The published documents are copied too. `SKILL.md` decides what the skill actually executes,
# so it is part of the guarded surface — a row against a file the copy does not carry would
# apply its mutation to nothing.
COPIED = ("scripts", "tests", "schemas", "profiles", "governance", "templates",
          "pyproject.toml", "SKILL.md", "README.md")

# name, file, (find, replace), tests that must fail once the guard is gone.
GUARDS: List[Dict[str, object]] = [
    {
        "name": "the compiler validates its own output against the schema it ships",
        "file": "scripts/plan.py",
        "mutate": ("    invalid = sorted(Draft202012Validator(_plan_schema()).iter_errors(core), key=str)",
                   "    invalid = []"),
        "killed_by": ["tests/test_slice1_plan.py::test_the_compiler_refuses_a_plan_that_does_not_satisfy_its_own_schema"],
    },
    {
        "name": "the operation id must be supplied, not invented per call",
        "file": "scripts/plan.py",
        "mutate": ('    if not operation_id or not operation_id.strip():',
                   '    if False:'),
        "killed_by": ["tests/test_slice1_plan.py::test_the_operation_id_must_be_supplied_rather_than_invented"],
    },
    {
        "name": "the request's declared stack is used",
        "file": "scripts/plan.py",
        "mutate": ("    if declared:", "    if False:"),
        "killed_by": ["tests/test_slice1_plan.py::test_the_request_stack_is_used_and_a_disagreement_is_refused"],
    },
    {
        "name": "the remote owner comes from the request",
        "file": "scripts/plan.py",
        "mutate": ("    return str(owner) if owner else DEFAULT_REMOTE_OWNER",
                   "    return DEFAULT_REMOTE_OWNER"),
        "killed_by": ["tests/test_slice1_plan.py::test_the_remote_owner_comes_from_the_request"],
    },
    {
        "name": "the environment snapshot id counts facts, not the moment they were read",
        "file": "scripts/plan.py",
        "mutate": ('    return digest(observation, volatile="strip")',
                   '    return digest(observation, volatile="allow")'),
        "killed_by": ["tests/test_slice1_plan.py::test_observing_the_same_facts_twice_gives_the_same_snapshot_id"],
    },
    {
        "name": "the ruleset is planned after the files exist",
        "file": "scripts/plan.py",
        "mutate": ('"phase": "after-files",\n             "desiredState": ruleset_desired_state()}',
                   '"phase": "before-files",\n             "desiredState": ruleset_desired_state()}'),
        "killed_by": ["tests/test_slice1_plan.py::test_the_ruleset_is_planned_after_the_files_and_the_repository_before_them"],
    },
    {
        "name": "a same-named resource with no receipt is a collision, not a resume",
        "file": "scripts/apply.py",
        "mutate": ('        if intent == "create" and observed is not None:', "        if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_an_unrelated_resource_of_the_same_name_is_a_collision_and_changes_nothing"],
    },
    {
        "name": "a resumed operation re-observes the resource",
        "file": "scripts/apply.py",
        "mutate": ("            if still_there is None:", "            if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_receipt_for_a_resource_that_no_longer_exists_is_not_a_resume"],
    },
    {
        "name": "the receipt states when each observation happened",
        "file": "scripts/apply.py",
        "mutate": ("created_at=created_at, reread_at=now())", "created_at=created_at, reread_at=created_at)"),
        "killed_by": ["tests/test_slice3_apply.py::test_the_receipt_states_when_each_observation_happened"],
    },
    {
        "name": "only the named phase is applied",
        "file": "scripts/apply.py",
        "mutate": ('    staged = [op for op in plan["githubOperations"] if op.get("phase", "before-files") == phase]',
                   '    staged = list(plan["githubOperations"])'),
        "killed_by": ["tests/test_slice3_apply.py::test_only_the_named_phase_is_applied"],
    },
    {
        "name": "the publish target must be empty",
        "file": "scripts/publish.py",
        "mutate": ("    if workdir.exists() and any(workdir.iterdir()):", "    if False:"),
        "killed_by": ["tests/test_publish.py::test_a_non_empty_target_is_refused_before_anything_is_written"],
    },
    {
        "name": "the committed set is compared to the planned set before the push",
        "file": "scripts/publish.py",
        "mutate": ("    if committed != planned:", "    if False:"),
        "killed_by": ["tests/test_publish.py::test_an_unplanned_file_stops_the_push_rather_than_being_reported_after_it"],
    },
    {
        "name": "a 404 is an answer and a transport failure is not",
        "file": "scripts/github_port.py",
        "mutate": ('        if "404" in err or "Not Found" in err:\n            return None\n', ""),
        "killed_by": ["tests/test_github_port.py::test_a_missing_repository_reads_as_absent_not_as_an_error"],
    },
    {
        "name": "observation keeps only fields that do not drift",
        "file": "scripts/github_port.py",
        "mutate": ('            return {\n                "identity": identity,\n                "resourceType": "repository",',
                   '            return {  # mutated\n                **observed,\n                "identity": identity,\n                "resourceType": "repository",'),
        "killed_by": ["tests/test_github_port.py::test_observation_keeps_only_fields_that_do_not_drift"],
    },
    {
        "name": "a rate limit is not read as absence",
        "file": "scripts/github_port.py",
        "mutate": ('        if "rate limit" in err.lower() or "secondary rate" in err.lower():',
                   "        if False:"),
        "killed_by": ["tests/test_github_port.py::test_a_rate_limit_is_told_apart_from_a_broken_call"],
    },
    {
        "name": "an unknown stack is refused rather than defaulted",
        "file": "scripts/render_ci.py",
        "mutate": ("    if not path.is_file():", "    if False:"),
        "killed_by": ["tests/test_slice2_stack_ci.py::test_an_unknown_stack_is_refused_and_not_defaulted_to_node"],
    },
    {
        "name": "an unverified receipt is refused at assembly",
        "file": "scripts/result.py",
        "mutate": ("    if unverified:", "    if False:"),
        "killed_by": ["tests/test_slice4_result.py::test_an_unverified_receipt_is_refused_at_assembly_not_at_handoff"],
    },
    {
        "name": "the compiler validates the request it is given, not only the plan it produces",
        "file": "scripts/plan.py",
        "mutate": ("    validate_request(request)\n    profile = load_profile", "    profile = load_profile"),
        "killed_by": ["tests/test_request_contract.py"],
    },
    {
        "name": "a third-party import at module scope has to be a declared runtime dependency",
        "file": "pyproject.toml",
        "mutate": ('dependencies = [\n  "jsonschema==4.23.0",\n]', "dependencies = []"),
        "killed_by": ["tests/test_declared_dependencies.py"],
    },
    {
        "name": "the effect that gets created is the one inside the approved operation",
        "file": "scripts/apply.py",
        "mutate": ('port.create(operation["resourceType"], operation["resourceIdentity"], operation["desiredState"])',
                   'port.create(operation["resourceType"], operation["resourceIdentity"], {})'),
        "killed_by": ["tests/test_slice3_apply.py"],
    },
    {
        "name": "the approved exposure and the exposure that will be created must agree",
        "file": "scripts/apply.py",
        "mutate": ('if operation.get("desiredState", {}).get("private") != approved_private:',
                   "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_an_operation_that_would_create_something_other_than_what_was_approved_is_refused"],
    },
    {
        "name": "the post-write re-read compares the approved state, not only existence",
        "file": "scripts/apply.py",
        "mutate": ('gaps = _state_gap(operation["desiredState"], after)', "gaps = []"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_resource_created_in_a_state_the_plan_did_not_approve_is_refused"],
    },
    {
        "name": "the ruleset body is in the plan, not only its name",
        "file": "scripts/plan.py",
        "mutate": ('"desiredState": ruleset_desired_state()}', '"desiredState": {}}'),
        "killed_by": ["tests/test_slice1_plan.py::test_the_ruleset_body_is_in_the_plan_and_not_only_its_name"],
    },
    {
        "name": "no command block tells a reader to run the retired product",
        "file": "SKILL.md",
        "mutate": ("python3 scripts/result.py --input result-input.json",
                   "python3 scripts/phase-gate.py 4 --tier L"),
        "killed_by": ["tests/test_published_surface.py"],
    },
    {
        "name": "every pipeline stage is runnable from the command line",
        "file": "scripts/apply.py",
        "mutate": ('if __name__ == "__main__":\n    raise SystemExit(main())',
                   "# the entrypoint used to live here"),
        "killed_by": ["tests/test_pipeline_cli.py"],
    },
    {
        "name": "every planned operation must have a receipt before a result is assembled",
        "file": "scripts/result.py",
        "mutate": ("    missing = sorted(planned - delivered)", "    missing = []"),
        "killed_by": ["tests/test_slice4_result.py::test_a_plan_operation_with_no_receipt_refuses_the_result"],
    },
    {
        "name": "a result may not carry unresolved gaps",
        "file": "scripts/result.py",
        "mutate": ("    if unresolved_gaps:\n        raise ResultError(", "    if False:\n        raise ResultError("),
        "killed_by": ["tests/test_slice4_result.py::test_a_result_may_not_carry_unresolved_gaps"],
    },
    {
        "name": "the committed bytes are the planned bytes, not only the planned paths",
        "file": "scripts/publish.py",
        "mutate": ("        if landed != planned_digests[path]:", "        if False:"),
        "killed_by": ["tests/test_publish.py::test_content_rewritten_between_the_plan_and_the_commit_is_refused"],
    },
    {
        "name": "the push destination is the repository the plan approved",
        "file": "scripts/publish.py",
        "mutate": ("    if observed_identity != repository_identity:", "    if False:"),
        "killed_by": ["tests/test_publish.py::test_a_remote_that_is_not_the_planned_repository_is_refused"],
    },
    {
        "name": "the remote is re-read after the push rather than trusted to have moved",
        "file": "scripts/publish.py",
        "mutate": ("    if disagreeing:", "    if False:"),
        "killed_by": ["tests/test_publish.py::test_a_push_that_did_not_move_the_remote_is_refused"],
    },
    {
        "name": "an unknown phase is refused rather than reported complete",
        "file": "scripts/apply.py",
        "mutate": ("    if phase not in PHASES:", "    if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_phase_the_plan_does_not_have_is_refused_rather_than_reported_complete"],
    },
    {
        "name": "the later phase waits for the earlier one to finish",
        "file": "scripts/apply.py",
        "mutate": ("        if unfinished:", "        if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_the_later_phase_refuses_while_the_earlier_one_is_unfinished"],
    },
    {
        "name": "the later phase waits for the genesis commit to be published",
        "file": "scripts/apply.py",
        "mutate": ("        if unpublished:", "        if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_the_later_phase_refuses_until_the_genesis_commit_is_published"],
    },
    {
        "name": "an update is refused when the resource it would change is absent",
        "file": "scripts/apply.py",
        "mutate": ('if intent == "update" and observed is None:', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_an_update_against_something_absent_is_refused_rather_than_creating_it"],
    },
    {
        "name": "an intent this applier does not perform is refused",
        "file": "scripts/apply.py",
        "mutate": ('if intent not in ("create", "update"):', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_an_intent_this_applier_does_not_perform_is_refused"],
    },
    {
        "name": "the reported default branch is the one the plan set and the remote confirmed",
        "file": "scripts/result.py",
        "mutate": ('if repository["defaultBranch"] != approved:', "if False:"),
        "killed_by": ["tests/test_slice4_result.py::test_a_default_branch_the_plan_never_set_refuses_the_result"],
    },
    {
        "name": "the default branch is an approved operation, not a caller-supplied fact",
        "file": "scripts/plan.py",
        "mutate": ('"phase": "after-files", "desiredState": {"defaultBranch": DEFAULT_BRANCH}}',
                   '"phase": "after-files", "desiredState": {"defaultBranch": "main"}}'),
        "killed_by": ["tests/test_slice4_result.py::test_a_default_branch_the_plan_never_set_refuses_the_result",
                      "tests/test_slice1_plan.py"],
    },
    {
        "name": "two ledger rows for one operation are refused rather than last-one-wins",
        "file": "scripts/apply.py",
        "mutate": ('if row.get("operationId") in self._rows:', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_two_rows_for_one_operation_are_refused_rather_than_last_one_winning"],
    },
    {
        "name": "a ledger row that does not say what it verified is refused",
        "file": "scripts/apply.py",
        "mutate": ("                if missing:", "                if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_row_that_never_says_what_it_verified_is_refused"],
    },
    {
        "name": "a resume checks the resource and the operation, not only the approval",
        "file": "scripts/apply.py",
        "mutate": ("            if mismatched:", "            if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_receipt_that_names_a_different_resource_is_not_a_resume"],
    },
    {
        "name": "a resumed resource must be in the state its receipt recorded",
        "file": "scripts/apply.py",
        "mutate": ('if digest(still_there, volatile="allow") != prior["afterStateDigest"]:', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_resource_that_drifted_since_its_receipt_is_refused"],
    },
    {
        "name": "the ledger is written 0600",
        "file": "scripts/apply.py",
        "mutate": ("os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600", "os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644"),
        "killed_by": ["tests/test_slice3_apply.py::test_the_ledger_is_not_world_readable"],
    },
    {
        "name": "a plan without an approval receipt is refused before the remote is read",
        "file": "scripts/apply.py",
        "mutate": ("    _check_authorization(plan, authorization, ledger)", "    pass"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_plan_with_no_approval_receipt_is_refused_before_the_remote_is_read"],
    },
    {
        "name": "an approval covers one exact plan digest",
        "file": "scripts/apply.py",
        "mutate": ('if receipt.get("planDigest") != stated:', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_an_approval_for_a_different_plan_is_refused"],
    },
    {
        "name": "a lesser authority cannot cover a plan that needs a greater one",
        "file": "scripts/apply.py",
        "mutate": ('if held < AUTHORITY_RANK.get(required, 1):', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_hermes_approval_cannot_cover_a_plan_that_needs_the_owner"],
    },
    {
        "name": "a revoked or superseded approval is spent",
        "file": "scripts/apply.py",
        "mutate": ('if receipt.get("revoked") or receipt.get("supersededBy"):', "if False:"),
        "killed_by": ["tests/test_slice3_apply.py::test_a_revoked_or_superseded_approval_is_refused"],
    },
]


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    """A copy, because a mutation that escapes into the working tree is worse than no check."""
    root = tmp_path_factory.mktemp("falsifiable")
    for name in COPIED:
        source = SKILL / name
        if source.is_dir():
            shutil.copytree(source, root / name)
        elif source.is_file():
            shutil.copy2(source, root / name)
    return root


@pytest.mark.parametrize("guard", GUARDS, ids=lambda g: g["file"].split("/")[-1] + ": " + g["name"][:44])
def test_removing_the_guard_kills_a_named_test(guard: Dict[str, object], tree: Path) -> None:
    target = tree / str(guard["file"])
    original = target.read_text(encoding="utf-8")
    find, replace = guard["mutate"]  # type: ignore[misc]

    assert find in original, (
        f"the mutation for {guard['name']!r} no longer matches {guard['file']}. The guard moved or "
        "was reworded; update the row rather than deleting it — a row that cannot be applied is a "
        "guard nobody is checking."
    )
    target.write_text(original.replace(find, replace, 1), encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", *guard["killed_by"]],  # type: ignore[misc]
            cwd=str(tree), capture_output=True, text=True, timeout=600,
        )
    finally:
        target.write_text(original, encoding="utf-8")

    assert done.returncode != 0, (
        f"{guard['name']!r} was removed and {guard['killed_by']} still passed.\n"
        "The test does not hold its own subject: it would pass whether or not the guard exists.\n"
        f"{done.stdout[-1200:]}"
    )


def test_every_guarded_file_appears_in_the_table():
    # A file full of refusals with no row is the state this whole file exists to end. The list is
    # explicit rather than derived, so adding a guarded module is a decision someone makes.
    #
    # Rows may also name a file outside this list — `pyproject.toml` declares the runtime
    # dependency set, and the mutation that proves that guard has to edit the declaration. What
    # is not allowed is a row naming a path that does not exist, which is how a mutation quietly
    # stops applying to anything.
    guarded = {"scripts/plan.py", "scripts/apply.py", "scripts/publish.py",
               "scripts/github_port.py", "scripts/render_ci.py", "scripts/result.py"}
    covered = {str(g["file"]) for g in GUARDS}

    missing = sorted(guarded - covered)
    assert not missing, f"guarded files with no falsifiability row: {missing}"

    absent = sorted(path for path in covered if not (SKILL / path).exists())
    assert not absent, (
        f"falsifiability rows name paths that do not exist: {absent}. "
        "A row against a missing file applies its mutation to nothing."
    )
