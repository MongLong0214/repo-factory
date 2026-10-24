"""RF61 R2: exact private identifier extraction and reference-only exemptions."""
import importlib

import pytest

from test_genesis_bundle import compile_bundle, public_texts
from test_public_text_contract import PUBLIC_FIELDS, put_public, request_for


SINKS = {
    "summary": "productIntent.summary",
    "publicRequirements": "productIntent.publicRequirements[0]",
    "reference": "productIntent.references[0]",
    "commit": "genesis.commitMessage",
    "issue": "genesis.issues[0].body",
    "issue_title": "genesis.issues[0].title",
    "milestone": "genesis.milestones[0].description",
    "milestone_title": "genesis.milestones[0].title",
    "label": "genesis.labels[0].description",
    "label_name": "genesis.labels[0].name",
}


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("private,token", [
    ("CEO decision ID 52b6e780", "52b6e780"),
    ("session_id private-demo", "private-demo"),
    ("session id: abc-123x", "abc-123x"),
])
def test_r2_leak_siblings_refused_with_sink_without_value(private_field, public_field, private, token):
    request = request_for()
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, public_field, f"Public provenance: {token}")
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        compile_bundle(request)
    assert SINKS[public_field] in str(caught.value)
    assert token not in str(caught.value)


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
def test_bare_decision_is_prose_not_an_id_declaration(private_field, public_field):
    request = request_for()
    private = "Decision: support exports."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    acceptance = "Acceptance: support Markdown exports."
    put_public(request, public_field, acceptance)
    assert any(acceptance in text for text in public_texts(compile_bundle(request)).values())


@pytest.mark.parametrize("key", ["session", "run", "decision", "message", "project",
                                  "operation", "actor", "binding", "escalation"])
@pytest.mark.parametrize("joiner", ["", "_", "-", " "])
@pytest.mark.parametrize("separator", ["=", ": ", " "])
def test_rule_a_all_explicit_id_keys(key, joiner, separator):
    bootstrap = importlib.import_module("bootstrap")
    private = f"Checked {key.upper()}{joiner}ID{separator}private-demo."
    assert bootstrap._private_identifiers([private]) == {"private-demo"}


@pytest.mark.parametrize("private,expected", [
    ("Revision abc1234.", {"abc1234"}),
    ("Revision 1234567.", {"1234567"}),
    ("12345678-abcd-4321-abcd-123456789abc", {"12345678", "123456789abc", "12345678-abcd-4321-abcd-123456789abc"}),
    *[(f"Checked {prefix}_privateproof.", {f"{prefix}_privateproof"})
      for prefix in ("ses", "run", "msg", "prj", "asg", "art", "task", "operation")],
    ("Checked /Users/private/proof.txt.", {"/Users/private/proof.txt"}),
    ("Checked /home/private/proof.txt.", {"/home/private/proof.txt"}),
    ("Checked ~/private/proof.txt.", {"~/private/proof.txt"}),
    ("Decision: support exports.", set()),
    ("Session: support exports.", set()),
    ("Product: abc-123x; note=private-demo.", set()),
    ("Ordinary acceptance: read-only user-facing exports, 42 reports.", set()),
    ("A session identifier stays private; indecision_id private-demo.", set()),
    ("The resident-safe-project-id and later changes.", set()),
])
def test_rule_a_only_identifier_shapes_and_explicit_id_keys(private, expected):
    assert importlib.import_module("bootstrap")._private_identifiers([private]) == expected


