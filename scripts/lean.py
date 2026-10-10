#!/usr/bin/env python3
"""lean — the Bootstrap CTO's Phase D verdict over one request (PRD §4.3, §7 Phase D).

Hermes writes the specification. The Bootstrap CTO cuts it to the smallest genesis that is still
production-ready, and may cut technical over-design only — never product scope or what the
profile requires. This command turns that judgement into the document `plan.py --lean-review`
consumes:

  --keep ITEM=CONSUMER   a requested optional artifact stays, and this is what consumes it
  --remove ITEM=REASON   an explicit cut, with its reason

A requested optional artifact the CTO does not keep is cut. The burden is on keeping: an
optional artifact is produced because something consumes it (PRD §6.1), so one with no named
consumer is the over-design this phase exists to remove.

A cut that reaches product scope or a required artifact is not the CTO's to make. It is written
as CEO_DECISION_REQUIRED, which `plan.py` refuses until Hermes decides. OWNER_DECISION_REQUIRED
has no rule here; the CTO writes it by hand when it finds an owner-only decision.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import (PRODUCT_SCOPE, PlanError, check_lean_review_shape, load_profile,  # noqa: E402
                  selected_artifacts, validate_lean_review, validate_request)

NO_CONSUMER = ("no consumer was named for it at genesis; an optional artifact is produced "
               "because something consumes it (PRD §6.1)")


def _rows(pairs: Sequence[Tuple[str, str]]) -> List[Dict[str, str]]:
    return [{"item": item, "reason": reason} for item, reason in sorted(pairs)]


def lean_review(request: Dict[str, Any], requested_optional: Sequence[str], *,
                keep: Sequence[Tuple[str, str]] = (),
                remove: Sequence[Tuple[str, str]] = ()) -> Dict[str, Any]:
    validate_request(request)
    profile = load_profile(request["bootstrapProfile"])
    # An option the profile does not offer is refused by name here, not quietly cut.
    selected_artifacts(profile, list(requested_optional))
    if any(item in PRODUCT_SCOPE or item in profile["required"] for item, _ in remove):
        review = {"verdict": "CEO_DECISION_REQUIRED", "removedItems": _rows(remove)}
        check_lean_review_shape(review)
        return review
    named = {item for item, _ in keep} | {item for item, _ in remove}
    cut = list(remove) + [(item, NO_CONSUMER) for item in sorted(set(requested_optional)) if item not in named]
    review = {"verdict": "LEAN_REVISE" if cut else "LEAN_ACCEPT",
              "removedItems": _rows(cut), "keptItems": _rows(keep)}
    # The consumer's own rules decide whether this verdict is usable, so the two cannot disagree:
    # keeping or cutting something not requested, naming an item twice, or a blank reason is
    # refused here rather than discovered by plan.py.
    validate_lean_review(review, profile, list(requested_optional))
    return review


def _pair(text: str) -> Tuple[str, str]:
    item, _, reason = text.partition("=")
    return item.strip(), reason.strip()


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the Bootstrap CTO's lean verdict over one request (PRD §7 Phase D).")
    parser.add_argument("--request", required=True, type=Path, help="BootstrapRequest JSON")
    parser.add_argument("--optional", nargs="*", default=[],
                        help="the optional artifacts the specification requests; pass the same list to plan.py")
    parser.add_argument("--keep", action="append", type=_pair, default=[], metavar="ITEM=CONSUMER",
                        help="keep a requested optional artifact and name what consumes it")
    parser.add_argument("--remove", action="append", type=_pair, default=[], metavar="ITEM=REASON",
                        help="cut an artifact with a reason; a cut into product scope or required "
                             "work is written as CEO_DECISION_REQUIRED")
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        review = lean_review(request, args.optional, keep=args.keep, remove=args.remove)
    except (OSError, PlanError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
