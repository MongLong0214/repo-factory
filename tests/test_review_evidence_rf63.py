"""RF63 R1: scan titled-link prose and reject unsupported 64-hex references early."""
import importlib

import pytest

from test_genesis_bundle import compile_bundle
from test_public_text_contract import PUBLIC_FIELDS, put_public, request_for


URL = "https://example.com/run_public"
SHA1 = "ab12cd34" * 5
REFERENCE_ERROR = ("PUBLIC_REFERENCE: productIntent.references does not support standalone 64-hex values; "
                   "ACP treats them as credential-shaped; use a 40-hex SHA-1 or an absolute URL")


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("reference", [URL, SHA1])
@pytest.mark.parametrize("title", ["See {}", "title ) end"])
def test_rf63_titled_link_prose_compiles_across_public_surfaces(private_field, public_field, reference, title):
    request = request_for()
    private = f"operation ID {reference}"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    public = f'[a]({reference} "{title.format(reference)}")'
    put_public(request, public_field, public)
    request["productIntent"]["references"].append(reference)
    plan = compile_bundle(request)
    assert private in plan["verificationIntent"]
    if public_field == "issue":
        assert plan["genesis"]["issues"][0]["body"] == public


@pytest.mark.parametrize("private_field", ["reviewEvidence", "stagePlan"])
@pytest.mark.parametrize("public_field", PUBLIC_FIELDS)
@pytest.mark.parametrize("reference", [URL, SHA1])
@pytest.mark.parametrize("template", ['[a]({} "See run_privateproof")',
                                      '[run_privateproof]({} "title ) end")'])
def test_rf63_private_title_and_label_identifiers_stay_refused(private_field, public_field, reference, template):
    request = request_for()
    private = f"operation ID {reference}; session ID run_privateproof"
    request[private_field] = [private] if private_field == "reviewEvidence" else private
    put_public(request, public_field, template.format(reference))
    request["productIntent"]["references"].append(reference)
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        compile_bundle(request)
    assert "run_privateproof" not in str(caught.value)
    assert reference not in str(caught.value)


@pytest.mark.parametrize("reference,extended", [
    *[(URL, URL + suffix) for suffix in ("/x", "?q=1", "#f", "X", "?")],
    *[(SHA1, public) for public in (f"x{SHA1}y", SHA1 + "0", "x" + SHA1)],
])
@pytest.mark.parametrize("title", ["See {}", "title ) end"])
def test_rf63_titled_links_do_not_exempt_extended_destinations(reference, extended, title):
    bootstrap = importlib.import_module("bootstrap")
    public = f'[a]({extended} "{title.format(reference)}")'
    with pytest.raises(ValueError, match="PUBLIC_TEXT: private identifier") as caught:
        bootstrap._screen_public(public, private_values=[f"operation ID {reference}"],
                                 public_references=[reference])
    assert reference not in str(caught.value)


@pytest.mark.parametrize("reference", ["ab12cd34" * 8, "AB12CD34" * 8, " " + "ab12cd34" * 8 + " "])
@pytest.mark.parametrize("entrypoint", ["validate_request", "compile_bundle"])
def test_rf63_64_hex_reference_refused_at_rf_request_validation(monkeypatch, reference, entrypoint):
    bootstrap = importlib.import_module("bootstrap")
    request = request_for()
    request["productIntent"]["references"] = [reference]

    def unexpected_acp(*args, **kwargs):
        pytest.fail("64-hex references must be refused before the ACP bridge")

    monkeypatch.setattr(bootstrap, "acp_normalize", unexpected_acp)
    compile_request = bootstrap.validate_request if entrypoint == "validate_request" else compile_bundle
    with pytest.raises(ValueError) as caught:
        compile_request(request)
    assert str(caught.value) == REFERENCE_ERROR
    assert reference.strip() not in str(caught.value)
