#!/usr/bin/env python3
"""`gh` 로 뒷받침되는 GitHubPort 구현 (PRD §16).

`apply.py` 가 정한 규칙은 여기서 하나도 바뀌지 않는다. 이 파일이 하는 일은 세 개다 —
원격을 **읽고**, 계획된 것을 **만들고**, 이미 있는 리소스의 승인된 상태를 **갱신한다**. 무엇을
만들지·갱신할지, 이미 있으면 우리 것인지, 실행 뒤 확인됐는지는 전부 위 계층의 판단이다.

`observe` 가 이 파일의 중심이다. §16.2 는 쓰기 뒤 재조회를 요구하는데, 같은 함수가
쓰기 *전* preexisting 판정에도 쓰인다. 둘을 다른 코드로 두면 "만들기 전엔 없다고
했는데 만든 뒤엔 있다고 하는" 두 눈이 생기고, 그 불일치는 조용하다.

PRD §16.1(906행) 의 외부 쓰기 범위: `compile_plan` 은 repository,
setting(default-branch, secret-scanning, code-scanning), ruleset 을 계획하고 이 포트가
관측·적용한다. branch 는
`publish.py` 의 genesis push 가 만들고 `git ls-remote` 로 재조회한다. issue,
milestone, tag 는 genesis Plan 에 넣지 않는다. PRD §17.3(962행) 에 따라 활성화 뒤 제어평면의
GitHub integration kernel 이 이 저장소의 projection code(issue update port 포함)를
호출한다. `compile_plan` 은 이 세 리소스의 genesis-time 쓰기를 계획하지 않는다.

**신뢰 게이트 자격증명은 여기 오지 않는다**(PRD §26 Security). Repo Factory 는
오너의 평소 `gh` 인증으로 자기 저장소를 만들 뿐이고, `acp-production-gate` 를 게시할
수 있는 App 자격증명은 제어평면만 갖는다.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["GhError", "GhRateLimited", "GhCliPort", "parse_identity"]

Runner = Callable[..., Tuple[int, str, str]]


def _app_jwt(key_path: str, app_id: str) -> str:
    """Sign a short-lived App JWT without putting credentials in process arguments."""
    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    now = int(time.time())
    signing_input = (encode(b'{"alg":"RS256","typ":"JWT"}') + "." +
                     encode(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id},
                                       separators=(",", ":")).encode("utf-8")))
    try:
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_path, "-binary"],
                                   input=signing_input.encode("ascii"), capture_output=True,
                                   check=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        raise GhError("GitHub App JWT signing failed") from None
    return signing_input + "." + encode(signature)


class _NoAppRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _app_http(url: str, method: str, headers: Dict[str, str], body: Optional[str] = None) -> Dict[str, Any]:
    try:
        origin = urlsplit(url)
        if origin.scheme != "https" or origin.netloc != "api.github.com":
            raise ValueError("unexpected App API origin")
        request = Request(url, data=body.encode("utf-8") if body is not None else None,
                          headers=headers, method=method)
        with build_opener(_NoAppRedirect()).open(request, timeout=30) as response:
            result_url = urlsplit(response.geturl())
            if (result_url.scheme != "https" or result_url.netloc != "api.github.com"
                    or not 200 <= response.getcode() < 300):
                raise ValueError("unexpected App API response")
            payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise ValueError("App API response exceeds size limit")
            return json.loads(payload)
    except Exception:
        # HTTP errors may echo authorization material or payloads; do not propagate their text.
        raise GhError("scoped GitHub App request failed") from None


class GhError(RuntimeError):
    """`gh` 가 예상 밖으로 실패했다. 404 는 실패가 아니라 '없다' 이므로 여기 오지 않는다."""


class GhRateLimited(GhError):
    """제한에 걸렸다. 이것을 일반 실패와 섞으면 "기다리면 되는 일" 이 "고쳐야 하는 일" 로 읽힌다.

    실측(2026-08-19): 저장소 존재 확인은 없는 이름에 대해 404 를 만드는데, GitHub 은
    404 를 만드는 요청에 코어와 별도인 2차 제한을 건다. `rate_limit` 이 core 4954/5000
    을 보고하는 동안 같은 계정이 이 제한에 걸려 있었다. 즉 남은 코어 한도를 보고
    "괜찮다" 고 판단하면 틀린다."""


# 이 포트가 이름으로 다루는 저장소 설정들. 목록에 없는 이름은 거부한다 — 조용히 저장소 문서
# 전체를 설정으로 삼는 것보다 이름 하나가 빠졌다고 말하는 편이 낫다.
SETTINGS: Tuple[str, ...] = ("default-branch", "secret-scanning", "code-scanning")


def parse_identity(identity: str) -> Tuple[str, str, Optional[str]]:
    """`github:owner/repo` 또는 `github:owner/repo#ref` 를 쪼갠다.

    호스트 접두어를 요구한다. `owner/repo` 만 받으면 어느 forge 인지가 문자열 밖의
    합의가 되고, 그 합의는 어딘가에서 달라진다."""
    if not identity.startswith("github:"):
        raise GhError(f"unsupported identity {identity!r}; expected a github: prefix")
    rest = identity[len("github:"):]
    ref = None
    if "#" in rest:
        rest, ref = rest.split("#", 1)
    parts = rest.split("/")
    if len(parts) != 2 or not all(parts):
        raise GhError(f"malformed repository identity {identity!r}")
    return parts[0], parts[1], ref


def _default_runner(argv: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120, input=stdin)
    return done.returncode, done.stdout, done.stderr


class GhCliPort:
    """읽기와 생성만 한다. 판단은 `apply_plan` 이 갖는다."""

    def __init__(self, runner: Runner = None, gh: str = "gh", *, http=None, app_signer=None):
        self.run = runner or _default_runner
        self.gh = gh
        self.calls: List[List[str]] = []
        self.http = http or _app_http
        self.app_signer = app_signer or _app_jwt
        self._issue_sources: Dict[Tuple[str, str], int] = {}

    def _source_token(self, repository_id: int) -> str:
        """Requires RF_GITHUB_APP_ID, RF_GITHUB_APP_INSTALLATION_ID and RF_GITHUB_APP_PRIVATE_KEY_PATH."""
        app_id = os.environ.get("RF_GITHUB_APP_ID", "")
        installation_id = os.environ.get("RF_GITHUB_APP_INSTALLATION_ID", "")
        key_path = os.environ.get("RF_GITHUB_APP_PRIVATE_KEY_PATH", "")
        if (not app_id.isdecimal() or not installation_id.isdecimal() or
                not key_path or not Path(key_path).is_file()):
            raise GhError("issue update requires configured GitHub App ID, installation ID and key path")
        jwt = self.app_signer(key_path, app_id)
        response = self.http(f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                             "POST", {"Authorization": f"Bearer {jwt}",
                                      "Accept": "application/vnd.github+json",
                                      "Content-Type": "application/json"},
                             json.dumps({"repository_ids": [repository_id],
                                         "permissions": {"issues": "write"}}))
        if (not isinstance(response, dict) or not isinstance(response.get("token"), str)
                or not response["token"] or response.get("permissions", {}).get("issues") != "write"
                or not isinstance(response.get("repositories"), list)
                or [r.get("id") for r in response["repositories"] if isinstance(r, dict)] != [repository_id]
                or len(response["repositories"]) != 1):
            raise GhError("GitHub App token scope is not exactly the source repository with issues:write")
        token = response["token"]
        visible = self.http("https://api.github.com/installation/repositories", "GET",
                            {"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json"})
        if (not isinstance(visible, dict) or visible.get("total_count") != 1
                or not isinstance(visible.get("repositories"), list)
                or len(visible["repositories"]) != 1
                or not isinstance(visible["repositories"][0], dict)
                or visible["repositories"][0].get("id") != repository_id):
            raise GhError("GitHub App token repository readback differs from source repository")
        return token

    def _api(self, path: str) -> Optional[Dict[str, Any]]:
        argv = [self.gh, "api", path]
        self.calls.append(argv)
        code, out, err = self.run(argv)
        if code == 0:
            return json.loads(out)
        # 없음과 실패를 구분한다. 404 를 오류로 올리면 preexisting 판정이 매번 죽고,
        # 오류를 없음으로 읽으면 남의 저장소 위에 쓴다.
        if "404" in err or "Not Found" in err:
            return None
        if "rate limit" in err.lower() or "secondary rate" in err.lower():
            raise GhRateLimited(
                f"gh api {path} is rate limited; existence checks produce 404s and GitHub limits "
                f"those separately from the core quota. Wait and retry — this is not a defect. "
                f"({err.strip()[:120]})"
            )
        raise GhError(f"gh api {path} failed ({code}): {err.strip()[:200]}")

    def observe(self, resource_type: str, identity: str) -> Optional[Dict[str, Any]]:
        owner, repo, ref = parse_identity(identity)
        if resource_type == "repository":
            observed = self._api(f"repos/{owner}/{repo}")
            if observed is None:
                return None
            # 관측 전체를 digest 에 넣지 않는다. 저장소 문서에는 star 수처럼 매 초 변하는
            # 필드가 있고, 그것이 영수증의 afterStateDigest 를 매번 다르게 만든다.
            return {
                "identity": identity,
                "resourceType": "repository",
                "defaultBranch": observed.get("default_branch"),
                "private": observed.get("private"),
                "nodeId": observed.get("node_id"),
            }
        if resource_type == "setting":
            # 이름 붙은 설정만 다룬다. 이름을 안 대면 저장소 문서 전체가 설정이 되고, 그러면
            # 재조회 대조가 star 수처럼 매 초 변하는 필드까지 비교한다.
            if ref not in SETTINGS:
                raise GhError(
                    f"the settings this port observes are {sorted(SETTINGS)}: {identity!r}"
                )
            if ref == "code-scanning":
                # 별도 엔드포인트다. 저장소 문서에는 없다.
                setup = self._api(f"repos/{owner}/{repo}/code-scanning/default-setup")
                if setup is None:
                    return None
                # `languages` 는 **일부러 안 읽는다.** configured 전에는 GitHub 이 감지한 목록이고
                # configured 뒤에는 영원히 빈 배열이다(실측). 그것을 승인 상태에 넣으면 재조회가
                # "approved [...], observed []" 로 매번 실패한다 — bypass_actors 와 정반대
                # 방향의 같은 결함이다. 언어를 안 대도 GitHub 이 감지하므로 계획에도 안 넣는다.
                return {"identity": identity, "resourceType": "setting",
                        "state": setup.get("state"), "querySuite": setup.get("query_suite")}
            observed = self._api(f"repos/{owner}/{repo}")
            if observed is None:
                return None
            if ref == "default-branch":
                return {"identity": identity, "resourceType": "setting",
                        "defaultBranch": observed.get("default_branch")}
            # public 저장소에서는 이 둘이 기본 on 이다. 그래서 이 Operation 이 하는 일은
            # "켜는 것" 이 아니라 **꺼져 있으면 잡는 것** 이다 — Plan 이 승인한 보안 자세를
            # 원격이 실제로 갖고 있는지 재조회가 확인한다.
            analysis = observed.get("security_and_analysis") or {}
            return {"identity": identity, "resourceType": "setting",
                    "secretScanning": (analysis.get("secret_scanning") or {}).get("status"),
                    "pushProtection": (analysis.get("secret_scanning_push_protection") or {}).get("status")}
        if resource_type == "issue":
            if not ref or not ref.isdecimal():
                raise GhError(f"issue identity must name a numeric issue number: {identity!r}")
            source = self._api(f"repos/{owner}/{repo}")
            repository_id = source.get("id") if isinstance(source, dict) else None
            if (isinstance(repository_id, bool) or not isinstance(repository_id, int)
                    or repository_id <= 0):
                raise GhError(f"issue source repository has no immutable numeric ID: {identity!r}")
            observed = self._api(f"repos/{owner}/{repo}/issues/{ref}")
            if observed is None:
                return None
            if "pull_request" in observed:
                raise GhError(f"issue identity names a pull request, not an issue: {identity!r}")
            repository_url = observed.get("repository_url")
            if not isinstance(repository_url, str):
                raise GhError(f"issue target cannot be verified: {identity!r}")
            location = urlsplit(repository_url)
            if (location.scheme != "https" or location.netloc != "api.github.com"
                    or location.path.casefold() != f"/repos/{owner}/{repo}".casefold()
                    or location.query or location.fragment
                    or isinstance(observed.get("number"), bool)
                    or not isinstance(observed.get("number"), int)
                    or observed["number"] != int(ref)):
                raise GhError(f"issue target differs from requested identity: {identity!r}")
            node_id = observed.get("node_id")
            if not isinstance(node_id, str) or not node_id.strip():
                node_id = observed.get("id")
                if isinstance(node_id, bool) or not isinstance(node_id, int) or node_id <= 0:
                    raise GhError(f"issue has no stable node_id or id: {identity!r}")
            if isinstance(node_id, str):
                self._issue_sources[(identity, node_id)] = repository_id
            return {"identity": identity, "resourceType": "issue", "nodeId": node_id,
                    "title": observed.get("title"), "body": observed.get("body"),
                    "state": observed.get("state")}
        if resource_type == "ruleset":
            if not ref:
                raise GhError(f"ruleset identity must name the ruleset: {identity!r}")
            # 목록에서 이름으로 찾는다. 개별 GET 은 id 를 요구하는데 id 는 우리가 만들기
            # 전에는 없고, 이름은 Plan 이 정하는 것이므로 이름이 우리가 가진 유일한 손잡이다.
            listed = self._api(f"repos/{owner}/{repo}/rulesets")
            if listed is None:
                return None
            match = next((r for r in listed if r.get("name") == ref), None)
            if match is None:
                return None
            # 목록 응답은 요약이라 조건·규칙이 없다. 영수증의 digest 가 무엇을 고정하는지
            # 말할 수 있어야 하므로 전문을 다시 읽는다.
            full = self._api(f"repos/{owner}/{repo}/rulesets/{match['id']}")
            if full is None:
                return None
            return {
                "identity": identity,
                "resourceType": "ruleset",
                "name": full.get("name"),
                "target": full.get("target"),
                "enforcement": full.get("enforcement"),
                "conditions": full.get("conditions"),
                "rules": full.get("rules"),
                # 누가 이 ruleset 을 우회할 수 있는가. Plan 은 `bypass_actors: []` 를 승인한다 —
                # 아무도 우회하지 못한다는 주장이다. 그 필드를 안 읽으면 재조회가 그 주장을
                # 확인하지 못하고, 실제로는 `create-ruleset` 이 매번 REREAD_MISMATCH 로 죽었다:
                # 승인된 상태에 있는 키가 관측에 없으면 그것은 gap 이다.
                "bypass_actors": full.get("bypass_actors"),
            }
        raise GhError(
            f"no observation is implemented for resourceType {resource_type!r}; "
            "an unobservable write cannot satisfy the post-write re-read (§16.2)"
        )

    def create(self, resource_type: str, identity: str, spec: Dict[str, Any]) -> None:
        owner, repo, ref = parse_identity(identity)
        if resource_type == "repository":
            # Plan 의 어휘를 그대로 읽는다. 여기서 `visibility` 같은 다른 이름을 읽으면
            # Plan 이 말한 것과 실행되는 것 사이에 번역이 하나 들어가고, 번역은 Plan 이
            # private 이라고 적힌 채로 public 저장소가 만들어질 수 있는 자리다.
            visibility = "--private" if spec.get("private", True) else "--public"
            argv = [self.gh, "repo", "create", f"{owner}/{repo}", visibility]
            if spec.get("description"):
                argv += ["--description", str(spec["description"])]
            self.calls.append(argv)
            code, _, err = self.run(argv)
            if code != 0:
                raise GhError(f"gh repo create {owner}/{repo} failed ({code}): {err.strip()[:200]}")
            return
        if resource_type == "ruleset":
            if not ref:
                raise GhError(f"ruleset creation must name the ruleset: {identity!r}")
            body = dict(spec)
            body["name"] = ref
            argv = [self.gh, "api", "--method", "POST", f"repos/{owner}/{repo}/rulesets",
                    "--input", "-"]
            self.calls.append(argv)
            code, _, err = self.run(argv, json.dumps(body))
            if code != 0:
                raise GhError(f"creating ruleset {ref} failed ({code}): {err.strip()[:200]}")
            return
        raise GhError(f"no creation is implemented for resourceType {resource_type!r}")

    def update(self, resource_type: str, identity: str, spec: Dict[str, Any],
               *, observed_node_id: Optional[str] = None) -> None:
        owner, repo, ref = parse_identity(identity)
        if resource_type == "setting" and ref == "secret-scanning":
            # Plan 의 어휘를 그대로 읽는다. API 의 이름(`secret_scanning`)으로 읽으면 Plan 이
            # 말한 것과 실행되는 것 사이에 번역이 하나 끼고, 번역은 두 값이 다른데 같아 보이게
            # 만들 수 있는 자리다.
            body = {"security_and_analysis": {
                "secret_scanning": {"status": spec["secretScanning"]},
                "secret_scanning_push_protection": {"status": spec["pushProtection"]},
            }}
            argv = [self.gh, "api", "--method", "PATCH", f"repos/{owner}/{repo}", "--input", "-"]
            self.calls.append(argv)
            code, _, err = self.run(argv, json.dumps(body))
            if code != 0:
                raise GhError(f"setting secret scanning on {owner}/{repo} failed ({code}): {err.strip()[:200]}")
            return
        if resource_type == "setting" and ref == "code-scanning":
            body = {"state": spec["state"], "query_suite": spec["querySuite"]}
            argv = [self.gh, "api", "--method", "PATCH",
                    f"repos/{owner}/{repo}/code-scanning/default-setup", "--input", "-"]
            self.calls.append(argv)
            code, _, err = self.run(argv, json.dumps(body))
            if code != 0:
                raise GhError(f"configuring code scanning on {owner}/{repo} failed ({code}): {err.strip()[:200]}")
            return
        if resource_type == "setting" and ref == "default-branch":
            wanted = spec.get("defaultBranch")
            if not wanted:
                raise GhError(f"a default-branch update must name the branch: {identity!r}")
            argv = [self.gh, "api", "--method", "PATCH", f"repos/{owner}/{repo}",
                    "-f", f"default_branch={wanted}"]
            self.calls.append(argv)
            code, _, err = self.run(argv)
            if code != 0:
                raise GhError(f"setting the default branch of {owner}/{repo} failed ({code}): {err.strip()[:200]}")
            return
        if resource_type == "issue":
            if not ref or not ref.isdecimal():
                raise GhError(f"issue update must name a numeric issue number: {identity!r}")
            if not isinstance(observed_node_id, str) or not observed_node_id.strip():
                raise GhError(f"issue update requires an observed GraphQL node_id: {identity!r}")
            fields = {"title", "body", "state"}
            unknown = set(spec) - fields
            if unknown or not spec:
                raise GhError(f"issue updates accept only non-empty {sorted(fields)} state: {identity!r}")
            body = {field: spec[field] for field in fields if field in spec}
            if "state" in body and body["state"] not in ("open", "closed"):
                raise GhError(f"issue update has an unsupported state: {identity!r}")
            repository_id = self._issue_sources.pop((identity, observed_node_id), None)
            if repository_id is None:
                raise GhError(f"issue update requires source repository observation: {identity!r}")
            token = self._source_token(repository_id)
            # The approved name is part of the write target. A GraphQL node ID follows a
            # repository rename; this REST path must instead fail on an old-name 301.
            url = f"https://api.github.com/repos/{owner}/{repo}/issues/{ref}"
            headers = {"Authorization": f"Bearer {token}",
                       "Accept": "application/vnd.github+json",
                       "Content-Type": "application/json"}
            target = self.http(url, "GET", headers)
            if (not isinstance(target, dict) or target.get("node_id") != observed_node_id
                    or target.get("repository_url", "").casefold()
                    != f"https://api.github.com/repos/{owner}/{repo}".casefold()
                    or isinstance(target.get("number"), bool) or target.get("number") != int(ref)
                    or "pull_request" in target):
                raise GhError(f"issue update preflight target differs from observed issue: {identity!r}")
            reply = self.http(url, "PATCH", headers, json.dumps(body))
            if (not isinstance(reply, dict) or reply.get("node_id") != observed_node_id
                    or reply.get("repository_url", "").casefold()
                    != f"https://api.github.com/repos/{owner}/{repo}".casefold()
                    or isinstance(reply.get("number"), bool) or reply.get("number") != int(ref)
                    or "pull_request" in reply):
                raise GhError(f"issue update REST response was not verified: {identity!r}")
            return
        raise GhError(
            f"no update is implemented for resourceType {resource_type!r}; an unobservable or "
            "unwritable change cannot carry a verified receipt (§16.2)"
        )