@pytest.mark.parametrize("reference,allowed", [
    ("ab12cd34" * 5, True),
    ("1234567890" * 4, True),
    ("ab12cd34" * 8, True),
    ("https://github.com/example/public-project", True),
    ("1718881034", False),
    ("Public provenance: abc1234", False),
    ("https://", False),
])
def test_rule_b_reference_formats(reference, allowed):
    request = request_for()
    request["productIntent"]["references"] = [reference]
    assert importlib.import_module("bootstrap")._public_references(request) == ([reference] if allowed else [])


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference,token", [
    ("https://github.com/example/run_public", "run_public"),
    ("https://github.com/example/project/commit/52b6e780", "52b6e780"),
    ("52b6e780" + "abcdef12" * 4, "52b6e780"),
])
def test_rule_b_reference_substrings_do_not_exempt_bare_identifiers(private_field, reference, token):
    request = request_for()
    put_public(request, "issue", f"Public provenance: {token}")
    request["productIntent"]["references"] = [reference]
    private = f"session ID {token}."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private identifier at genesis.issues\[0\].body") as caught:
        compile_bundle(request)
    assert token not in str(caught.value)


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference", [
    "Documentation https://example.com/guide; decision ID private-demo.",
    "https://example.com/guide — session ID private-demo",
])
def test_private_id_in_reference_prose_is_not_exempt(private_field, reference):
    request = request_for()
    private = "session ID private-demo."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    request["productIntent"]["references"] = [reference]
    put_public(request, "issue", "Public provenance: private-demo")
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private identifier at productIntent.references\[0\]") as caught:
        compile_bundle(request)
    assert "private-demo" not in str(caught.value)


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference", ["https://github.com/example/run_public", "ab12cd34" * 5, "1234567890" * 4])
@pytest.mark.parametrize("wrapper", ["{}", "Public provenance: ({})."])
def test_exact_reference_url_or_standalone_sha_is_allowed(private_field, reference, wrapper):
    request = request_for()
    private = f"operation ID {reference}."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    request["productIntent"]["references"] = [reference]
    public = wrapper.format(reference)
    put_public(request, "issue", public)
    plan = compile_bundle(request)
    assert plan["genesis"]["issues"][0]["body"] == public
    assert private in plan["verificationIntent"]


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference,embedded", [
    ("ab12cd34" * 5, "prefix-" + "ab12cd34" * 5),
    ("ab12cd34" * 5, "ab12cd34" * 5 + "a"),
    ("ab12cd34" * 5, "https://example.com/commit/" + "ab12cd34" * 5),
    ("https://example.com/guide", "https://example.com/guide?decision=public"),
    ("https://example.com/guide", "https://example.com/guide?"),
    ("https://example.com/guide", "https://example.com/guide."),
    ("https://example.com/guide", "(https://example.com/guide?)."),
])
def test_reference_value_embedded_in_another_token_is_not_exempt(private_field, reference, embedded):
    request = request_for()
    private = f"operation ID {reference}."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    request["productIntent"]["references"] = [reference]
    put_public(request, "issue", f"Public provenance: {embedded}")
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private identifier at genesis.issues\[0\].body") as caught:
        compile_bundle(request)
    assert reference not in str(caught.value)


@pytest.mark.parametrize("reference", ["Public provenance: abc1234", "abc1234 is documented here"])
def test_arbitrary_reference_prose_does_not_exempt_private_identifiers(reference):
    request = request_for()
    request["productIntent"]["references"] = [reference]
    request["reviewEvidence"] = ["Verified abc1234."]
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier"):
        compile_bundle(request)


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("token,public", [
    ("run_privateproof", "https://github.com/example/run_privateproof"),
    ("52b6e780", "52b6e780" + "abcdef12" * 4),
    ("/Users/private/proof", "https://example.com/Users/private/proof"),
])
def test_url_or_sha_in_other_public_input_is_not_a_reference_exemption(private_field, token, public):
    request = request_for()
    private = f"session ID {token}."
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, "issue", f"Public provenance: {public}")
    with pytest.raises(ValueError, match=r"PUBLIC_TEXT: private identifier at genesis.issues\[0\].body"):
        compile_bundle(request)


@pytest.mark.parametrize("public", ["https://github.com/example/ACP", "1234567890" * 4])
def test_public_provenance_without_private_data_keeps_legacy_behavior(public):
    request = request_for()
    put_public(request, "issue", f"Public provenance: {public}")
    assert compile_bundle(request)["genesis"]["issues"][0]["body"] == f"Public provenance: {public}"


def test_review_prose_overlap_is_not_an_identifier_rule():
    request = request_for()
    acceptance = "Acceptance: support Markdown exports."
    put_public(request, "issue", acceptance)
    baseline = compile_bundle(request)
    request["reviewEvidence"] = [acceptance]
    request["stagePlan"] = acceptance
    assert public_texts(compile_bundle(request)) == public_texts(baseline)
