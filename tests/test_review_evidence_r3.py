"""RF61 R3: formatted exact references remain public; extensions do not."""
import importlib

import pytest

from test_genesis_bundle import acp_check, compile_bundle, public_texts
from test_public_text_contract import PUBLIC_FIELDS, put_public, request_for


URL = "https://example.com/run_public"
SHA = "ab12cd34" * 5


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("reference,formatted", [(URL, f"[source]({URL})"), (SHA, f"`{SHA}`")])
def test_r3_formatted_exact_references_across_every_public_surface(private_field, public_field, reference, formatted):
    request = request_for()
    private = f"operation ID {reference}"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, public_field, formatted)
    request["productIntent"]["references"].append(reference)
    plan = compile_bundle(request)
    assert private in plan["verificationIntent"]
    assert any(formatted in value for value in public_texts(plan).values())
    if public_field == "issue":
        assert acp_check(plan)["intent"]["allowed"] is True


@pytest.mark.parametrize("reference", [URL, SHA])
@pytest.mark.parametrize("template", [
    "({})", "[{}]", "<{}>", "`{}`", '"{}"', "'{}'", ",{},", ";{};", " {} ",
    "{}.", "[source]({}).", "See [{}], then continue.",
])
def test_exact_reference_delimiters(reference, template):
    bootstrap = importlib.import_module("bootstrap")
    assert reference not in bootstrap._without_exact_references(template.format(reference), [reference])


@pytest.mark.parametrize("reference,extended", [
    *[(URL, URL + suffix) for suffix in ("/detail", "?detail=1", "?", "#anchor", ".detail", "suffix")],
    (SHA, "prefix-" + SHA),
    (SHA, SHA + "a"),
    (SHA, "prefix_" + SHA),
    (SHA, "https://example.com/commit/" + SHA),
])
@pytest.mark.parametrize("template", ["{}", "[source]({})", "`{}`"])
def test_formatting_does_not_exempt_extended_urls_or_embedded_shas(reference, extended, template):
    bootstrap = importlib.import_module("bootstrap")
    public = template.format(extended)
    assert bootstrap._without_exact_references(public, [reference]) == public
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier at public") as caught:
        bootstrap._screen_public(public, private_values=[f"operation ID {reference}"], public_references=[reference])
    assert reference not in str(caught.value)


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("reference,public,allowed", [
    *[(URL, URL + suffix, False) for suffix in ("/x", "?q=1", "#f", "X")],
    (URL, f"[a]({URL})", True),
    (URL, f"`{URL}`", True),
    (SHA, f"`{SHA}`", True),
    (SHA, f"x{SHA}y", False),
])
def test_ceo_full_reference_values(private_field, reference, public, allowed):
    request = request_for()
    private = f"operation ID {reference}"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    request["productIntent"]["references"] = [reference]
    put_public(request, "issue", public)
    if allowed:
        assert compile_bundle(request)["genesis"]["issues"][0]["body"] == public
    else:
        with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
            compile_bundle(request)
        assert reference not in str(caught.value)


@pytest.mark.parametrize("reference,public", [
    (URL, f"[a]({URL}(x))"),
    (URL, f"[a]({URL}.)"),
    (URL, f"<{URL})>"),
    (URL, f"<{URL}.>"),
    (URL, f"`{URL}]`"),
    (URL, f"`{URL}.`"),
    *[(URL, URL + suffix) for suffix in ("(x)", ")", "]", ";x", ",x", "'x", '"x', "`x", ">x")],
    (URL + ")", f"[a]({URL}))"),
    (URL + ".", URL),
    (URL + "/longer", URL + "."),
])
def test_wrapper_contents_and_bare_url_punctuation_are_not_split(reference, public):
    bootstrap = importlib.import_module("bootstrap")
    assert bootstrap._without_exact_references(public, [reference]) == public
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        bootstrap._screen_public(public, private_values=[f"operation ID {reference}"], public_references=[reference])
    assert reference not in str(caught.value)


@pytest.mark.parametrize("reference,template", [
    *[(URL, "{}" + punctuation) for punctuation in (".", ",", ";")],
    *[(URL + suffix, "<{}>") for suffix in (".", ")", "]")],
    *[(URL + suffix, "`{}`") for suffix in (".", ")", "]")],
    (URL + "(x)", "[a]({})"),
    (URL + ".", "{}"),
    (URL + ".", "{}."),
])
def test_only_exact_full_url_values_are_exempt(reference, template):
    bootstrap = importlib.import_module("bootstrap")
    public = template.format(reference)
    assert reference not in bootstrap._without_exact_references(public, [reference])
