"""#1240 (RF part): the genesis backlog follows the request's approved work plan.

A repository whose issues and milestones are not set up properly is a failure. The plan is
request `genesis.workUnits`: each unit (key, public title, public acceptance, milestone) becomes
exactly one issue linked to its unit's milestone, so several units never hide in one catch-all
issue. The compiler checks structure and linkage only; the blind review judges semantic adequacy.
"""
import copy
import importlib
import re

import pytest

from test_genesis_bundle import REQUEST, acp_check, compile_bundle, public_texts


# Public-safe synthetic stand-in for the live counterexample: a SIMPLE request whose eleven
# public requirements cover independent areas.
REQUIREMENTS = [
    "Discover candidate court rulings from public sources.",
    "Confirm each ruling's facts against the published judgment before use.",
    "Exclude rulings that match the documented exclusion list.",
    "Record the provenance of every fact shown in an episode.",
    "Gate every cue sheet on a complete, validated timing table.",
    "Lint every script for banned phrasing before rendering.",
    "Gate every rendered frame on the visual safe-area check.",
    "Gate every episode on the audio/video sync check.",
    "Publish approved episodes on the daily schedule.",
    "Feed published-episode metrics back into topic selection.",
    "Keep credentials out of every committed file and log.",
]
SUMMARY = "Produce short explainer episodes about public court rulings."
MILESTONES = [{"key": "sourcing", "title": "Sourcing", "description": "Find and confirm rulings."},
              {"key": "production", "title": "Production", "description": "Produce gated episodes."},
              {"key": "operation", "title": "Operation", "description": "Publish safely and learn."}]
# The author's approved plan groups related requirements into units with one owner each.
UNITS = [
    {"key": "ruling-intake", "title": "Ruling discovery and fact confirmation", "milestoneKey": "sourcing",
     "acceptance": "Candidate rulings are discovered, excluded per the list, fact-checked and carry provenance."},
    {"key": "render-gates", "title": "Cue-sheet, lint, visual and A/V gates", "milestoneKey": "production",
     "acceptance": "An episode renders only after its cue-sheet, lint, visual and A/V gates pass."},
    {"key": "publishing", "title": "Scheduled publishing and metrics loop", "milestoneKey": "operation",
     "acceptance": "Approved episodes publish on schedule and their metrics feed topic selection."},
    {"key": "secret-hygiene", "title": "Credential hygiene", "milestoneKey": "operation",
     "acceptance": "No credential appears in any committed file or log."},
]
MARKER = re.compile(r"\n\nWork unit ([1-9]\d*) of ([1-9]\d*): ([A-Za-z0-9][A-Za-z0-9._-]*)\Z")


def counterexample():
    request = copy.deepcopy(REQUEST)
    request["productIntent"] = {"summary": SUMMARY, "references": [],
                                "ownerInstructions": ["The owner described the channel in private."],
                                "publicRequirements": list(REQUIREMENTS)}
    return request


def planned(units=None, *, issues=None):
    request = counterexample()
    request["genesis"] = {
        "labels": [{"name": "enhancement", "color": "a2eeef", "description": "Product development"}],
        "milestones": copy.deepcopy(MILESTONES), "workUnits": copy.deepcopy(UNITS if units is None else units)}
    if issues is not None:
        request["genesis"]["issues"] = issues
    return request


def supplied_issues():
    """Issues written separately from the plan: own titles and extra detail, linked by `covers`."""
    return [{"key": f"issue-{unit['key']}", "title": f"Build {unit['title'].lower()}",
             "body": f"Context for the resident CTO.\n\nAcceptance: {unit['acceptance']}",
             "labels": ["enhancement"], "milestoneKey": unit["milestoneKey"], "covers": unit["key"]}
            for unit in UNITS]


def markers(plan):
    return [MARKER.search(issue["body"]).groups() for issue in plan["genesis"]["issues"]]


# --- the counterexample ---------------------------------------------------------------------

def test_several_independent_requirements_without_a_work_plan_never_compile_to_one_catch_all():
    try:
        plan = compile_bundle(counterexample())
    except ValueError as error:
        assert str(error).startswith("BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: no declared work plan")
        assert all(text not in str(error) for text in REQUIREMENTS)
    else:
        issues = plan["genesis"]["issues"]
        pytest.fail(f"compiled {len(issues)} issue(s) for an intent spanning {len(REQUIREMENTS)} independent "
                    f"requirements: {[issue['title'] for issue in issues]}; milestones "
                    f"{[m['title'] for m in plan['genesis']['milestones']]}")


