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

CommitLore init·doctor 는 클론마다 실행한다. genesis 관측은 원격이 설정된 이 로컬
저장소가 두 명령을 받아들이는지만 증명한다. 클론의 활성화는 클론에서 실행할 단계다.
클론에 전달되는 계약은 매니페스트의 `commitlore.mode` 와 AGENTS.md 이며 hook 과
로컬 git 설정은 전달되지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical import digest  # noqa: E402
from plan import load_profile  # noqa: E402

__all__ = ["PublishError", "CommitLoreRefusal", "publish_files", "publish_receipt",
           "remote_identity"]

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


class CommitLoreRefusal(PublishError):
    """프로파일 정책에 따라 genesis 게시를 멈춘다."""

    def __init__(self, observation: Dict[str, object]):
        self.observation = observation
        label = ("bootstrap revision required" if observation["outcome"] == "REVISE"
                 else "blocking decision memory refusal")
        super().__init__(f"COMMITLORE_{observation['outcome']}: {label}: {observation['detail']}")


def _run(argv: List[str], cwd: Path, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=180, env=env)
    return done.returncode, done.stdout, done.stderr


def publish_receipt(plan: Dict[str, object], heads: Dict[str, object], *, clock) -> Dict[str, object]:
    """genesis 푸시도 영수증을 남긴다.

    남기지 않으면 `after-files` 가 "파일이 이미 올라갔는가" 를 물을 곳이 없다. 순서를
    주석으로만 적어두면 호출자가 `after-files` 를 먼저 부를 수 있고, `project-ci` 를
    요구하는 ruleset 이 그 워크플로보다 먼저 존재하면 저장소에 내용을 넣는 바로 그 푸시가
    거부된다."""
    identity = str(heads["repositoryIdentity"])
    at = clock()
    observed = {"head": heads["head"], "remoteHeads": dict(heads["remoteHeads"])}
    return {
        "bootstrapOperationId": plan["bootstrapOperationId"],
        "requestDigest": plan["requestDigest"],
        "operationId": f"publish:{identity}",
        "resourceType": "genesis-commit",
        "resourceIdentity": identity,
        "preexisting": False,
        "beforeStateDigest": None,
        # 이 영수증의 "쓰고 나서 읽은 상태" 는 원격이 실제로 가리키는 커밋이다. 다른 영수증과
        # 같은 자리에 같은 이름으로 둔다 — 원장이 행마다 다른 모양을 요구하면, 어떤 행이
        # 무엇을 증명하는지를 읽는 쪽이 매번 다시 판단해야 한다.
        "afterStateDigest": digest(observed, volatile="allow"),
        "committedPaths": list(heads["committedPaths"]),
        "branches": list(heads["branches"]),
        "head": heads["head"],
        "remoteHeads": dict(heads["remoteHeads"]),
        "createdAt": at,
        "rereadAt": at,
        "verified": True,
        "commitlore": dict(heads["commitlore"]),
    }


