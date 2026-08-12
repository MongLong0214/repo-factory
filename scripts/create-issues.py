#!/usr/bin/env python3
"""티켓 → GitHub 이슈 동기화 — Phase 4 레포 창세용 (v3).

issues.json 같은 별도 정본을 쓰지 않는다. 정본은 저장소 자신이다:
  docs/tickets/**/*.md  (repo-governance-ticket:v1 metadata)
  governance/policy.v1.json

각 이슈 body 에는 marker 가 박힌다:
  <!-- repo-governance-ticket:F1-001 -->

동작 (티켓별):
  marker 를 가진 이슈 0개 → create
  1개 → 내용 drift 시 sync(edit)
  2개 이상 → fail closed (exit 2) — 사람이 중복을 정리하기 전에는 아무것도 쓰지 않는다

규칙: rerun duplicate 0 · static label 만(risk:*, kind:*, epic:*) · 동적 status:* 금지
· write 후 API reread 로 검증(실패는 partial write 로 정확히 보고, exit 1)
· API outage 는 exit 2 · GitHub issue state 는 projection 일 뿐 readiness 입력이 아니다.

사용법:
  python3 create-issues.py --root /repo --dry-run
  python3 create-issues.py --root /repo --confirm-external-write

종료 코드: 0 성공 / 1 partial write · 검증 실패 / 2 사용법·스키마·API 오류
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
KERNEL_PATH = SKILL_ROOT / "templates" / "kit" / "scripts" / "governance.py"
ISSUE_MARKER = "<!-- repo-governance-ticket:{tid} -->"
MARKER_RE = re.compile(r"<!--\s*repo-governance-ticket:([A-Z][A-Z0-9]{0,7}-\d{3,4})\s*-->")
STATIC_LABEL_RE = re.compile(r"^(risk|kind|epic):[A-Za-z0-9._-]+$")


def load_kernel():
    spec = importlib.util.spec_from_file_location("rf_governance", KERNEL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def gh(args: list[str]) -> tuple[int, str]:
    binary = os.environ.get("REPO_GOVERNANCE_GH", "gh")
    result = subprocess.run([binary, *args], capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def gh_json_or_die(args: list[str], context: str):
    code, out = gh(args)
    if code:
        die(f"{context} 실패 (API outage 는 fail-closed) — {out.splitlines()[-1] if out else code}")
    try:
        return json.loads(out) if out else []
    except json.JSONDecodeError:
        die(f"{context} 응답이 JSON 이 아니다")


def issue_body(meta: dict, ticket_rel: str) -> str:
    tid = meta["id"]
    deps = ", ".join(meta.get("dependencies") or []) or "없음"
    lines = [
        ISSUE_MARKER.format(tid=tid),
        "",
        f"**kind**: {meta.get('kind')} · **risk**: {meta.get('risk')}",
        f"**의존성**: {deps}",
        f"**티켓 정본**: `{ticket_rel}`",
        "",
        "> 이 이슈는 projection 이다. readiness/merge 판정 입력이 아니다 —",
        "> 정본은 티켓 파일과 governance/policy.v1.json, 판정은 scripts/governance.py.",
    ]
    return "\n".join(lines)


def issue_labels(meta: dict) -> list[str]:
    labels = [f"kind:{meta.get('kind')}", f"risk:{meta.get('risk')}",
              f"epic:{str(meta.get('id', '')).split('-')[0]}"]
    for label in labels:
        if not STATIC_LABEL_RE.fullmatch(label):
            die(f"static label 규칙 위반: {label}")
        if label.startswith("status:"):
            die(f"동적 status:* label 금지: {label}")
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--confirm-external-write", action="store_true")
    try:
        args = parser.parse_args()
    except SystemExit as error:
        return 0 if error.code == 0 else 2

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        die(f"--root 디렉터리 없음: {root}")
    if not KERNEL_PATH.is_file():
        die(f"governance kernel 템플릿 없음: {KERNEL_PATH}")
    gov = load_kernel()

    problems, context = gov.validate_repo(root)
    if problems:
        problems.render(sys.stderr)
        die(f"contract 위반 {len(problems.items)}건 — 깨진 정본을 이슈로 투영하지 않는다")
    policy = context["policy"]
    repo = policy["repository"]["name"]
    tickets = context["tickets"]
    if not tickets:
        die("docs/tickets 에 티켓이 없다 — 동기화할 것이 없다")

    dry = args.dry_run
    if not dry:
        code, out = gh(["auth", "status"])
        if code:
            die(f"gh 인증 실패 — {out.splitlines()[0] if out else ''}")

    existing = gh_json_or_die(
        ["issue", "list", "-R", repo, "--state", "all", "--limit", "100000",
         "--json", "number,title,body,state"],
        "issue list",
    ) if not dry else []
    if dry:
        code, out = gh(["issue", "list", "-R", repo, "--state", "all", "--limit", "100000",
                        "--json", "number,title,body,state"])
        existing = json.loads(out) if not code and out else []

    by_marker: dict[str, list[dict]] = {}
    for issue in existing:
        for tid in MARKER_RE.findall(issue.get("body") or ""):
            by_marker.setdefault(tid, []).append(issue)

    duplicates = {tid: [i["number"] for i in issues]
                  for tid, issues in by_marker.items() if len(issues) > 1}
    if duplicates:
        die(f"marker 중복 이슈 — 수동 정리 전에는 쓰지 않는다: {duplicates}")

    created, synced, unchanged, failed = [], [], [], []
    wanted_labels: set[str] = set()
    plans = []
    for tid, ticket in sorted(tickets.items()):
        meta = ticket["meta"]
        rel = ticket["path"].resolve().relative_to(root).as_posix()
        title = f"{tid} · {meta.get('title')}"
        body = issue_body(meta, rel)
        labels = issue_labels(meta)
        wanted_labels.update(labels)
        match = by_marker.get(tid, [])
        if not match:
            plans.append(("create", tid, title, body, labels, meta.get("milestone"), None))
        else:
            issue = match[0]
            if issue.get("title") != title or (issue.get("body") or "").strip() != body.strip():
                plans.append(("sync", tid, title, body, labels, meta.get("milestone"), issue["number"]))
            else:
                unchanged.append(tid)

    if dry:
        for action, tid, title, *_rest in plans:
            print(f"DRY {action.upper():<6} {tid} | {title}")
        print(f"\ndry-run: create {sum(1 for p in plans if p[0] == 'create')} / "
              f"sync {sum(1 for p in plans if p[0] == 'sync')} / unchanged {len(unchanged)}")
        return 0

    for label in sorted(wanted_labels):
        code, out = gh(["label", "create", label, "-R", repo, "--color", "ededed", "--force"])
        if code:
            print(f"LABEL FAIL {label} — {out}", file=sys.stderr)

    for action, tid, title, body, labels, milestone, number in plans:
        if action == "create":
            argv = ["issue", "create", "-R", repo, "--title", title, "--body", body]
            for label in labels:
                argv += ["--label", label]
            if milestone:
                argv += ["--milestone", milestone]
            code, out = gh(argv)
            if code:
                failed.append((tid, out.splitlines()[-1] if out else "create 실패"))
                continue
            created.append(tid)
        else:
            argv = ["issue", "edit", str(number), "-R", repo, "--title", title, "--body", body]
            code, out = gh(argv)
            if code:
                failed.append((tid, out.splitlines()[-1] if out else "edit 실패"))
                continue
            synced.append(tid)
        time.sleep(0.2)  # secondary rate limit 회피

    # write 후 API reread — 못 본 것은 못 봤다고 말한다
    reread = gh_json_or_die(
        ["issue", "list", "-R", repo, "--state", "all", "--limit", "100000",
         "--json", "number,title,body,state"],
        "post-write reread",
    )
    reread_markers: dict[str, int] = {}
    for issue in reread:
        for tid in MARKER_RE.findall(issue.get("body") or ""):
            reread_markers[tid] = reread_markers.get(tid, 0) + 1
    missing_after = [tid for tid in tickets if reread_markers.get(tid, 0) == 0]
    duplicated_after = [tid for tid, n in reread_markers.items() if n > 1]

    print(f"create {len(created)} / sync {len(synced)} / unchanged {len(unchanged)} / fail {len(failed)}")
    if failed:
        for tid, reason in failed:
            print(f"PARTIAL {tid} — {reason}", file=sys.stderr)
    if missing_after:
        print(f"PARTIAL reread 에서 marker 미발견: {missing_after}", file=sys.stderr)
    if duplicated_after:
        print(f"PARTIAL reread 에서 marker 중복 생성: {duplicated_after}", file=sys.stderr)
    return 1 if (failed or missing_after or duplicated_after) else 0


if __name__ == "__main__":
    sys.exit(main())
