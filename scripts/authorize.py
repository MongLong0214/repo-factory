#!/usr/bin/env python3
"""승인자가 Plan 하나를 승인하는 문서를 만든다 (PRD §16.1).

이 명령이 `apply` 와 **다른 명령인 것이 요점이다.** 승인이 Plan 안의 필드였을 때는
그 필드를 고치고 다시 digest 한 Plan 이 스스로를 승인한 것과 구별되지 않았다. 영수증은
어떤 digest 를 승인했는지 말하므로, Plan 이 한 바이트라도 바뀌면 그 승인은 더 이상 이
Plan 을 가리키지 않는다.

서명이 아니다. 이 파일을 쓸 수 있는 사람은 승인을 주장할 수 있다. 사는 것은 주장이
별개의 아티팩트가 되고, 행위자·시각·묶인 digest 를 갖고, `apply` 가 눈앞의 Plan 과
대조할 수 있다는 것이다.

통합 구성에서는 이 문서를 제어평면(Hermes/CEO)이 만든다. 이 명령은 그 자리가 비어
있는 동안 — 로컬 도그푸드와 검증 — 같은 모양의 문서를 만드는 경로다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply import AUTHORITY_RANK, ApplyError, authorized_plan_receipt  # noqa: E402


def _plan_core(document: dict) -> dict:
    """컴파일러 출력이든 planCore 든 받는다. 승인 대상은 `planCore` 다."""
    return document["planCore"] if "planCore" in document else document


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Issue an approval receipt over one compiled plan.")
    parser.add_argument("--plan", required=True, type=Path,
                        help="compiler output, or a plan core; the receipt binds to its digest")
    parser.add_argument("--authority", required=True, choices=sorted(AUTHORITY_RANK),
                        help="the authority this approval carries")
    parser.add_argument("--actor", required=True,
                        help="who approved, e.g. owner:isaac or hermes:ceo")
    parser.add_argument("--approved-at", default=None,
                        help="RFC 3339 UTC; defaults to now")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--binding-generation", default=None, type=int)
    parser.add_argument("--source-receipt", default=None,
                        help="an upstream receipt this approval derives from")
    args = parser.parse_args(argv)

    try:
        document = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"cannot read the plan: {error}", file=sys.stderr)
        return 2
    plan = _plan_core(document)
    if "bootstrapOperationId" not in plan:
        print("the plan carries no bootstrapOperationId; an approval must name the operation "
              "it approves", file=sys.stderr)
        return 2

    approved_at = args.approved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        receipt = authorized_plan_receipt(
            plan, authority=args.authority, actor=args.actor, approved_at=approved_at,
            session_id=args.session_id, binding_generation=args.binding_generation,
            source_receipt=args.source_receipt)
    except ApplyError as error:
        print(json.dumps({"error": error.code, "message": str(error)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
