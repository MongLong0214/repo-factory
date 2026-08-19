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

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = ["PublishError", "publish_files"]

Runner = "callable"


class PublishError(RuntimeError):
    """푸시가 끝나지 않았다. 어느 명령이 왜 실패했는지 함께 보고한다."""


def _run(argv: List[str], cwd: Path, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=180, env=env)
    return done.returncode, done.stdout, done.stderr


def publish_files(
    files: Dict[str, str],
    *,
    workdir: Path,
    remote_url: str,
    author_name: str,
    author_email: str,
    message: str,
    default_branch: str = "dev",
    release_branch: str = "main",
    runner=_run,
) -> Dict[str, str]:
    """빈 저장소에 첫 커밋을 올리고 두 장수 브랜치를 세운다. 커밋 SHA 를 돌려준다."""
    workdir.mkdir(parents=True, exist_ok=True)
    for path, content in sorted(files.items()):
        target = workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    steps: List[List[str]] = [
        ["git", "init", "-q", "-b", release_branch],
        ["git", "config", "user.name", author_name],
        ["git", "config", "user.email", author_email],
        # 서명·트레일러 훅이 이 커밋에 끼어들지 않게 한다. 생성 저장소의 첫 커밋은
        # 공장이 만든 정확한 바이트여야 하고, 훅이 덧붙인 것이어서는 안 된다.
        ["git", "config", "commit.gpgsign", "false"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "--no-verify", "-m", message],
        ["git", "branch", default_branch],
        ["git", "remote", "add", "origin", remote_url],
        ["git", "push", "-q", "origin", release_branch],
        ["git", "push", "-q", "origin", default_branch],
    ]
    for argv in steps:
        code, _, err = runner(argv, workdir)
        if code != 0:
            raise PublishError(f"{' '.join(argv[:3])} failed ({code}): {err.strip()[:300]}")

    code, out, err = runner(["git", "rev-parse", "HEAD"], workdir)
    if code != 0:
        raise PublishError(f"could not read the published head: {err.strip()[:200]}")
    return {"head": out.strip(), "branches": [release_branch, default_branch]}
