"""RF60-01/02: public approval is explicit; private instructions are never a text source."""
import copy
import json

import pytest

from test_genesis_bundle import REQUEST, acp_check, compile_bundle, public_texts


FEATURES = ["Create a repository comparison report after each scan.",
            "Public repository READMEs must be checked for broken links.",
            "Check private repositories as well as public ones.",
            "Keep a per-repository scan history."]
PUBLIC_FIELDS = ["summary", "publicRequirements", "reference", "commit", "issue", "issue_title",
                 "milestone", "milestone_title", "label", "label_name"]


def request_for():
    request = copy.deepcopy(REQUEST)
    request["bootstrapProfile"] = "STANDARD"
    request["seed"] = "The current actor is private-owner. Build a README checker."
    request["productIntent"] = {
        "summary": "Compare README claims against source files.", "references": [],
        "ownerInstructions": ["The current actor is private-owner.",
                              "The launch memo carries violet lantern signals."]}
    return request


def put_public(request, field, value):
    intent = request["productIntent"]
    if field == "summary":
        intent["summary"] = value
    elif field == "publicRequirements":
        intent["publicRequirements"] = [value]
    elif field == "reference":
        intent["references"] = [value]
    else:
        request["genesis"] = {
            "commitMessage": value if field == "commit" else "chore: initialize checker",
            "labels": [{"name": value if field == "label_name" else "enhancement", "color": "a2eeef",
                        "description": value if field == "label" else "README checking"}],
            "milestones": [{"key": "m", "title": value if field == "milestone_title" else "First increment",
                            "description": value if field == "milestone" else "Deliver README checking."}],
            "issues": [{"key": "i", "title": value if field == "issue_title" else "Implement README checking",
                        "body": value if field == "issue" else "Deliver README checking.",
                        "labels": [value if field == "label_name" else "enhancement"], "milestoneKey": "m"}]}


@pytest.mark.parametrize("sink", ["README.md", "docs/PRD.md", "issues", "milestones", "labels", "commitMessage"])
def test_reviewer_owner_instruction_fragment_is_private_in_every_sink(sink):
    request = request_for()
    plan = compile_bundle(request)
    text = public_texts(plan)[sink]
    assert "private-owner" not in text
    if sink in ("README.md", "docs/PRD.md", "issues", "milestones"):
        assert request["productIntent"]["summary"] in text
    assert plan["genesis"]["productIntent"]["ownerInstructions"] == [request["seed"], *request["productIntent"]["ownerInstructions"]]


@pytest.mark.parametrize("field", PUBLIC_FIELDS)
@pytest.mark.parametrize("fragment", ["The current actor is private-owner.",
                                      "the CURRENT \n actor IS private-owner",
                                      "current actor is private-owner",
                                      "launch MEMO carries\tVIOLET lantern signals"])
def test_private_sentence_and_subsentence_fragments_are_refused_in_each_public_field(field, fragment):
    request = request_for()
    put_public(request, field, fragment)
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private fragment") as caught:
        compile_bundle(request)
    assert fragment not in str(caught.value) and "private-owner" not in str(caught.value)


@pytest.mark.parametrize("field", PUBLIC_FIELDS)
@pytest.mark.parametrize("condition", ["Create a public repository named ReadmeTool. Visibility: public.",
                                       "Public repository only.", "The repository must be private.",
                                       "Repository name: ReadmeTool.", "Create a repository.",
                                       "Create\na public repository named ReadmeTool.", "Public\nrepository only.",
                                       "Public repository READMEs must be checked for broken links."])
def test_explicit_approved_prose_is_never_classified_as_a_creation_condition(field, condition):
    request = request_for()
    put_public(request, field, condition)
    plan = compile_bundle(request)
    bundle = plan["genesis"]
    if field in ("summary", "publicRequirements", "reference"):
        assert condition in public_texts(plan)["README.md"]
        assert condition in public_texts(plan)["docs/PRD.md"]
    else:
        published = {"commit": bundle["commitMessage"], "issue": bundle["issues"][0]["body"],
                     "issue_title": bundle["issues"][0]["title"],
                     "milestone": bundle["milestones"][0]["description"],
                     "milestone_title": bundle["milestones"][0]["title"],
                     "label": bundle["labels"][0]["description"], "label_name": bundle["labels"][0]["name"]}
        assert published[field] == condition


@pytest.mark.parametrize("field", PUBLIC_FIELDS)
@pytest.mark.parametrize("value", ["The current actor is another-private-owner.", "Identity is resident-123.",
                                   "/Users/private-owner/project", "run_284fdfe39d4a405b9b763b10",
                                   "model: gpt-6", "runtime: private-machine",
                                   "identity: private-owner", "runId: resident-123",
                                   '\"identity\": \"private-owner\"', '\"runId\": \"resident-123\"'])
