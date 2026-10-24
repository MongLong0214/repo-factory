"""RF61 R1: private token leaks and ACP review-intent boundary regressions."""
import copy
import importlib

import pytest

from test_genesis_bundle import REQUEST, acp_check, compile_bundle, public_texts
from test_public_text_contract import FEATURES, PUBLIC_FIELDS, put_public, request_for
from test_review_evidence import STAGE


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("private,token", [
    ("Prerequisite verified using CEO decision 52b6e780 in the private record.", "52b6e780"),
    ("Prerequisite verified using session_id=private-demo in the private record.", "private-demo"),
])
def test_bare_private_identifiers_are_refused_across_public_surfaces(private_field, public_field, private, token):
    request = request_for()
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, public_field, f"Public provenance: {token}")
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        compile_bundle(request)
    assert token not in str(caught.value)


@pytest.mark.parametrize("private,token", [
    ("Verified private decision abc1234.", "abc1234"),
    ("Verified private session_id=12345678-abcd-4321-abcd-123456789abc.", "12345678-abcd-4321-abcd-123456789abc"),
    *[(f"Verified private session_id={prefix}_privateproof.", f"{prefix}_privateproof")
      for prefix in ("ses", "run", "msg", "prj", "asg")],
    ("Private session_id=hidden-record.", "hidden-record"),
    ('Private session_id: "hidden-record".', "hidden-record"),
    ("Verified file /nonstandard/private/witness.txt privately.", "/nonstandard/private/witness.txt"),
    ("Verified private decision ID abc-123.", "abc-123"),
    ("Verified private decision ID abc_123.", "abc_123"),
])
@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
def test_private_identifier_shapes_are_screened_without_their_context(private, token, private_field):
    request = request_for()
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, "summary", f"Public provenance: {token}")
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier"):
        compile_bundle(request)


def test_public_generation_receives_no_private_review_fields(monkeypatch):
    bootstrap = importlib.import_module("bootstrap")
    original_compile = bootstrap.compile_plan
    original_public_text = bootstrap._public_product_text
    original_product_intent = bootstrap._product_intent
    seen = []

    def guarded(function):
        def call(request, *args, **kwargs):
            assert "reviewEvidence" not in request and "stagePlan" not in request
            if function.__name__ == "_public_product_text":
                assert not args and not kwargs  # No private screening data reaches this renderer either.
            seen.append(function.__name__)
            return function(request, *args, **kwargs)
        return call

    monkeypatch.setattr(bootstrap, "compile_plan", guarded(original_compile))
    monkeypatch.setattr(bootstrap, "_public_product_text", guarded(original_public_text))
    monkeypatch.setattr(bootstrap, "_product_intent", guarded(original_product_intent))
    request = request_for()
    request.update(reviewEvidence=["Private witness abc1234 stays here."], stagePlan=STAGE)
    compile_bundle(request)
    assert seen == ["_public_product_text", "compile_plan", "_product_intent"]


@pytest.mark.parametrize("word", ["reports", "42", "2026", "user-facing", "read-only", "end-to-end"])
def test_plain_product_words_numbers_and_hyphenated_english_are_not_private_ids(word):
    request = request_for()
    request["reviewEvidence"] = [f"Private discussion mentioned {word} during prerequisite checking."]
    put_public(request, "issue", f"Acceptance: support {word} output.")
    plan = compile_bundle(request)
    assert plan["genesis"]["issues"][0]["body"] == f"Acceptance: support {word} output."


def test_freshreadme_public_acceptance_bodies_and_milestones_survive_unchanged():
    request = request_for()
    put_public(request, "issue", FEATURES[0])
    request["genesis"]["issues"] = [
        {**request["genesis"]["issues"][0], "key": f"feature-{index}", "body": body}
        for index, body in enumerate(FEATURES)
    ]
    request["genesis"]["milestones"][0]["description"] = "\n".join(FEATURES)
    request["reviewEvidence"] = ["Verified private prerequisite abc1234 via session_id=private-demo."]
    request["stagePlan"] = STAGE
    before = copy.deepcopy(request["genesis"])
    plan = compile_bundle(request)
    assert plan["genesis"]["issues"] == before["issues"]
    assert plan["genesis"]["milestones"] == before["milestones"]
    assert acp_check(plan)["intent"]["allowed"] is True


def test_private_review_data_cannot_change_any_public_output_bytes():
    request = request_for()
    put_public(request, "issue", "Acceptance: report every broken link.")
    baseline = compile_bundle(request)
    for evidence, stage in [
        (["Private decision c0ffee42; session_id=private-demo; /internal/witness."], STAGE),
        (["Private decision ab12cd34; session_id=another-private-session; /other/witness."], STAGE + " Follow up after activation."),
    ]:
        candidate = compile_bundle({**request, "reviewEvidence": evidence, "stagePlan": stage})
        assert candidate["genesis"]["files"] == baseline["genesis"]["files"]
        assert {key: value.encode("utf-8") for key, value in public_texts(candidate).items()} == {
            key: value.encode("utf-8") for key, value in public_texts(baseline).items()}
        assert {key: value for key, value in candidate.items() if key not in ("verificationIntent", "requestDigest")} == {
            key: value for key, value in baseline.items() if key not in ("verificationIntent", "requestDigest")}
        assert candidate["verificationIntent"] != baseline["verificationIntent"]
        # requestDigest also binds the private request data; it is not a public output.


