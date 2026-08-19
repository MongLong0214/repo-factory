"""The gh-backed GitHubPort: what it reads, what it builds, and what it refuses."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from apply import OWNER_AUTHORIZATION_REQUIRED, ApplyError, ReceiptLedger, apply_plan  # noqa: E402
from github_port import GhCliPort, GhError, parse_identity  # noqa: E402


class ScriptedGh:
    """argv → (exit, stdout, stderr). 네트워크 없이 정확한 명령 구성을 검사한다."""

    def __init__(self, responses):
        self.responses = responses
        self.seen: List[List[str]] = []

    def __call__(self, argv: List[str]) -> Tuple[int, str, str]:
        self.seen.append(argv)
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


def test_public_is_passed_through_only_when_the_spec_says_so():
    gh = ScriptedGh([("repo create", (0, "", ""))])
    GhCliPort(runner=gh).create("repository", "github:MongLong0214/alpha", {"visibility": "public"})

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
        port.observe("ruleset", "github:MongLong0214/alpha")


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
                              "intent": "create", "resourceIdentity": "github:MongLong0214/alpha"}],
    }

    class NeverCalled:
        def observe(self, *_):
            raise AssertionError("nothing may be observed before authorization is settled")

        def create(self, *_):
            raise AssertionError("nothing may be created under an insufficient authorization")

    with pytest.raises(ApplyError) as caught:
        apply_plan(plan, NeverCalled(), ReceiptLedger(tmp_path / "r.json"))

    assert caught.value.code == OWNER_AUTHORIZATION_REQUIRED
    assert caught.value.evidence["repositories"] == ["github:MongLong0214/alpha"]


def test_the_same_plan_under_owner_authorisation_proceeds(tmp_path):
    plan = {
        "bootstrapOperationId": "11111111-2222-3333-4444-555555555555",
        "requestDigest": "sha256:" + "a" * 64,
        "authorization": "OWNER",
        "repositories": [{"role": "primary", "identity": "github:MongLong0214/alpha", "visibility": "public"}],
        "githubOperations": [{"operationId": "create-repository:alpha", "resourceType": "repository",
                              "intent": "create", "resourceIdentity": "github:MongLong0214/alpha"}],
    }

    class Fake:
        def __init__(self):
            self.state = {}

        def observe(self, _t, identity):
            return self.state.get(identity)

        def create(self, _t, identity, spec):
            self.state[identity] = {"identity": identity}

    result = apply_plan(plan, Fake(), ReceiptLedger(tmp_path / "r.json"))

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
