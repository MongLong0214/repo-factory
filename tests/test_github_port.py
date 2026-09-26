"""The gh-backed GitHubPort: what it reads, what it builds, and what it refuses."""
from __future__ import annotations

import copy
import io
import json
import sys
from email.message import Message
from pathlib import Path
from typing import List, Tuple
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from apply import (  # noqa: E402
    OWNER_AUTHORIZATION_REQUIRED, REREAD_MISMATCH, RESUMED_RESOURCE_DRIFTED, ApplyError, ReceiptLedger,
    apply_plan, authorized_plan_receipt,
)


def approval(plan_core, authority: str = "OWNER"):
    """A receipt the approver would have produced for exactly this plan.

    Built with the production helper so a test cannot approve a plan in a way a real caller
    could not. Tests about tampering modify what this returns."""
    return authorized_plan_receipt(plan_core, authority=authority, actor="owner:isaac",
                                   approved_at="2026-08-19T09:00:00Z")

from github_port import GhCliPort, GhError, _app_http, parse_identity  # noqa: E402


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("path,method,body", [
    ("/app/installations/456/access_tokens", "POST", '{"repository_ids":[17]}'),
    ("/installation/repositories", "GET", None),
    ("/graphql", "POST", '{"query":"mutation"}'),
])
def test_scoped_app_http_never_follows_credential_redirect(monkeypatch, path, method, body, status):
    credential = "test-only-secret-credential"
    target = "https://attacker.invalid/steal"
    seen = []

    def fake_https_open(self, request):
        seen.append((request.full_url, request.get_header("Authorization")))
        if request.full_url == target:
            response = addinfourl(io.BytesIO(b'{"unexpected":"success"}'), Message(), target, 200)
            setattr(response, "msg", "OK")
            return response
        headers = Message()
        headers["Location"] = target
        response = addinfourl(io.BytesIO(b"test-only-secret-response-body"), headers,
                              request.full_url, status)
        setattr(response, "msg", "Found")
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", fake_https_open)
    with pytest.raises(GhError) as caught:
        _app_http("https://api.github.com" + path, method,
                  {"Authorization": "Bearer " + credential}, body)
    assert seen == [("https://api.github.com" + path, "Bearer " + credential)]
    assert credential not in str(caught.value)
    assert "test-only-secret-response-body" not in str(caught.value)


@pytest.mark.parametrize("bad_url", ["http://api.github.com/graphql",
                                       "https://attacker.invalid/graphql",
                                       "https://api.github.com@attacker.invalid/graphql",
                                       "https://api.github.com:444/graphql"])
def test_scoped_app_http_refuses_non_api_origin_before_sending(monkeypatch, bad_url):
    def never_open(self, request):
        pytest.fail("credentials sent to an untrusted origin")

    monkeypatch.setattr(HTTPSHandler, "https_open", never_open)
    with pytest.raises(GhError, match="scoped GitHub App request failed"):
        _app_http(bad_url, "POST", {"Authorization": "Bearer test-only-secret"}, "{}")


def test_scoped_app_http_caps_response_read_without_leaking_body(monkeypatch):
    body = b'{"token":"test-only-secret-response"}' + b"x" * (1024 * 1024)
    response = addinfourl(io.BytesIO(body), Message(), "https://api.github.com/graphql", 200)
    setattr(response, "msg", "OK")
    seen = []
    original_read = response.read

    def bounded_read(size=-1):
        seen.append(size)
        return original_read(size)

    monkeypatch.setattr(response, "read", bounded_read)
    monkeypatch.setattr(HTTPSHandler, "https_open", lambda self, request: response)
    with pytest.raises(GhError) as caught:
        _app_http("https://api.github.com/graphql", "POST",
                  {"Authorization": "Bearer test-only-secret"}, "{}")
    assert seen == [1024 * 1024 + 1]
    assert "test-only-secret-response" not in str(caught.value)


def test_scoped_app_http_reads_small_json(monkeypatch):
    def fake_https_open(self, request):
        response = addinfourl(io.BytesIO(b'{"total_count":1}'), Message(), request.full_url, 200)
        setattr(response, "msg", "OK")
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", fake_https_open)
    assert _app_http("https://api.github.com/installation/repositories", "GET",
                     {"Authorization": "Bearer test-only-secret"}) == {"total_count": 1}


