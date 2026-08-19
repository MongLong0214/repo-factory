#!/usr/bin/env python3
"""Canonical intent digest — 같은 의도는 같은 digest, 다른 의도는 다른 digest (PRD §8.3).

이 모듈이 답하는 질문은 "이 Plan 이 승인된 그 Plan 인가" 하나다. 그래서 관측이
아니라 **의도**만 digest 에 들어간다. `observedAt` 하나가 섞이면 같은 Plan 을 두 번
컴파일한 결과가 서로 다른 digest 를 갖고, Authorization 이 매번 무효가 된다.

규칙 (§8.3):
  UTF-8, ASCII escape 없음        비-ASCII 를 escape 하면 같은 문자열이 두 바이트열이 된다
  Object key 정렬                  키 순서는 의미가 아니다
  Array 순서 보존                  순서는 의미다 — merge_order 가 그 예다
  Newline 정규화 (\\r\\n, \\r → \\n)  같은 파일이 체크아웃 설정에 따라 달라지지 않게
  Volatile field 거부              아래 참조

Volatile 처리는 세 모드다. 기본은 **거부**다 — §8.3 은 "제거를 강제한다" 고 적지만,
PlanCore 에서 조용히 빼면 호출자가 넣은 것이 사라졌는데 성공으로 보인다. 넣지
말았어야 할 것을 이름과 함께 거부하는 편이 싸다.

`strip` 은 그 반대 방향의 요구를 위한 것이다. §8.3 은 "Timestamp 만 변경 →
PlanCore Digest 불변" 도 함께 요구하는데, `requestDigest` 는 `origin.requestedAt`
을 정당하게 갖는 BootstrapRequest 위에서 계산된다. 거부하면 요청을 digest 할 수
없고, 그대로 두면 같은 의도의 두 요청이 다른 Plan digest 를 만든다. 그러니 요청의
**의도**만 남기고 관측을 떼어내는 것이 답이고, 그것은 호출자가 명시적으로 고르는
동작이지 기본값이 아니다.

`allow` 는 EnvironmentObservation 처럼 관측이 본체인 문서를 위한 것이다.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, List, Tuple

__all__ = ["CanonicalError", "canonicalize", "digest", "volatile_findings"]


class CanonicalError(ValueError):
    """의도가 아닌 것이 PlanCore 에 들어왔다. 어느 경로의 무엇인지 함께 보고한다."""


# 키 이름으로 알아볼 수 있는 것들. §8.1 이 이름으로 나열한 네 부류 중 셋이다.
#
# camelCase 경계를 요구한다. 접미사만 보면 `candidate`·`update`·`format` 이 전부
# timestamp 로 걸리는데, `format` 은 이 저장소의 스키마에 이미 있는 정당한 키다.
# 넓은 정규식은 의도를 지키는 대신 의도를 쓸 수 없게 만든다.
_TIMESTAMP_KEY = re.compile(r"^(at|time|timestamp|date|datetime)$|[a-z0-9](At|Time|Timestamp|Date|DateTime)$")
_SESSION_KEY = re.compile(r"^session|[a-z0-9]Session")
_PROVIDER_KEY = re.compile(r"^(provider|quota|capacity|usage)|[a-z0-9](Provider|Quota|Capacity|Usage)")

# 네 번째 부류인 Absolute Path 는 키가 아니라 값의 성질이다.
_ABSOLUTE_VALUE = re.compile(r"^(/|~|[A-Za-z]:[\\/])")

_NEWLINES = re.compile(r"\r\n|\r")


def _volatile_kind(key: str) -> str:
    if _TIMESTAMP_KEY.search(key):
        return "timestamp"
    if _SESSION_KEY.search(key):
        return "session identity"
    if _PROVIDER_KEY.search(key):
        return "provider usage"
    return ""


def _normalize(value: Any, path: str, findings: List[Tuple[str, str]], strip: bool = False) -> Any:
    if isinstance(value, dict):
        out = {}
        for key in sorted(value):
            here = f"{path}.{key}" if path else key
            kind = _volatile_kind(key)
            if kind:
                findings.append((here, kind))
                if strip:
                    continue
            out[key] = _normalize(value[key], here, findings, strip)
        return out
    if isinstance(value, list):
        # 정렬하지 않는다. branchContracts 나 githubOperations 의 순서는 의도의 일부다.
        return [_normalize(item, f"{path}[{i}]", findings, strip) for i, item in enumerate(value)]
    if isinstance(value, str):
        if _ABSOLUTE_VALUE.match(value):
            findings.append((path, "absolute path"))
        return _NEWLINES.sub("\n", value)
    return value


def volatile_findings(value: Any) -> List[Tuple[str, str]]:
    """거부 없이 무엇이 걸리는지만 돌려준다. 진단과 강제를 같은 코드로 유지한다."""
    findings: List[Tuple[str, str]] = []
    _normalize(value, "", findings)
    return findings


VOLATILE_MODES = ("forbid", "strip", "allow")


def canonicalize(value: Any, *, volatile: str = "forbid") -> bytes:
    """정규 바이트열. `volatile` 은 위 세 모드 중 하나다."""
    if volatile not in VOLATILE_MODES:
        raise ValueError(f"volatile must be one of {VOLATILE_MODES}, got {volatile!r}")
    findings: List[Tuple[str, str]] = []
    normalized = _normalize(value, "", findings, strip=(volatile == "strip"))
    if volatile == "forbid" and findings:
        detail = "; ".join(f"{where or '<root>'} ({what})" for where, what in findings)
        raise CanonicalError(f"canonical intent must not carry volatile values: {detail}")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any, *, volatile: str = "forbid") -> str:
    return "sha256:" + hashlib.sha256(canonicalize(value, volatile=volatile)).hexdigest()