def test_the_refusal_happens_before_any_materialization(monkeypatch):
    module = importlib.import_module("bootstrap")

    def must_not_materialize(*args, **kwargs):
        pytest.fail("an untraced multi-unit request reached materialization")
    monkeypatch.setattr(module, "compile_plan", must_not_materialize)
    with pytest.raises(ValueError, match="BACKLOG_DOES_NOT_TRACE_APPROVED_WORK"):
        compile_bundle(counterexample())


def test_a_multi_unit_plan_never_compiles_to_one_catch_all_issue():
    catch_all = dict(supplied_issues()[0], key="everything", title="Implement the owner's product intent",
                     body="\n".join(unit["acceptance"] for unit in UNITS))
    with pytest.raises(ValueError, match=r"BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: genesis\.workUnits\[1\] has no issue"):
        compile_bundle(planned(issues=[catch_all]))


def test_the_counterexample_compiles_one_issue_per_declared_unit_with_its_milestone():
    plan = compile_bundle(planned())
    issues = plan["genesis"]["issues"]
    assert [issue["title"] for issue in issues] == [unit["title"] for unit in UNITS]
    assert markers(plan) == [(str(i), str(len(UNITS)), unit["key"]) for i, unit in enumerate(UNITS, start=1)]
    assert [issue["milestoneKey"] for issue in issues] == [unit["milestoneKey"] for unit in UNITS]
    assert all(issue["body"].startswith(unit["acceptance"]) for issue, unit in zip(issues, UNITS))
    assert {m["key"] for m in plan["genesis"]["milestones"]} == {m["key"] for m in MILESTONES}
    # The pinned ACP genesis shape is strict: request-only workUnits/covers never reach the wire.
    assert "workUnits" not in plan["genesis"]
    assert all(set(issue) == {"key", "title", "body", "labels", "milestoneKey"} for issue in issues)
    checked = acp_check(plan)["normalization"]
    assert checked["allowed"] is True and checked["genesis"]["issues"] == issues


def test_supplied_issues_link_to_their_units_without_restating_requirements():
    plan = compile_bundle(planned(issues=supplied_issues()))
    issues = plan["genesis"]["issues"]
    assert [issue["key"] for issue in issues] == [f"issue-{unit['key']}" for unit in UNITS]
    assert markers(plan) == [(str(i), str(len(UNITS)), unit["key"]) for i, unit in enumerate(UNITS, start=1)]
    assert all(text not in issue["body"] for issue in issues for text in REQUIREMENTS)
    assert acp_check(plan)["normalization"]["allowed"] is True


def test_unit_labels_reach_the_derived_issue():
    units = copy.deepcopy(UNITS)
    units[1]["labels"] = ["gate"]
    request = planned(units)
    request["genesis"]["labels"].append({"name": "gate", "color": "d73a4a", "description": "Release gate"})
    assert compile_bundle(request)["genesis"]["issues"][1]["labels"] == ["gate"]


# --- single unit: no padding ------------------------------------------------------------------

def test_a_single_unit_plan_keeps_exactly_one_issue():
    request = planned([dict(UNITS[0], milestoneKey="sourcing")])
    plan = compile_bundle(request)
    assert len(plan["genesis"]["issues"]) == 1
    assert markers(plan) == [("1", "1", "ruling-intake")]


def test_related_requirements_may_be_one_declared_unit():
    # Structure only: the author groups work sharing one implementation and rollback responsibility.
    request = counterexample()
    request["productIntent"]["publicRequirements"] = [REQUIREMENTS[5], "Fail the build on any lint finding."]
    request["genesis"] = {"workUnits": [{"key": "script-lint", "title": "Script linting",
                                         "acceptance": "Scripts are linted and lint findings fail the build.",
                                         "milestoneKey": "first-product"}]}
    plan = compile_bundle(request)
    assert len(plan["genesis"]["issues"]) == 1 and len(plan["genesis"]["milestones"]) == 1
    assert markers(plan) == [("1", "1", "script-lint")]


def test_a_single_requirement_without_a_plan_keeps_its_one_default_issue():
    request = counterexample()
    request["productIntent"]["publicRequirements"] = [REQUIREMENTS[0]]
    plan = compile_bundle(request)
    issues, milestones = plan["genesis"]["issues"], plan["genesis"]["milestones"]
    assert len(issues) == 1 and len(milestones) == 1
    assert issues[0]["body"] == plan["summary"]  # No plan, no marker: the intent is the single unit.
    assert issues[0]["milestoneKey"] == milestones[0]["key"]
    assert acp_check(plan)["normalization"]["allowed"] is True