def test_transfer_after_observation_cannot_update_destination(tmp_path, monkeypatch):
    identity = "github:example/alpha#42"
    core = {"bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
            "requestDigest": "sha256:" + "a" * 64, "authorization": "OWNER",
            "githubOperations": [{"operationId": "update-issue:42", "resourceType": "issue",
                                  "intent": "update", "resourceIdentity": identity,
                                  "desiredState": {"title": "Approved title"}}]}
    for key, value in {"RF_GITHUB_APP_ID": "123", "RF_GITHUB_APP_INSTALLATION_ID": "456",
                       "RF_GITHUB_APP_PRIVATE_KEY_PATH": str(tmp_path / "key.pem")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "key.pem").write_text("test-only key")
    state = {"repository": 17, "title": "Old title", "mutation_attempts": 0, "minted": 0}
    def run(argv, stdin=None):
        if argv == ["gh", "api", "repos/example/alpha"]:
            return 0, json.dumps({"id": 17}), ""
        if argv == ["gh", "api", "repos/example/alpha/issues/42"]:
            return 0, json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                                  "number": 42, "node_id": "I_original", "title": state["title"]}), ""
        raise AssertionError(f"unexpected gh command: {argv}")
    def http(url, method, headers, body=None):
        assert url.startswith("https://api.github.com/")
        if url.endswith("/access_tokens"):
            state["minted"] += 1
            assert json.loads(body) == {"repository_ids": [17], "permissions": {"issues": "write"}}
            state["repository"] = 99  # issue transferred after observation, before GraphQL
            return {"token": "scoped-test-token", "repositories": [{"id": 17}],
                    "permissions": {"issues": "write"}}
        if url.endswith("/installation/repositories"):
            assert headers["Authorization"] == "Bearer scoped-test-token"
            return {"total_count": 1, "repositories": [{"id": 17}]}
        if url.endswith("/graphql"):
            assert headers["Authorization"] == "Bearer scoped-test-token"
            assert json.loads(body)["variables"]["input"]["id"] == "I_original"
            state["mutation_attempts"] += 1
            if state["repository"] != 17:
                return {"errors": [{"message": "Resource not accessible by integration"}]}
            state["title"] = "Approved title"
            return {"data": {"updateIssue": {"issue": {"id": "I_original"}}}}
        raise AssertionError(url)
    port = GhCliPort(runner=run, http=http, app_signer=lambda path, app_id: "test-jwt")
    book = ReceiptLedger(tmp_path / "receipts.json")
    with pytest.raises(ApplyError, match="remote"):
        apply_plan(core, port, book, authorization=approval(core))
    assert state == {"repository": 99, "title": "Old title", "mutation_attempts": 1, "minted": 1}
    assert book.get("update-issue:42") is None


class ScriptedGh:
    """argv → (exit, stdout, stderr). 네트워크 없이 정확한 명령 구성을 검사한다."""

    def __init__(self, responses):
        self.responses = responses
        self.seen: List[List[str]] = []
        self.stdin: List[str] = []

    def __call__(self, argv: List[str], stdin: str = None) -> Tuple[int, str, str]:
        self.seen.append(argv)
        self.stdin.append(stdin)
        if argv == ["gh", "api", "repos/example/alpha"]:
            return 0, '{"id":17}', ""
        for match, reply in self.responses:
            if match in " ".join(argv):
                return reply
        return 1, "", "gh: HTTP 404: Not Found"


def test_identity_requires_a_host_prefix():
    assert parse_identity("github:MongLong0214/alpha") == ("MongLong0214", "alpha", None)
    assert parse_identity("github:MongLong0214/alpha#dev") == ("MongLong0214", "alpha", "dev")
    for bad in ["MongLong0214/alpha", "github:alpha", "github:a/b/c", "gitlab:a/b"]:
        with pytest.raises(GhError):
            parse_identity(bad)


def test_a_missing_repository_reads_as_absent_not_as_an_error():
    # 404 is the answer to "is it there", not a failure. Raising here would kill every
    # preexisting check; swallowing a real error would write on top of someone else's repo.
    port = GhCliPort(runner=ScriptedGh([]))

    assert port.observe("repository", "github:MongLong0214/alpha") is None