def observe_commitlore(profile: str, workdir: Path, runner) -> Dict[str, object]:
    """Genesis 뒤, 원격 push 앞에 실제 로컬 저장소의 Decision Memory를 확인한다."""
    on_failure = load_profile(profile)["commitlore"]["onFailure"]
    warnings: List[str] = []
    report_status: Optional[str] = None
    for argv in (["commitlore", "init", "--mcp-scope", "none", "--no-unattended"],
                 ["commitlore", "doctor", "--json"]):
        try:
            code, out, err = runner(argv, workdir)
        except subprocess.TimeoutExpired as error:
            return {"outcome": on_failure, "scope": "genesis-checkout", "warnings": warnings,
                    "detail": f"{' '.join(argv[:2])} timed out after {error.timeout}s"}
        except OSError as error:
            return {"outcome": on_failure, "scope": "genesis-checkout", "warnings": warnings,
                    "detail": f"{argv[0]} unavailable: {error}"}
        if argv[1] == "doctor":
            try:
                report = json.loads(out)
                if not isinstance(report, dict) or report.get("schema") != "commitlore_doctor.v2":
                    raise ValueError("invalid schema")
                report_status = report.get("status")
                if report_status not in ("ok", "degraded", "failed"):
                    raise ValueError("invalid status")
                checks = report["checks"]
                if not isinstance(checks, list) or not all(
                    isinstance(check, dict) and check.get("status") in
                    ("ok", "warn", "fail", "skipped") for check in checks
                ):
                    raise ValueError("invalid checks")
                for check in checks:
                    if check["status"] != "ok":
                        if check["status"] == "skipped":
                            reason = check.get("skipReason") or check.get("title") or check.get("detail")
                            message = f"skipped ({reason})" if reason else "skipped"
                        else:
                            message = check.get("detail") or check.get("title") or check["status"]
                        warnings.append(f"{check['id']}: {message}" if check.get("id") else str(message))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                return {"outcome": on_failure, "scope": "genesis-checkout", "warnings": [],
                        "detail": f"commitlore doctor returned an invalid JSON report: {error}"}
            if code == 0:
                contradictions = []
                if report_status == "failed":
                    contradictions.append("report status is failed")
                if any(check["status"] == "fail" for check in checks):
                    contradictions.append("a check has status fail")
                if "exitCode" in report and (type(report["exitCode"]) is not int or
                                             report["exitCode"] != 0):
                    contradictions.append(f"report exitCode is {report['exitCode']!r}")
                if contradictions:
                    return {"outcome": on_failure, "scope": "genesis-checkout",
                            "warnings": warnings, "reportStatus": report_status,
                            "detail": ("commitlore doctor " + "; ".join(contradictions) +
                                       " while the process exited 0")}
        if code != 0:
            detail = (err.strip() or out.strip() or "no diagnostic output")[:300]
            return {"outcome": on_failure, "scope": "genesis-checkout", "warnings": warnings,
                    **({"reportStatus": report_status} if report_status is not None else {}),
                    "detail": f"{' '.join(argv[:2])} failed ({code}): {detail}"}
    diagnostic = "\n".join(part for part in (out.strip(), err.strip()) if part)
    return {"outcome": "PASS", "scope": "genesis-checkout", "warnings": warnings,
            "reportStatus": report_status,
            "detail": f"commitlore doctor passed: {diagnostic[-300:]}"}


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
    ])

    commitlore = observe_commitlore(str(plan["bootstrapProfile"]), workdir, runner)
    # init 는 hook/index 만 배치해야 한다. 추적 파일이나 새 저장소 파일을 만졌다면
    # genesis 뒤의 로컬 트리가 계획된 파일 집합과 달라진 것이므로 게시하지 않는다.
    code, status, err = runner(["git", "status", "--porcelain", "--untracked-files=all"], workdir)
    if code != 0 or status.strip():
        raise PublishError(f"CommitLore changed the generated checkout or its state could not be read: "
                           f"{(status.strip() or err.strip())[:300]}")
    if commitlore["outcome"] in ("REVISE", "BLOCK"):
        raise CommitLoreRefusal(commitlore)

    run_all([
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
            "remoteHeads": {b: remote_heads.get(b) for b in (release_branch, default_branch)},
            "commitlore": commitlore}



def _remote_heads(workdir: Path, remote_url: str) -> Optional[Dict[str, str]]:
    """원격이 지금 가리키는 브랜치들. 읽지 못하면 `None` — "못 읽었다" 와 "비어 있다" 는 다르다."""
    scratch = workdir.parent / f"{workdir.name}.lsremote"
    scratch.mkdir(parents=True, exist_ok=True)
    code, listed, _ = _run(["git", "ls-remote", "--heads", remote_url], scratch)
    if code != 0:
        return None
    heads = {}
    for line in listed.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            heads[parts[1][len("refs/heads/"):]] = parts[0]
    return heads


def main(argv: List[str] = None) -> int:
    """계획된 바이트를 빈 저장소에 올린다.

    파일 집합은 Plan 에서 온다. 명령줄로 따로 주게 하면 승인된 Plan 이 무엇을 올릴지
    결정하지 못하고, 그 자리가 정확히 `specs` 가 effect 에 대해 했던 일이다."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Push an approved plan's file set as the genesis commit.")
    parser.add_argument("--plan", required=True, type=Path, help="compiler output carrying `files`")
    parser.add_argument("--authorization", required=True, type=Path,
                        help="approval receipt covering the plan core, from scripts/authorize.py")
    parser.add_argument("--workdir", required=True, type=Path, help="empty scratch directory to build the commit in")
    parser.add_argument("--remote-url", required=True, help="the created repository's git URL")
    parser.add_argument("--author-name", required=True)
    parser.add_argument("--author-email", required=True)
    parser.add_argument("--message", default="genesis: repository contract and verification",
                        help="the genesis commit subject; no session identifier is added (PRD §4.6)")
    parser.add_argument("--ledger", type=Path, default=None,
                        help="receipt ledger to record the genesis push into; after-files needs it")
    parser.add_argument("--repository-identity", default=None,
                        help="which planned repository this push targets; inferred when the plan names one")
    parser.add_argument("--default-branch", default="dev")
    parser.add_argument("--release-branch", default="main")
    args = parser.parse_args(argv)

    document = json.loads(args.plan.read_text(encoding="utf-8"))
    core = document.get("planCore", document)
    from apply import ApplyError, _check_authorization

    try:
        authorization = json.loads(args.authorization.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"cannot read the approval receipt: {error}", file=sys.stderr)
        return 2
    try:
        _check_authorization(core, authorization)
    except ApplyError as error:
        print(json.dumps({"error": error.code, "message": str(error), "evidence": error.evidence},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    files = document.get("files")
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
    # 이미 밀었는가. `apply` 에는 재개 이야기가 있는데 genesis 푸시에는 없었다 — 완료된
    # 부트스트랩을 다시 돌리면 원격이 앞서 있어서 `git push` 가 거부하고, 그 거부가 이름
    # 없는 git 오류로 그대로 올라왔다. 원장은 이 질문에 답할 수 있는 자리다.
    ledger = None
    if args.ledger is not None:
        from apply import ReceiptLedger

        try:
            ledger = ReceiptLedger(args.ledger)
            ledger.assert_owner(core["bootstrapOperationId"], core["requestDigest"])
        except ApplyError as error:
            print(json.dumps({"error": error.code, "message": str(error), "evidence": error.evidence},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        prior = ledger.get(f"publish:{identity}")
        if prior is not None and prior.get("verified"):
            observation = prior.get("commitlore")
            policy = load_profile(core["bootstrapProfile"])["commitlore"]["onFailure"]
            if (not isinstance(observation, dict) or
                    observation.get("outcome") not in ("PASS", policy) or
                    observation.get("scope") != "genesis-checkout" or
                    (observation.get("outcome") != "PASS" and not observation.get("detail"))):
                print(json.dumps({"error": "COMMITLORE_MISSING_OR_INVALID: a prior genesis receipt "
                                           "cannot resume without an explicit CommitLore outcome; "
                                           "the observation may predate the scoped shape",
                                  "commitlore": observation}, ensure_ascii=False), file=sys.stderr)
                return 1
            landed = sorted(prior.get("committedPaths") or [])
            if landed != sorted(files):
                # 같은 저장소에 이미 다른 파일 집합이 착지해 있다. 두 번째 genesis 는 없다.
                print(json.dumps({"error": "this repository already carries a genesis commit from "
                                           "a different file set; a second genesis is not a resume",
                                  "landed": landed, "planned": sorted(files)},
                                 ensure_ascii=False), file=sys.stderr)
                return 1
            # 영수증이 과거의 푸시를 말한다. 지금 원격이 거기 있는지는 다시 읽어서 본다.
            current = _remote_heads(Path(args.workdir), args.remote_url)
            if current is not None and current == dict(prior["remoteHeads"]):
                print(json.dumps({k: prior[k] for k in
                                  ("head", "remoteHeads", "committedPaths", "branches")}
                                 | {"repositoryIdentity": identity, "resumed": True,
                                    "commitlore": observation},
                                 ensure_ascii=False, indent=2))
                return 0
            print(json.dumps({"error": "the ledger records a genesis push that the remote no longer "
                                       "matches; the repository moved since the receipt was written",
                              "receipt": prior["remoteHeads"], "remote": current},
                             ensure_ascii=False), file=sys.stderr)
            return 1

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
        payload = {"error": str(error)}
        if isinstance(error, CommitLoreRefusal):
            payload["commitlore"] = error.observation
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    if ledger is not None:
        from datetime import datetime, timezone

        def now() -> str:
            return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        ledger.record(publish_receipt(core, heads, clock=now))
    print(json.dumps(heads, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
