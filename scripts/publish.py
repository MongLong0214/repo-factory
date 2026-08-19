#!/usr/bin/env python3
"""계획된 파일을 생성된 저장소에 올린다 (PRD §7 Phase G).

`apply.py` 가 원격 리소스를 만들고, 이 파일이 그 안에 바이트를 넣는다. 둘을 나눈
이유는 §16.3 이다 — 리소스 생성은 provenance 로 판정되는 멱등 연산이고, 파일 푸시는
그 판정이 끝난 뒤에만 일어난다.

브랜치 순서가 의도적이다. `main` 을 먼저 밀고 `dev` 를 만든 뒤 기본 브랜치를 `dev` 로
바꾼다. 반대로 하면 첫 푸시가 기본 브랜치를 `dev` 로 정해버리고, 그 뒤 `main` 을 만들
때까지 저장소에 release history 가 없는 창이 생긴다.

커밋에 세션 식별자를 남기지 않는다. 생성 저장소는 공개일 수 있고, 그 경우 트레일러는
저장소 안에 운영 정보를 넣는 §4.6 위반이 된다.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = ["PublishError", "publish_files", "remote_identity"]

_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def remote_identity(remote_url: str) -> Optional[str]:
    """`github:owner/name`, or None when the URL is not a GitHub remote we can read.

    None is "could not tell", not "does not match". The caller refuses on either, but the two
    are different facts and writing them as one value is how an unchecked destination reads as
    a checked one."""
    match = _REMOTE.match(remote_url.strip())
    if not match:
        return None
    return f"github:{match.group('owner')}/{match.group('repo')}"

Runner = "callable"


class PublishError(RuntimeError):
    """푸시가 끝나지 않았다. 어느 명령이 왜 실패했는지 함께 보고한다."""


def _run(argv: List[str], cwd: Path, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=180, env=env)
    return done.returncode, done.stdout, done.stderr


def publish_files(
    files: Dict[str, str],
    *,
    plan: Dict[str, object],
    repository_identity: str,
    workdir: Path,
    remote_url: str,
    author_name: str,
    author_email: str,
    message: str,
    default_branch: str = "dev",
    release_branch: str = "main",
    runner=_run,
) -> Dict[str, str]:
    """빈 저장소에 첫 커밋을 올리고 두 장수 브랜치를 세운다. 커밋 SHA 를 돌려준다.

    workdir 은 비어 있어야 한다. `git add -A` 는 거기 있는 것을 전부 담으므로, 남아 있던
    파일 하나가 genesis 커밋에 섞이면 Plan 의 contentDigest 집합이 실제로 착지한 바이트를
    더 이상 가리키지 않는다 — Plan 이 "무엇을 만들 것인가" 의 진술이 아니게 된다."""
    # 목적지를 먼저 본다. 계획된 저장소가 아닌 곳으로 밀면 계획된 집합을 정확히 올려도
    # 계획되지 않은 저장소가 하나 생긴다 — 경로 집합 검사는 그것을 못 본다.
    observed_identity = remote_identity(remote_url)
    if observed_identity is None:
        raise PublishError(
            f"the remote {remote_url!r} is not a GitHub URL this publisher can bind to the plan; "
            "it cannot tell whether the destination is the approved repository"
        )
    if observed_identity != repository_identity:
        raise PublishError(
            f"the remote resolves to {observed_identity} and the plan approved {repository_identity}"
        )

    planned_digests = {entry["path"]: entry["contentDigest"] for entry in plan["files"]}
    if set(planned_digests) != set(files):
        raise PublishError(
            "the file map does not match the plan's file list: "
            f"unplanned={sorted(set(files) - set(planned_digests))} "
            f"missing={sorted(set(planned_digests) - set(files))}"
        )

    if workdir.exists() and any(workdir.iterdir()):
        raise PublishError(
            f"publish target is not empty: {workdir}. The genesis commit must contain the planned "
            "set and nothing else, and `git add -A` cannot tell the difference."
        )
    workdir.mkdir(parents=True, exist_ok=True)
    for path, content in sorted(files.items()):
        target = workdir / path
        resolved = target.resolve()
        # 계획된 경로는 저장소 안에만 쓴다. `..` 은 plan 스키마가 이미 거부하지만, 쓰는
        # 쪽에서도 확인한다 — 계획을 만든 코드와 쓰는 코드가 같은 가정을 공유하면
        # 그 가정이 틀렸을 때 아무도 안 막는다.
        if not str(resolved).startswith(str(workdir.resolve()) + "/"):
            raise PublishError(f"planned path escapes the publish target: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_all(steps: List[List[str]]) -> None:
        for argv in steps:
            code, _, err = runner(argv, workdir)
            if code != 0:
                raise PublishError(f"{' '.join(argv[:3])} failed ({code}): {err.strip()[:300]}")

    run_all([
        ["git", "init", "-q", "-b", release_branch],
        ["git", "config", "user.name", author_name],
        ["git", "config", "user.email", author_email],
        # 서명·트레일러 훅이 이 커밋에 끼어들지 않게 한다. 생성 저장소의 첫 커밋은
        # 공장이 만든 정확한 바이트여야 하고, 훅이 덧붙인 것이어서는 안 된다.
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "--no-verify", "-m", message],
    ])

    # 푸시 **전에** 확인한다. 뒤에 두면 이미 올라간 것을 두고 "달랐다" 고 말하게 되고,
    # 그 시점의 원격은 계획되지 않은 바이트를 이미 담고 있다.
    code, out, err = runner(["git", "ls-files"], workdir)
    if code != 0:
        raise PublishError(f"could not list the committed set: {err.strip()[:200]}")
    committed = {line for line in out.splitlines() if line.strip()}
    planned = set(files)
    if committed != planned:
        raise PublishError(
            f"the genesis commit is not the planned set: "
            f"unplanned={sorted(committed - planned)} missing={sorted(planned - committed)}"
        )

    # 경로 집합이 같아도 바이트는 다를 수 있다. 전역 git filter, autocrlf, 훅 하나면
    # 커밋된 내용이 계획된 내용과 갈라지고 `ls-files` 는 그것을 모른다. 커밋 안의 바이트를
    # 그대로 읽어 Plan 의 contentDigest 와 맞춘다.
    drifted = []
    for path in sorted(planned_digests):
        code, blob, err = runner(["git", "show", f"HEAD:{path}"], workdir)
        if code != 0:
            raise PublishError(f"could not read {path} back out of the genesis commit: {err.strip()[:200]}")
        landed = "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
        if landed != planned_digests[path]:
            drifted.append(path)
    if drifted:
        raise PublishError(
            f"the committed bytes are not the planned bytes: {drifted}. The path set matched, so "
            "something rewrote content between the plan and the commit."
        )

    run_all([
        ["git", "branch", default_branch],
        ["git", "remote", "add", "origin", remote_url],
        ["git", "push", "-q", "origin", release_branch],
        ["git", "push", "-q", "origin", default_branch],
    ])

    code, out, err = runner(["git", "rev-parse", "HEAD"], workdir)
    if code != 0:
        raise PublishError(f"could not read the published head: {err.strip()[:200]}")
    head = out.strip()

    # 밀고 나서 원격을 다시 읽는다. push 의 exit 0 은 명령이 실패하지 않았다는 뜻이고,
    # 원격의 ref 가 이 커밋을 가리킨다는 뜻이 아니다 — 그건 원격에게 물어봐야 안다.
    code, listed, err = runner(["git", "ls-remote", "origin"], workdir)
    if code != 0:
        raise PublishError(f"could not re-read the remote after pushing: {err.strip()[:200]}")
    remote_heads = {}
    for line in listed.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            remote_heads[parts[1][len("refs/heads/"):]] = parts[0]
    disagreeing = sorted(branch for branch in (release_branch, default_branch)
                         if remote_heads.get(branch) != head)
    if disagreeing:
        raise PublishError(
            f"the remote does not carry the genesis commit on {disagreeing}: "
            f"expected {head}, observed {[remote_heads.get(b) for b in disagreeing]}"
        )

    return {"head": head, "branches": [release_branch, default_branch],
            "committedPaths": sorted(committed),
            "repositoryIdentity": repository_identity,
            "remoteHeads": {b: remote_heads.get(b) for b in (release_branch, default_branch)}}


def main(argv: List[str] = None) -> int:
    """계획된 바이트를 빈 저장소에 올린다.

    파일 집합은 Plan 에서 온다. 명령줄로 따로 주게 하면 승인된 Plan 이 무엇을 올릴지
    결정하지 못하고, 그 자리가 정확히 `specs` 가 effect 에 대해 했던 일이다."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Push an approved plan's file set as the genesis commit.")
    parser.add_argument("--plan", required=True, type=Path, help="compiler output carrying `files`")
    parser.add_argument("--workdir", required=True, type=Path, help="empty scratch directory to build the commit in")
    parser.add_argument("--remote-url", required=True, help="the created repository's git URL")
    parser.add_argument("--author-name", required=True)
    parser.add_argument("--author-email", required=True)
    parser.add_argument("--message", default="genesis: repository contract and verification",
                        help="the genesis commit subject; no session identifier is added (PRD §4.6)")
    parser.add_argument("--repository-identity", default=None,
                        help="which planned repository this push targets; inferred when the plan names one")
    parser.add_argument("--default-branch", default="dev")
    parser.add_argument("--release-branch", default="main")
    args = parser.parse_args(argv)

    document = json.loads(args.plan.read_text(encoding="utf-8"))
    files = document.get("files")
    core = document.get("planCore", document)
    if not isinstance(files, dict) or not files:
        print(json.dumps({"error": "the plan document carries no `files` map to publish"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    identity = args.repository_identity
    if identity is None:
        repositories = core.get("repositories") or []
        if len(repositories) != 1:
            print(json.dumps({"error": "--repository-identity is required when the plan names "
                                       f"{len(repositories)} repositories"},
                             ensure_ascii=False), file=sys.stderr)
            return 2
        identity = repositories[0]["identity"]
    try:
        heads = publish_files(
            files,
            plan=core,
            repository_identity=identity,
            workdir=args.workdir,
            remote_url=args.remote_url,
            author_name=args.author_name,
            author_email=args.author_email,
            message=args.message,
            default_branch=args.default_branch,
            release_branch=args.release_branch,
        )
    except PublishError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(heads, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