def test_a_transport_failure_is_not_reported_as_absent():
    gh = ScriptedGh([("api repos/", (1, "", "gh: HTTP 500: server error"))])
    port = GhCliPort(runner=gh)

    with pytest.raises(GhError, match="500"):
        port.observe("repository", "github:MongLong0214/alpha")


def test_observation_keeps_only_fields_that_do_not_drift():
    # A repository document carries star counts and timestamps. Digesting all of it would make
    # afterStateDigest differ on every read, and the receipt would stop meaning anything.
    body = json.dumps({"default_branch": "dev", "private": True, "node_id": "R_1",
                       "stargazers_count": 7, "pushed_at": "2026-08-19T09:00:00Z"})
    port = GhCliPort(runner=ScriptedGh([("api repos/", (0, body, ""))]))

    observed = port.observe("repository", "github:MongLong0214/alpha")

    assert observed == {"identity": "github:MongLong0214/alpha", "resourceType": "repository",
                        "defaultBranch": "dev", "private": True, "nodeId": "R_1"}


def test_creating_a_repository_defaults_to_private():
    gh = ScriptedGh([("repo create", (0, "", ""))])
    GhCliPort(runner=gh).create("repository", "github:MongLong0214/alpha", {})

    assert gh.seen[-1] == ["gh", "repo", "create", "MongLong0214/alpha", "--private"]


def test_public_is_passed_through_only_when_the_plan_state_says_so():
    gh = ScriptedGh([("repo create", (0, "", ""))])
    GhCliPort(runner=gh).create("repository", "github:MongLong0214/alpha", {"private": False})

    assert "--public" in gh.seen[-1]


def test_a_branch_needs_a_ref_and_a_source_commit():
    port = GhCliPort(runner=ScriptedGh([]))
    with pytest.raises(GhError, match="ref"):
        port.observe("branch", "github:MongLong0214/alpha")
    with pytest.raises(GhError, match="fromSha"):
        port.create("branch", "github:MongLong0214/alpha#dev", {})


def test_an_unobservable_resource_type_is_refused_rather_than_silently_skipped():
    # A write whose result cannot be read back cannot satisfy §16.2, so pretending to handle
    # it would produce a receipt that verifies nothing.
    port = GhCliPort(runner=ScriptedGh([]))
    with pytest.raises(GhError, match="post-write re-read"):
        port.observe("milestone", "github:MongLong0214/alpha#1")


def test_the_real_port_satisfies_the_engine_it_was_written_for():
    # Structural, not behavioural: the engine only ever calls these two.
    port = GhCliPort(runner=ScriptedGh([]))
    assert callable(port.observe) and callable(port.create)


# --- RF-S25 defence in depth ------------------------------------------------------------

def test_a_hermes_plan_that_would_create_a_public_repository_is_refused(tmp_path):
    # compile_plan already raises authorization to OWNER for a public request. This is the
    # second reading: if the compiler and the applier share one assumption, a wrong assumption
    # is stopped by nobody.
    plan = {
        "bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
        "requestDigest": "sha256:" + "a" * 64,
        "authorization": "HERMES",
        "repositories": [{"role": "primary", "identity": "github:MongLong0214/alpha", "visibility": "public"}],
        "githubOperations": [{"operationId": "create-repository:alpha", "resourceType": "repository",
                              "intent": "create", "resourceIdentity": "github:MongLong0214/alpha",
                              "desiredState": {"private": False}}],
    }

    class NeverCalled:
        def observe(self, *_):
            raise AssertionError("nothing may be observed before authorization is settled")

        def create(self, *_):
            raise AssertionError("nothing may be created under an insufficient authorization")

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan, NeverCalled(), ReceiptLedger(tmp_path / "r.json"), authorization=approval(plan))

    assert caught.value.code == OWNER_AUTHORIZATION_REQUIRED
    assert caught.value.evidence["repositories"] == ["github:MongLong0214/alpha"]


