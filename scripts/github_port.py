#!/usr/bin/env python3
"""`gh` 로 뒷받침되는 GitHubPort 구현 (PRD §16).

`apply.py` 가 정한 규칙은 여기서 하나도 바뀌지 않는다. 이 파일이 하는 일은 두 개다 —
원격을 **읽고**, 계획된 것을 **만든다**. 무엇을 만들지, 이미 있으면 우리 것인지,
만든 뒤 확인됐는지는 전부 위 계층의 판단이다.

`observe` 가 이 파일의 중심이다. §16.2 는 쓰기 뒤 재조회를 요구하는데, 같은 함수가
쓰기 *전* preexisting 판정에도 쓰인다. 둘을 다른 코드로 두면 "만들기 전엔 없다고
했는데 만든 뒤엔 있다고 하는" 두 눈이 생기고, 그 불일치는 조용하다.

**신뢰 게이트 자격증명은 여기 오지 않는다**(PRD §26 Security). Repo Factory 는
오너의 평소 `gh` 인증으로 자기 저장소를 만들 뿐이고, `acp-production-gate` 를 게시할
수 있는 App 자격증명은 제어평면만 갖는다.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["GhError", "GhRateLimited", "GhCliPort", "parse_identity"]

Runner = Callable[..., Tuple[int, str, str]]


class GhError(RuntimeError):
    """`gh` 가 예상 밖으로 실패했다. 404 는 실패가 아니라 '없다' 이므로 여기 오지 않는다."""


class GhRateLimited(GhError):
    """제한에 걸렸다. 이것을 일반 실패와 섞으면 "기다리면 되는 일" 이 "고쳐야 하는 일" 로 읽힌다.

    실측(2026-08-19): 저장소 존재 확인은 없는 이름에 대해 404 를 만드는데, GitHub 은
    404 를 만드는 요청에 코어와 별도인 2차 제한을 건다. `rate_limit` 이 core 4954/5000
    을 보고하는 동안 같은 계정이 이 제한에 걸려 있었다. 즉 남은 코어 한도를 보고
    "괜찮다" 고 판단하면 틀린다."""


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

    def __init__(self, runner: Runner = None, gh: str = "gh"):
        self.run = runner or _default_runner
        self.gh = gh
        self.calls: List[List[str]] = []

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
        if resource_type == "branch":
            if not ref:
                raise GhError(f"branch identity must name a ref: {identity!r}")
            observed = self._api(f"repos/{owner}/{repo}/branches/{ref}")
            if observed is None:
                return None
            return {"identity": identity, "resourceType": "branch", "name": observed.get("name"),
                    "head": (observed.get("commit") or {}).get("sha")}
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
            }
        raise GhError(
            f"no observation is implemented for resourceType {resource_type!r}; "
            "an unobservable write cannot satisfy the post-write re-read (§16.2)"
        )

    def create(self, resource_type: str, identity: str, spec: Dict[str, Any]) -> None:
        owner, repo, ref = parse_identity(identity)
        if resource_type == "repository":
            visibility = "--public" if spec.get("visibility") == "public" else "--private"
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
        if resource_type == "branch":
            if not ref or not spec.get("fromSha"):
                raise GhError(f"branch creation needs a ref and a fromSha: {identity!r}")
            argv = [self.gh, "api", "--method", "POST", f"repos/{owner}/{repo}/git/refs",
                    "-f", f"ref=refs/heads/{ref}", "-f", f"sha={spec['fromSha']}"]
            self.calls.append(argv)
            code, _, err = self.run(argv)
            if code != 0:
                raise GhError(f"creating branch {ref} failed ({code}): {err.strip()[:200]}")
            return
        raise GhError(f"no creation is implemented for resourceType {resource_type!r}")