def test_a_summary_only_request_keeps_its_single_issue_byte_for_byte():
    plan = compile_bundle(copy.deepcopy(REQUEST))
    issues = plan["genesis"]["issues"]
    assert len(issues) == 1 and len(plan["genesis"]["milestones"]) == 1
    assert issues[0]["body"] == plan["summary"]
    assert issues[0]["milestoneKey"] == plan["genesis"]["milestones"][0]["key"]


# --- refusal witnesses: the marker is a link, not proof of content --------------------------

def test_a_marker_on_an_unrelated_body_is_refused():
    issues = supplied_issues()
    issues[1]["body"] = "Acceptance: the cue sheets look fine."
    with pytest.raises(ValueError, match=r"genesis\.issues\[1\]\.body does not state its work unit's declared acceptance"):
        compile_bundle(planned(issues=issues))


def test_a_missing_unit_is_refused():
    issues = supplied_issues()
    del issues[2]
    with pytest.raises(ValueError, match=r"BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: genesis\.workUnits\[2\] has no issue"):
        compile_bundle(planned(issues=issues))


@pytest.mark.parametrize("duplicate", ["second-issue", "marker-in-body"])
def test_a_duplicate_marker_is_refused(duplicate):
    issues = supplied_issues()
    if duplicate == "second-issue":
        issues.append(dict(issues[0], key="again"))
        index = len(issues) - 1
    else:
        issues[0]["body"] += "\n\nWork unit 3 of 4: publishing"
        index = 0
    with pytest.raises(ValueError, match=rf"genesis\.issues\[{index}\] duplicates a work unit marker"):
        compile_bundle(planned(issues=issues))


@pytest.mark.parametrize("source", ["supplied", "default"])
def test_without_a_plan_no_issue_may_carry_a_reserved_marker(source):
    # Review P2 on #64: markers are compiler-owned and appear only on issues linked to declared units.
    request = counterexample()
    request["productIntent"]["publicRequirements"] = ["Export the edited clip."]
    if source == "supplied":
        request["genesis"] = {"issues": [
            {"key": key, "title": f"Export {key}", "body": "Export the edited clip.\n\nWork unit 1 of 1: export",
             "labels": ["enhancement"], "milestoneKey": "first-product"} for key in ("export-a", "export-b")]}
    else:
        request["productIntent"]["summary"] = "Edit clips.\nWork unit 1 of 1: export"
    with pytest.raises(ValueError, match=r"BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: genesis\.issues\[0\]\.body "
                                         r"carries a reserved work unit marker"):
        compile_bundle(request)


def test_one_issue_cannot_cover_two_units():
    issues = supplied_issues()
    issues[0]["covers"] = ["ruling-intake", "render-gates"]
    with pytest.raises(ValueError, match=r"genesis\.issues\.0\.covers"):
        compile_bundle(planned(issues=issues))


def test_a_unit_naming_a_nonexistent_milestone_is_refused():
    units = copy.deepcopy(UNITS)
    units[3]["milestoneKey"] = "missing-stage"
    with pytest.raises(ValueError, match=r"genesis\.workUnits\[3\]\.milestoneKey names no declared milestone"):
        compile_bundle(planned(units))


@pytest.mark.parametrize("milestone_key", ["missing-stage", None])
def test_an_issue_naming_a_nonexistent_milestone_is_refused(milestone_key):
    issues = supplied_issues()
    issues[0]["milestoneKey"] = milestone_key
    with pytest.raises(ValueError, match="BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: .*milestone"):
        compile_bundle(planned(issues=issues))
    request = copy.deepcopy(REQUEST)  # Without a plan the existing backlog check refuses it.
    request["genesis"] = {"labels": [{"name": "enhancement", "color": "a2eeef", "description": "d"}],
                          "milestones": [{"key": "m", "title": "m", "description": "d"}],
                          "issues": [{"key": "i", "title": "i", "body": "b", "labels": ["enhancement"],
                                      "milestoneKey": milestone_key}]}
    with pytest.raises(ValueError, match="GENESIS_BACKLOG: .*milestone"):
        compile_bundle(request)


def test_a_wrong_milestone_link_is_refused():
    issues = supplied_issues()
    issues[0]["milestoneKey"] = "production"  # Exists, but ruling-intake belongs to sourcing.
    with pytest.raises(ValueError, match=r"genesis\.issues\[0\]\.milestoneKey is not its work unit's milestone"):
        compile_bundle(planned(issues=issues))