def test_the_same_plan_under_owner_authorisation_proceeds(tmp_path):
    plan = {
        "bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
        "requestDigest": "sha256:" + "a" * 64,
        "authorization": "OWNER",
        "repositories": [{"role": "primary", "identity": "github:MongLong0214/alpha", "visibility": "public"}],
        "githubOperations": [{"operationId": "create-repository:alpha", "resourceType": "repository",
                              "intent": "create", "resourceIdentity": "github:MongLong0214/alpha",
                              "desiredState": {"private": False}}],
    }

    class Fake:
        def __init__(self):
            self.state = {}

        def observe(self, _t, identity):
            return self.state.get(identity)

        def create(self, _t, identity, spec):
            # The created resource reflects what it was created with. A fake that drops `spec`
            # cannot represent the thing it claims to have made, and the post-write re-read then
            # compares the approved state against a stub that could never disagree with it.
            self.state[identity] = {"identity": identity, **spec}

    result = apply_plan(plan, Fake(), ReceiptLedger(tmp_path / "r.json"), authorization=approval(plan))

    assert result["completed"] is True


def test_a_rate_limit_is_told_apart_from_a_broken_call():
    # Measured on 2026-08-19: `gh api rate_limit` reported core 4954/5000 while repository
    # existence checks were refused, because GitHub limits 404-producing requests separately.
    # Reading the remaining core quota and concluding "fine" is therefore wrong, and a generic
    # transport error makes a wait-and-retry look like something to fix.
    from github_port import GhRateLimited

    gh = ScriptedGh([("api repos/", (1, "", "gh: API rate limit exceeded for user ID 97578200."))])
    port = GhCliPort(runner=gh)

    with pytest.raises(GhRateLimited, match="not a defect"):
        port.observe("repository", "github:MongLong0214/alpha")


def test_a_rate_limit_is_still_not_read_as_absence():
    # The important half: whatever kind of failure it is, it is not "the repository is free".
    from github_port import GhError, GhRateLimited

    gh = ScriptedGh([("api repos/", (1, "", "gh: API rate limit exceeded"))])
    with pytest.raises(GhError):  # GhRateLimited is a GhError, so callers that catch the base still stop
        GhCliPort(runner=gh).observe("repository", "github:MongLong0214/alpha")
    assert issubclass(GhRateLimited, GhError)


def test_a_ruleset_is_found_by_name_and_read_in_full():
    # The list response is a summary with no conditions or rules. A receipt digest has to say
    # what it pins, so the entry is re-read in full rather than digested from the summary.
    listed = json.dumps([{"id": 7, "name": "main-protection"}])
    full = json.dumps({"id": 7, "name": "main-protection", "target": "branch",
                       "enforcement": "active", "conditions": {"ref_name": {"include": ["refs/heads/main"]}},
                       "rules": [{"type": "pull_request"}]})
    gh = ScriptedGh([("api repos/MongLong0214/alpha/rulesets/7", (0, full, "")),
                     ("api repos/MongLong0214/alpha/rulesets", (0, listed, ""))])
    port = GhCliPort(runner=gh)

    observed = port.observe("ruleset", "github:MongLong0214/alpha#main-protection")

    assert observed["target"] == "branch" and observed["enforcement"] == "active"
    assert observed["rules"] == [{"type": "pull_request"}]


def test_a_ruleset_that_is_not_listed_reads_as_absent():
    gh = ScriptedGh([("api repos/MongLong0214/alpha/rulesets", (0, "[]", ""))])

    assert GhCliPort(runner=gh).observe("ruleset", "github:MongLong0214/alpha#main-protection") is None


def test_creating_a_ruleset_sends_the_body_on_stdin_with_the_name_from_the_identity():
    # The name is the only handle we have before the ruleset exists — an id is assigned by
    # GitHub — so the identity carries it and the body cannot disagree.
    gh = ScriptedGh([("api --method POST", (0, "", ""))])
    GhCliPort(runner=gh).create("ruleset", "github:MongLong0214/alpha#main-protection",
                                {"target": "branch", "enforcement": "active", "name": "ignored"})

    assert gh.seen[-1][-2:] == ["--input", "-"]
    assert json.loads(gh.stdin[-1])["name"] == "main-protection"


