"""RF61 R4 / #62: exact reference boundaries without prefix exemptions."""
import importlib

import pytest

from test_genesis_bundle import compile_bundle
from test_public_text_contract import PUBLIC_FIELDS, put_public, request_for


URL = "https://example.com/run_public"
SHA1 = "ab12cd34" * 5
SHA256 = "ab12cd34" * 8
FORMATS = [*("{}" + mark for mark in ":!?。！？"),
           "Unmatched ` prose; then {}", '[a]({} "title")', "**{}**", "*{}*", "_{}_"]


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("reference", [SHA1, SHA256, URL])
@pytest.mark.parametrize("template", FORMATS)
def test_r4_exact_references_across_every_public_surface(private_field, public_field, reference, template):
    request = request_for()
    private = f"operation ID {reference}"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    # Preserve the existing empty-query refusal; explicit wrappers disambiguate URL '?'.
    public = "<" + reference + ">?" if reference == URL and template == "{}?" else template.format(reference)
    put_public(request, public_field, public)
    request["productIntent"]["references"].append(reference)
    bootstrap = importlib.import_module("bootstrap")
    for value in (request["productIntent"]["summary"], request["productIntent"]["references"],
                  request["productIntent"].get("publicRequirements"), request.get("genesis")):
        bootstrap._screen_public(value, private_values=bootstrap._private_review_text(request),
                                 public_references=bootstrap._public_references(request))


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference", [SHA1, URL])
@pytest.mark.parametrize("template", FORMATS)
def test_r4_formatted_reference_bytes_survive_compilation(private_field, reference, template):
    request = request_for()
    private = f"operation ID {reference}"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    public = "<" + reference + ">?" if reference == URL and template == "{}?" else template.format(reference)
    put_public(request, "issue", public)
    request["productIntent"]["references"] = [reference]
    plan = compile_bundle(request)
    assert private in plan["verificationIntent"]
    assert plan["genesis"]["issues"][0]["body"] == public


@pytest.mark.parametrize("reference,extended", [
    *[(URL, URL + suffix) for suffix in ("/x", "?q=1", "#f", "X", "?", ";x", ":x", "!x")],
    *[(sha, public) for sha in (SHA1, SHA256)
      for public in (f"x{sha}y", sha + "0", "x" + sha)],
])
@pytest.mark.parametrize("template", ['[a]({} "title")', "**{}**", "*{}*", "_{}_",
                                      "Unmatched ` prose; then {}"])
def test_r4_new_formats_preserve_extension_refusals(reference, extended, template):
    bootstrap = importlib.import_module("bootstrap")
    public = template.format(extended)
    assert bootstrap._without_exact_references(public, [reference]) == public
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        bootstrap._screen_public(public, private_values=[f"operation ID {reference}"],
                                 public_references=[reference])
    assert reference not in str(caught.value)


@pytest.mark.parametrize("public", [f'[run_privateproof]({URL} "title")',
                                    f'[a]({URL} "run_privateproof")',
                                    f'**{URL} run_privateproof**',
                                    f'`x{SHA1}`', f'`{URL}.`', f'<{URL}.>'])
def test_r4_labels_titles_and_opaque_wrappers_still_screen_private_values(public):
    bootstrap = importlib.import_module("bootstrap")
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        bootstrap._screen_public(public, private_values=["operation ID run_privateproof", URL, SHA1],
                                 public_references=[URL, SHA1])
    assert "run_privateproof" not in str(caught.value)


@pytest.mark.parametrize("reference", [URL + mark for mark in ":!?。！？"])
def test_r4_url_punctuation_bytes_are_compared_before_sentence_punctuation(reference):
    bootstrap = importlib.import_module("bootstrap")
    for public in (reference, reference + ":", f'[a]({reference} "title")', f"**{reference}**"):
        assert reference not in bootstrap._without_exact_references(public, [reference])