def test_identity_and_machine_values_are_refused_even_without_a_private_overlap(field, value):
    request = request_for()
    request["seed"] = "The launch memo stays within the private handoff."
    request["productIntent"]["ownerInstructions"] = ["A separate note remains undisclosed here."]
    put_public(request, field, value)
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private identity or runtime value") as caught:
        compile_bundle(request)
    assert value not in str(caught.value)


def test_every_explicit_feature_survives_verbatim_and_private_handoff_is_preserved():
    request = request_for()
    request["productIntent"]["summary"] = "  " + request["productIntent"]["summary"] + "  "
    request["productIntent"]["publicRequirements"] = [*FEATURES, "  Preserve mixed Unicode output: 눈사람 雪.  "]
    before = copy.deepcopy(request)
    plan = compile_bundle(request)
    for sink in ("README.md", "docs/PRD.md", "issues", "milestones"):
        for feature in [request["productIntent"]["summary"], *request["productIntent"]["publicRequirements"]]:
            assert feature in public_texts(plan)[sink], (sink, feature)
    assert request == before
    decision = json.loads(acp_check(plan)["outputs"]["value"]["handoff"]["recentDecisions"][0])
    assert decision["productIntent"]["summary"] == request["productIntent"]["summary"]
    assert decision["productIntent"]["ownerInstructions"] == [request["seed"], *request["productIntent"]["ownerInstructions"]]
    assert "publicRequirements" not in plan["genesis"]["productIntent"]  # Exact pinned ACP wire shape.


@pytest.mark.parametrize("feature", FEATURES + ["Create a repository\ncomparison report after each scan.",
                                                "Public repository\nREADMEs must be checked for broken links."])
def test_product_behaviour_is_also_allowed_as_the_public_summary(feature):
    request = request_for()
    request["productIntent"]["summary"] = feature
    plan = compile_bundle(request)
    assert feature in public_texts(plan)["README.md"]
    assert feature in public_texts(plan)["docs/PRD.md"]


def test_owner_instructions_remain_private_even_when_they_look_like_product_requirements():
    request = request_for()
    request["productIntent"]["ownerInstructions"] += FEATURES
    plan = compile_bundle(request)
    for text in public_texts(plan).values():
        assert all(feature not in text for feature in FEATURES)
    assert plan["genesis"]["productIntent"]["ownerInstructions"] == [request["seed"], *request["productIntent"]["ownerInstructions"]]


@pytest.mark.parametrize("public,refused", [("violet lantern signal", False),
                                          ("violet\nLANTERN signal remains", True),
                                          ("비공개\t대화 표식 은", True)])
def test_private_fragment_threshold_and_unicode_whitespace(public, refused):
    request = request_for()
    request["productIntent"]["ownerInstructions"] = ["violet lantern signal remains hidden.", "비공개 대화 표식 은 여기에 있다."]
    request["productIntent"]["summary"] = public
    if refused:
        with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private fragment"):
            compile_bundle(request)
    else:
        assert public in public_texts(compile_bundle(request))["README.md"]


def test_freshreadme_shape_uses_four_public_requirements_and_private_instructions():
    request = request_for()
    request["repositories"][0]["name"] = "FreshReadme"
    request["projectId"] = "freshreadme-2"
    request["productIntent"]["publicRequirements"] = FEATURES
    request["productIntent"]["references"] = ["https://github.com/ShreyasDasari/evergif"]
    plan = compile_bundle(request)
    assert plan["projectManifest"]["projectId"] == "freshreadme-2"
    assert all(feature in public_texts(plan)["docs/PRD.md"] for feature in FEATURES)
    assert plan["genesis"]["productIntent"]["references"] == request["productIntent"]["references"]


def test_all_output_summaries_are_public_and_private_seed_reaches_only_private_handoff():
    request = request_for()
    request["seed"] += " Reference: https://private.example/original"
    plan = compile_bundle(request)
    intent = plan["genesis"]["productIntent"]
    assert intent["summary"] == request["productIntent"]["summary"]
    assert plan["summary"] == request["productIntent"]["summary"]
    assert intent["references"] == []  # No automatic public reference extraction.
    decision = json.loads(acp_check(plan)["outputs"]["value"]["handoff"]["recentDecisions"][0])
    assert decision["productIntent"]["ownerInstructions"] == [request["seed"], *request["productIntent"]["ownerInstructions"]]
    assert all("private-owner" not in text for text in public_texts(plan).values())


@pytest.mark.parametrize("text", ["The runtime is configurable by the user.",
                                  "The model is a domain object for repository data."])
def test_generic_product_vocabulary_without_identifiers_is_not_private_content(text):
    request = request_for()
    request["productIntent"]["summary"] = text
    assert text in public_texts(compile_bundle(request))["README.md"]
