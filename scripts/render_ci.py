#!/usr/bin/env python3
"""Stack-specific CI 렌더러와 no-placeholder 게이트 (PRD §14.1).

§14.1 은 Apply 된 저장소에 남아 있으면 실패인 것을 다섯 가지로 나열한다. 그 다섯은
전부 "초록인데 아무것도 검증하지 않는 CI" 의 서로 다른 모양이다.

  PLACEHOLDER_ECHO        런타임 셋업 자리에 echo
  UNRESOLVED_TOKEN        치환되지 않은 {{TOKEN}}
  IMPLICIT_RUNTIME        setup action 없이 runner 기본 런타임에 기댐
  MISSING_INSTALL         의존성 설치 단계 없음
  UNPINNED_ACTION         full SHA 가 아닌 tag

게이트는 렌더된 결과에 대고 돈다. 템플릿을 검사하면 템플릿은 통과하는데 저장소에는
플레이스홀더가 남는 경우를 못 잡는다 — 검사가 자기 대상을 안 갖는 그 형태다.

Unknown stack 은 Node 로 조용히 대체하지 않는다(§14.1). 대체하면 그 저장소의 CI 는
설치하지 않은 런타임 위에서 아무것도 아닌 것을 검증한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates" / "ci"

# `${{ … }}` 는 GitHub Actions 표현식이고 치환 대상이 아니다.
_TOKEN = re.compile(r"(?<!\$)\{\{\s*([A-Z_]+)\s*\}\}")
_USES = re.compile(r"uses:\s*(\S+)@(\S+)")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SETUP_ACTION = re.compile(r"uses:\s*(actions/setup-|dtolnay/rust-toolchain)")
_ECHO_ONLY_STEP = re.compile(r"run:\s*echo\b[^\n]*$", re.MULTILINE)

__all__ = ["CiRenderError", "available_stacks", "render", "ci_findings", "required_tokens"]


class CiRenderError(ValueError):
    """스택을 모르거나 값이 모자란다. 어느 쪽인지 이름과 함께 보고한다."""


def available_stacks() -> List[str]:
    return sorted(p.stem for p in TEMPLATES.glob("*.yml"))


def required_tokens(stack: str) -> List[str]:
    return sorted(set(_TOKEN.findall(_template(stack))))


def _template(stack: str) -> str:
    path = TEMPLATES / f"{stack}.yml"
    if not path.is_file():
        raise CiRenderError(
            f"no reviewed template for stack {stack!r}; available: {available_stacks()}. "
            "An unknown stack requires a reviewed custom template and is never defaulted to Node (PRD §14.1)."
        )
    return path.read_text(encoding="utf-8")


def render(stack: str, values: Dict[str, str]) -> str:
    """토큰을 값으로 바꾸고, 모자란 값은 빈 문자열이 아니라 실패로 만든다."""
    text = _template(stack)
    needed = set(_TOKEN.findall(text))
    missing = sorted(needed.difference(values))
    if missing:
        raise CiRenderError(f"{stack}: no value supplied for {missing}")
    empty = sorted(name for name in needed if not str(values[name]).strip())
    if empty:
        raise CiRenderError(f"{stack}: empty value for {empty}; an empty command is a lane that verifies nothing")
    return _TOKEN.sub(lambda m: str(values[m.group(1)]), text)


def ci_findings(rendered: str) -> List[Tuple[str, str]]:
    """§14.1 의 다섯 조건. 빈 목록이면 이 워크플로는 저장소에 남아도 된다."""
    findings: List[Tuple[str, str]] = []

    for token in sorted(set(_TOKEN.findall(rendered))):
        findings.append(("UNRESOLVED_TOKEN", token))

    if not _SETUP_ACTION.search(rendered):
        findings.append(("IMPLICIT_RUNTIME", "no setup action; the job would use whatever the runner ships"))

    if "install dependencies" not in rendered:
        findings.append(("MISSING_INSTALL", "no dependency install step"))

    for action, ref in _USES.findall(rendered):
        if not _FULL_SHA.match(ref):
            findings.append(("UNPINNED_ACTION", f"{action}@{ref}"))

    # `echo` 로만 이뤄진 step 은 그 자리에서 무엇도 하지 않는다. 집계 job 의 진단 출력과
    # 구분하기 위해, setup 이나 install 을 자칭하는 step 안에 있을 때만 잡는다.
    for block in re.split(r"\n\s*- ", rendered):
        names = re.search(r"name:\s*(.+)", block)
        label = names.group(1).lower() if names else ""
        if ("setup" in label or "install" in label) and _ECHO_ONLY_STEP.search(block):
            findings.append(("PLACEHOLDER_ECHO", label.strip()))

    return findings


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a stack CI workflow and gate it against PRD §14.1.")
    parser.add_argument("--stack", required=True)
    parser.add_argument("--values", required=True, type=Path, help="JSON object of token values")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        rendered = render(args.stack, json.loads(args.values.read_text(encoding="utf-8")))
    except CiRenderError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1

    findings = ci_findings(rendered)
    if findings:
        print(json.dumps({"findings": [{"code": c, "detail": d} for c, d in findings]},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