def test_a_ruleset_identity_without_a_name_is_refused():
    port = GhCliPort(runner=ScriptedGh([]))
    with pytest.raises(GhError, match="must name the ruleset"):
        port.observe("ruleset", "github:MongLong0214/alpha")
    with pytest.raises(GhError, match="must name the ruleset"):
        port.create("ruleset", "github:MongLong0214/alpha", {})


def test_an_issue_is_read_by_its_numeric_identity_in_the_plan_vocabulary():
    body = json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                       "number": 42, "id": 83042, "title": "Approved title",
                       "body": "Approved body", "state": "open"})
    port = GhCliPort(runner=(scripted := ScriptedGh([("issues/42", (0, body, ""))])))

    observed = port.observe("issue", "github:example/alpha#42")

    assert scripted.seen[-2] == ["gh", "api", "repos/example/alpha"]
    assert scripted.seen[-1] == ["gh", "api", "repos/example/alpha/issues/42"]
    assert observed == {"identity": "github:example/alpha#42", "resourceType": "issue",
                        "nodeId": 83042, "title": "Approved title", "body": "Approved body", "state": "open"}


@pytest.mark.parametrize("target", [
    {"repository_url": "https://api.github.com/repos/other/destination", "number": 7},
    {"repository_url": "https://api.github.com/repos/example/alpha", "number": 7},
    {"number": 42},
    {"repository_url": "https://api.github.com/repos/example/alpha"},
])
def test_transferred_or_unverified_issue_target_is_not_observed(target):
    body = json.dumps({"node_id": "I_destination", "title": "Approved title",
                       "body": "Approved body", "state": "open", **target})
    port = GhCliPort(runner=ScriptedGh([("issues/42", (0, body, ""))]))

    with pytest.raises(GhError, match="issue.*target"):
        port.observe("issue", "github:example/alpha#42")


def scoped_test_port(tmp_path, monkeypatch, runner, graphql):
    for key, value in {"RF_GITHUB_APP_ID": "123", "RF_GITHUB_APP_INSTALLATION_ID": "456",
                       "RF_GITHUB_APP_PRIVATE_KEY_PATH": str(tmp_path / "key.pem")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "key.pem").write_text("test-only key")
    calls = []
    def http(url, method, headers, body=None):
        calls.append((url, method, headers, body))
        if url.endswith("/access_tokens"):
            assert json.loads(body) == {"repository_ids": [17], "permissions": {"issues": "write"}}
            return {"token": "test-scoped-token", "repositories": [{"id": 17}],
                    "permissions": {"issues": "write"}}
        if url.endswith("/installation/repositories"):
            return {"total_count": 1, "repositories": [{"id": 17}]}
        assert url.endswith("/graphql")
        assert headers["Authorization"] == "Bearer test-scoped-token"
        return graphql(json.loads(body))
    return GhCliPort(runner=runner, http=http, app_signer=lambda *_: "test-jwt"), calls


def test_absent_app_configuration_never_falls_back_to_gh(tmp_path, monkeypatch):
    for name in ("RF_GITHUB_APP_ID", "RF_GITHUB_APP_INSTALLATION_ID", "RF_GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    gh = ScriptedGh([("issues/42", (0, json.dumps({
        "repository_url": "https://api.github.com/repos/example/alpha", "number": 42,
        "node_id": "I_original"}), ""))])
    port = GhCliPort(runner=gh, http=lambda *args: pytest.fail("HTTP attempted without App config"))
    port.observe("issue", "github:example/alpha#42")
    with pytest.raises(GhError, match="configured GitHub App"):
        port.update("issue", "github:example/alpha#42", {"title": "Approved"},
                    observed_node_id="I_original")
    assert gh.seen == [["gh", "api", "repos/example/alpha"],
                       ["gh", "api", "repos/example/alpha/issues/42"]]


@pytest.mark.parametrize("missing", ["RF_GITHUB_APP_ID", "RF_GITHUB_APP_INSTALLATION_ID",
                                     "RF_GITHUB_APP_PRIVATE_KEY_PATH"])