@pytest.mark.parametrize("covers", [None, "unknown-unit", "RULING-INTAKE"])
def test_an_issue_must_name_exactly_one_declared_unit(covers):
    issues = supplied_issues()
    if covers is None:
        del issues[0]["covers"]
    else:
        issues[0]["covers"] = covers
    with pytest.raises(ValueError, match=r"genesis\.issues\[0\]\.covers must name exactly one declared work unit") as caught:
        compile_bundle(planned(issues=issues))
    assert covers is None or covers not in str(caught.value)


def test_covers_without_a_declared_plan_is_refused():
    request = copy.deepcopy(REQUEST)
    request["genesis"] = {"issues": [dict(supplied_issues()[0], milestoneKey="first-product")]}
    with pytest.raises(ValueError, match="BACKLOG_DOES_NOT_TRACE_APPROVED_WORK: genesis.issues covers a work unit"):
        compile_bundle(request)


@pytest.mark.parametrize("key", ["ruling-intake", "bad key", "-leading"])
def test_unit_keys_are_unique_marker_safe_and_never_echoed(key):
    units = copy.deepcopy(UNITS)
    units[1]["key"] = key
    with pytest.raises(ValueError, match=r"genesis\.workUnits\[1\]\.key must be unique") as caught:
        compile_bundle(planned(units))
    assert key not in str(caught.value)


@pytest.mark.parametrize("field", ["title", "acceptance"])
def test_a_unit_needs_a_public_title_and_acceptance(field):
    units = copy.deepcopy(UNITS)
    units[0][field] = "   "
    with pytest.raises(ValueError, match=r"genesis\.workUnits\[0\] needs a public title and acceptance"):
        compile_bundle(planned(units))


# --- public safety of plan text and derived text --------------------------------------------

@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("field", ["title", "acceptance", "key"])
def test_private_identifiers_in_the_plan_are_refused_without_echo(private_field, field):
    token = "run_284fdfe39d4a405b9b763b10"
    request = planned()
    request[private_field] = [f"operation ID {token}"] if private_field == "reviewEvidence" else f"operation ID {token}"
    request["genesis"]["workUnits"][0][field] = token if field == "key" else f"Deliver {token} output."
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier at genesis.workUnits") as caught:
        compile_bundle(request)
    assert token not in str(caught.value)


def test_a_unit_acceptance_with_a_local_path_is_refused():
    request = planned()
    request["genesis"]["workUnits"][2]["acceptance"] = "Publish from /Users/private-owner/episodes."
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identity or runtime value"):
        compile_bundle(request)


@pytest.mark.parametrize("case,sink", [
    ("derived", r"genesis\.workUnits\[1\]\.acceptance"),
    ("supplied", r"genesis\.workUnits\[1\]\.acceptance"),
    ("acceptance-only", r"genesis\.workUnits\[1\]\.acceptance"),
    ("body-only", r"genesis\.issues\[1\]\.body"),
])
def test_a_whole_value_key_is_screened_before_the_marker_hides_it(case, sink):
    # Review P2 on #64: ACP refuses a body that is nothing but 64 hex characters; the appended
    # marker would hide it, so RF applies that whole-value rule to the original text first.
    key = "a1b2c3d4" * 8
    units = copy.deepcopy(UNITS)
    units[1]["acceptance"] = "a1b2c3d4" if case == "body-only" else key
    issues = None
    if case != "derived":
        issues = supplied_issues()
        issues[1]["body"] = f"Acceptance: {key}" if case == "acceptance-only" else key
    with pytest.raises(ValueError, match=rf"SECRET_SCREEN: credential-shaped content in {sink}") as caught:
        compile_bundle(planned(units, issues=issues))
    assert key not in str(caught.value) and "a1b2c3d4" not in str(caught.value)


def test_derived_marker_text_passes_through_the_public_screen(monkeypatch):
    # Falsifiable: if the generated marker carried a private value, the bundle screen refuses it.
    module = importlib.import_module("bootstrap")
    monkeypatch.setattr(module, "_trace_line", lambda position, count, key: "runtime: private-machine")
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identity or runtime value") as caught:
        compile_bundle(planned())
    assert "private-machine" not in str(caught.value)


def test_derived_backlog_is_public_only_and_invariant_under_private_fields():
    request = planned()
    baseline = compile_bundle(request)
    private = copy.deepcopy(request)
    private["reviewEvidence"] = ["Private witness abc1234 stays here."]
    private["stagePlan"] = "Stage one ends at activation; product acceptance follows in later runs."
    plan = compile_bundle(private)
    assert public_texts(plan) == public_texts(baseline)
    for text in public_texts(plan).values():
        assert request["seed"] not in text
        assert all(instruction not in text for instruction in request["productIntent"]["ownerInstructions"])
        assert "abc1234" not in text and "Stage one ends" not in text