@pytest.mark.parametrize("field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public", [
    "https://github.com/example/public-project",
    "https://github.com/example/ACP",
    "https://github.com/example/run_public",
    "ab12cd34" * 5,
    "1234567890" * 4,
])
@pytest.mark.parametrize("verbatim", [False, True])
def test_explicit_public_reference_remains_public_when_private_evidence_repeats_it(field, public, verbatim):
    request = request_for()
    request["productIntent"]["references"] = [public]
    baseline = compile_bundle(request)
    private = public if verbatim else f"Prerequisite checked against the declared public reference {public}."
    request[field] = [private] if field == "reviewEvidence" else private
    plan = compile_bundle(request)
    assert public_texts(plan) == public_texts(baseline)
    assert private in plan["verificationIntent"]
    assert acp_check(plan)["intent"]["allowed"] is True


@pytest.mark.parametrize("field", ["reviewEvidence", "stagePlan"])
def test_explicit_product_acceptance_remains_public_when_private_data_repeats_it(field):
    request = request_for()
    acceptance = FEATURES[0]
    put_public(request, "issue", acceptance)
    baseline = compile_bundle(request)
    request[field] = [acceptance] if field == "reviewEvidence" else acceptance
    assert public_texts(compile_bundle(request)) == public_texts(baseline)


def test_private_id_substring_does_not_reclassify_an_existing_public_commit_sha():
    request = request_for()
    public_sha = "52b6e780" + "abcdef12" * 4
    request["productIntent"]["references"] = [public_sha]
    baseline = compile_bundle(request)
    request["reviewEvidence"] = ["CEO decision 52b6e780 stays private.", f"Public commit {public_sha} was checked."]
    assert public_texts(compile_bundle(request)) == public_texts(baseline)


@pytest.mark.parametrize("field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("length", [1000, 1001])
def test_intent_item_character_bound_is_enforced_before_compile_success(field, length):
    request = copy.deepcopy(REQUEST)
    text = "z" * length
    request[field] = [text] if field == "reviewEvidence" else text
    if length == 1001:
        with pytest.raises(ValueError, match=r"ACP_PLAN_INTENT: verificationIntent\[1\]") as caught:
            compile_bundle(request)
        assert text not in str(caught.value)
    else:
        plan = compile_bundle(request)
        assert plan["verificationIntent"][1] == text
        assert acp_check(plan)["intent"]["allowed"] is True


@pytest.mark.parametrize("count", [32, 33])
def test_intent_list_bound_counts_every_generated_item(count):
    request = copy.deepcopy(REQUEST)
    request["stagePlan"] = STAGE
    # Genesis verification + stage statement + one issue line + evidence.
    request["reviewEvidence"] = [f"Private witness word {index}." for index in range(count - 3)]
    if count == 33:
        with pytest.raises(ValueError, match="ACP_PLAN_INTENT: verificationIntent"):
            compile_bundle(request)
    else:
        plan = compile_bundle(request)
        assert len(plan["verificationIntent"]) == count
        assert acp_check(plan)["intent"]["allowed"] is True


@pytest.mark.parametrize("new_fields", [{}, {"reviewEvidence": []}, {"reviewEvidence": ["Private prerequisite checked."]}])
def test_32_seeded_issues_without_stage_plan_retain_base_intent_and_pass_review(new_fields):
    request = request_for()
    put_public(request, "issue", "Acceptance: report broken links.")
    request["genesis"]["issues"] = [
        {**request["genesis"]["issues"][0], "key": f"feature-{index}"} for index in range(32)
    ]
    request.update(new_fields)
    plan = compile_bundle(request)
    assert plan["verificationIntent"] == [
        "Run declared local tests and required CI with Actions provenance at the genesis PR head "
        "before ACP merges into main; verify merged main before activation",
        *new_fields.get("reviewEvidence", []),
    ]
    assert len(plan["genesis"]["issues"]) == 32
    assert acp_check(plan)["intent"]["allowed"] is True


def test_long_issue_key_is_refused_in_generated_responsibility_line():
    request = request_for()
    put_public(request, "issue", "Acceptance: report broken links.")
    request["stagePlan"] = STAGE
    request["genesis"]["issues"][0]["key"] = "k" * 700
    with pytest.raises(ValueError, match=r"ACP_PLAN_INTENT: verificationIntent\[2\]"):
        compile_bundle(request)


@pytest.mark.parametrize("length", [500, 501])
def test_acp_utf16_character_count_is_authoritative(length):
    request = copy.deepcopy(REQUEST)
    request["reviewEvidence"] = ["🌱" * length]
    if length == 501:
        with pytest.raises(ValueError, match="ACP_PLAN_INTENT: verificationIntent"):
            compile_bundle(request)
    else:
        assert acp_check(compile_bundle(request))["intent"]["allowed"] is True