def test_partial_app_configuration_refuses_without_http(tmp_path, monkeypatch, missing):
    for key, value in {"RF_GITHUB_APP_ID": "123", "RF_GITHUB_APP_INSTALLATION_ID": "456",
                       "RF_GITHUB_APP_PRIVATE_KEY_PATH": str(tmp_path / "key.pem")}.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "key.pem").write_text("test-only key")
    monkeypatch.delenv(missing)
    port = GhCliPort(http=lambda *args: pytest.fail("HTTP attempted with incomplete App config"))
    with pytest.raises(GhError, match="configured GitHub App"):
        port._source_token(17)


@pytest.mark.parametrize("minted,visible", [
    ({"token": "scoped", "permissions": {"issues": "write"}}, None),
    ({"token": "scoped", "repositories": [{"id": 17}], "permissions": {"issues": "read"}}, None),
    ({"token": "scoped", "repositories": [{"id": 99}], "permissions": {"issues": "write"}}, None),
    ({"token": "scoped", "repositories": [{"id": 17}, {"id": 99}],
      "permissions": {"issues": "write"}}, None),
    ({"token": "scoped", "repositories": [{"id": 17}], "permissions": {"issues": "write"}},
     {"total_count": 2, "repositories": [{"id": 17}]}),
    ({"token": "scoped", "repositories": [{"id": 17}], "permissions": {"issues": "write"}},
     {"total_count": 1, "repositories": [{"id": 99}]}),
])
def test_unverified_app_token_scope_never_sends_graphql(tmp_path, monkeypatch, minted, visible):
    gh = ScriptedGh([("issues/42", (0, json.dumps({
        "repository_url": "https://api.github.com/repos/example/alpha", "number": 42,
        "node_id": "I_original"}), ""))])
    port, _ = scoped_test_port(tmp_path, monkeypatch, gh, lambda _: pytest.fail("GraphQL attempted"))
    calls = []
    def http(url, method, headers, body=None):
        calls.append(url)
        if url.endswith("/access_tokens"):
            return minted
        if url.endswith("/installation/repositories"):
            return visible
        pytest.fail("GraphQL attempted without verified source-only scope")
    port.http = http
    port.observe("issue", "github:example/alpha#42")
    with pytest.raises(GhError, match="scope|readback"):
        port.update("issue", "github:example/alpha#42", {"title": "Approved"},
                    observed_node_id="I_original")
    assert not any(url.endswith("/graphql") for url in calls)
    assert all("graphql" not in " ".join(argv) for argv in gh.seen)


def test_an_issue_update_writes_only_the_observed_plan_vocabulary(tmp_path, monkeypatch):
    scripted = ScriptedGh([("issues/42", (0, json.dumps({
        "repository_url": "https://api.github.com/repos/example/alpha", "number": 42,
        "node_id": "I_original", "title": "Old title"}), ""))])
    port, calls = scoped_test_port(tmp_path, monkeypatch, scripted,
                                   lambda payload: {"data": {"updateIssue": {"issue": {"id": "I_original"}}}})
    port.observe("issue", "github:example/alpha#42")

    port.update("issue", "github:example/alpha#42",
                {"title": "Approved title", "body": "Approved body", "state": "closed"},
                observed_node_id="I_original")

    assert calls[-1][0] == "https://api.github.com/graphql"
    assert all("test-scoped-token" not in " ".join(argv) for argv in scripted.seen)
    payload = json.loads(calls[-1][3])
    assert "updateIssue" in payload["query"]
    assert payload["variables"] == {"input": {"id": "I_original", "title": "Approved title",
                                               "body": "Approved body", "state": "CLOSED"}}


def test_issue_graphql_errors_refuse_the_update(tmp_path, monkeypatch):
    gh = ScriptedGh([("issues/42", (0, json.dumps({
        "repository_url": "https://api.github.com/repos/example/alpha", "number": 42,
        "node_id": "I_original", "title": "Old title"}), ""))])
    port, _ = scoped_test_port(tmp_path, monkeypatch, gh,
                               lambda payload: {"errors": [{"message": "refused"}], "data": None})
    port.observe("issue", "github:example/alpha#42")
    with pytest.raises(GhError, match="GraphQL"):
        port.update("issue", "github:example/alpha#42", {"title": "Approved title"},
                    observed_node_id="I_original")


def test_numeric_only_issue_id_is_not_usable_for_mutation():
    body = json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                       "number": 42, "id": 83042, "title": "Old title"})
    gh = ScriptedGh([("issues/42", (0, body, ""))])
    port = GhCliPort(runner=gh)
    observed = port.observe("issue", "github:example/alpha#42")
    with pytest.raises(GhError, match="node_id"):
        port.update("issue", "github:example/alpha#42", {"title": "Approved title"},
                    observed_node_id=observed["nodeId"])
    assert gh.seen == [["gh", "api", "repos/example/alpha"],
                       ["gh", "api", "repos/example/alpha/issues/42"]]


def test_number_retarget_between_observation_and_update_never_mutates_replacement(tmp_path, monkeypatch):
    identity = "github:example/alpha#42"
    operation = {"operationId": "update-issue:42", "resourceType": "issue", "intent": "update",
                 "resourceIdentity": identity, "desiredState": {"title": "Approved title"}}
    plan = {"bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
            "requestDigest": "sha256:" + "a" * 64, "authorization": "OWNER",
            "githubOperations": [operation]}
    state = {"current": "I_original", "original_title": "Old title", "replacement_title": "Other title",
             "mutation_ids": []}

    def run(argv, stdin=None):
        if argv == ["gh", "api", "repos/example/alpha"]:
            return 0, '{"id":17}', ""
        if argv == ["gh", "api", "repos/example/alpha/issues/42"]:
            observed = state["current"]
            if observed == "I_original":
                state["current"] = "I_replacement"  # deletion/recreation after the first GET
            title = state["original_title"] if observed == "I_original" else state["replacement_title"]
            return 0, json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                                  "number": 42, "node_id": observed, "title": title, "body": "", "state": "open"}), ""

        if "PATCH" in argv:
            state["replacement_title"] = json.loads(stdin)["title"]
            return 0, "{}", ""
        raise AssertionError(f"unexpected gh command: {argv}")

    book = ReceiptLedger(tmp_path / "receipts.json")
    def graphql(payload):
        target = payload["variables"]["input"]["id"]
        state["mutation_ids"].append(target)
        if target == "I_original":
            state["original_title"] = "Approved title"
        else:
            state["replacement_title"] = "Approved title"
        return {"data": {"updateIssue": {"issue": {"id": target}}}}
    port, _ = scoped_test_port(tmp_path, monkeypatch, run, graphql)
    with pytest.raises(ApplyError) as caught:
        apply_plan(plan, port, book, authorization=approval(plan))
    assert caught.value.code == REREAD_MISMATCH
    assert state["replacement_title"] == "Other title"
    assert state["mutation_ids"] == ["I_original"]
    assert book.get("update-issue:42") is None


def test_a_pull_request_is_not_mistaken_for_an_updatable_issue():
    body = json.dumps({"number": 42, "title": "PR", "pull_request": {"url": "https://example.invalid"}})
    port = GhCliPort(runner=ScriptedGh([("issues/42", (0, body, ""))]))

    with pytest.raises(GhError, match="pull request"):
        port.observe("issue", "github:example/alpha#42")


@pytest.mark.parametrize("ids", [{}, {"node_id": "", "id": None},
                                 {"node_id": "  ", "id": False}])
def test_an_issue_without_a_stable_remote_id_is_not_observable(ids):
    body = json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                       "number": 42, "title": "Approved title", "body": "Approved body",
                       "state": "open", **ids})
    port = GhCliPort(runner=ScriptedGh([("issues/42", (0, body, ""))]))

    with pytest.raises(GhError, match="stable.*id"):
        port.observe("issue", "github:example/alpha#42")


def test_recreated_issue_with_identical_state_is_not_resumed(tmp_path, monkeypatch):
    identity = "github:example/alpha#42"
    operation = {"operationId": "update-issue:42", "resourceType": "issue", "intent": "update",
                 "resourceIdentity": identity, "desiredState": {"title": "Approved title"}}
    plan = {"bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
            "requestDigest": "sha256:" + "a" * 64, "authorization": "OWNER",
            "githubOperations": [operation]}
    def issue(node_id):
        return json.dumps({"repository_url": "https://api.github.com/repos/example/alpha",
                           "number": 42, "node_id": node_id, "title": "Approved title",
                           "body": "Approved body", "state": "open"})

    scripted = ScriptedGh([("issues/42", (0, issue("I_original"), "")),
                           ("api graphql", (0, '{"data":{"updateIssue":{"issue":{"id":"I_original"}}}}', ""))])
    port, calls = scoped_test_port(tmp_path, monkeypatch, scripted,
                                   lambda payload: {"data": {"updateIssue": {"issue": {"id": "I_original"}}}})
    book = ReceiptLedger(tmp_path / "receipts.json")
    authorized = approval(plan)
    apply_plan(plan, port, book, authorization=authorized)
    scripted.responses = [("issues/42", (0, issue("I_recreated"), ""))]
    writes_before_resume = len([call for call in calls if call[0].endswith("/graphql")])

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan, port, ReceiptLedger(book.path), authorization=authorized)

    assert book.get(operation["operationId"])["resourceFingerprint"] == "I_original"
    assert caught.value.code == RESUMED_RESOURCE_DRIFTED
    assert caught.value.evidence["observed"] == "I_recreated"
    assert len([call for call in calls if call[0].endswith("/graphql")]) == writes_before_resume


def test_the_port_reads_the_security_posture_it_was_asked_about():
    """관측이 Plan 의 어휘로 나와야 한다. API 이름(`secret_scanning`)을 그대로 흘리면 재조회
    대조가 번역을 하나 거치고, 번역은 두 값이 다른데 같아 보이게 만들 수 있는 자리다."""
    body = json.dumps({
        "default_branch": "dev", "private": False, "node_id": "R_1",
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "disabled"},
            "dependabot_security_updates": {"status": "disabled"},
        },
    })
    port = GhCliPort(runner=ScriptedGh([("api repos/", (0, body, ""))]))

    observed = port.observe("setting", "github:MongLong0214/alpha#secret-scanning")

    # push protection 이 꺼진 것을 꺼졌다고 읽어야 한다. 상수를 돌려주면 이 Operation 은
    # 어떤 원격 상태에도 통과하고, 재조회가 아무것도 안 보는 칸이 된다.
    assert observed == {"identity": "github:MongLong0214/alpha#secret-scanning",
                        "resourceType": "setting",
                        "secretScanning": "enabled", "pushProtection": "disabled"}


def test_writing_the_security_posture_sends_the_api_its_own_names():
    port = GhCliPort(runner=(scripted := ScriptedGh([("api --method PATCH", (0, "{}", ""))])))

    port.update("setting", "github:MongLong0214/alpha#secret-scanning",
                {"secretScanning": "enabled", "pushProtection": "enabled"})

    assert scripted.seen[-1] == ["gh", "api", "--method", "PATCH", "repos/MongLong0214/alpha",
                                 "--input", "-"]
    assert json.loads(scripted.stdin[-1]) == {"security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"}}}


def test_a_setting_this_port_does_not_name_is_refused():
    port = GhCliPort(runner=ScriptedGh([]))

    with pytest.raises(GhError, match="settings this port observes"):
        port.observe("setting", "github:MongLong0214/alpha#whatever-else")


def test_the_port_reads_the_code_scanning_setup_it_was_asked_about():
    """별도 엔드포인트이고, 상수를 돌려주면 어떤 원격 상태에도 통과한다."""
    body = json.dumps({"state": "not-configured", "query_suite": "extended",
                       "languages": ["javascript", "python"]})
    port = GhCliPort(runner=(scripted := ScriptedGh([("code-scanning/default-setup", (0, body, ""))])))

    observed = port.observe("setting", "github:MongLong0214/alpha#code-scanning")

    assert scripted.seen[-1] == ["gh", "api", "repos/MongLong0214/alpha/code-scanning/default-setup"]
    # `languages` 는 관측에 들어오지 않는다. configured 뒤에 영원히 빈 배열이 되므로, 승인
    # 상태에 넣으면 재조회가 매번 실패한다.
    assert observed == {"identity": "github:MongLong0214/alpha#code-scanning",
                        "resourceType": "setting",
                        "state": "not-configured", "querySuite": "extended"}
