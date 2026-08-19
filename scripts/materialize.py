#!/usr/bin/env python3
"""계획된 파일을 실제 바이트로 만든다 (PRD §7 Phase E·G).

Plan 은 파일마다 `contentDigest` 를 갖는다. 그 digest 가 무엇의 digest 인지 말할 수
있어야 Plan 이 "무엇을 만들 것인가" 의 진술이 된다. 그래서 렌더링은 Apply 가 아니라
Plan 시점에 일어나고, Apply 는 이미 확정된 바이트를 쓴다.

생성 저장소에는 **프로젝트 진실만** 둔다(§4.6). 여기서 만드는 `AGENTS.md` 에
Hermes 가 누구인지, 현재 CTO session 이 무엇인지, provider 가 어떻게 되는지는
들어가지 않는다 — 그것은 제어평면의 관심사이고, 저장소에 적으면 두 곳에서 낡는다.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from render_ci import available_stacks, render

__all__ = ["materialize", "MANIFEST_PATH", "CI_PATH"]

MANIFEST_PATH = ".agent-control-plane/project.json"
CI_PATH = ".github/workflows/project-ci.yml"


def _readme(project_id: str, seed: str, commands: List[Dict[str, Any]]) -> str:
    lines = [
        f"# {project_id}",
        "",
        seed.strip(),
        "",
        "## Verification",
        "",
        "These are the commands CI runs and the control plane admits a candidate against.",
        "They are declared in `.agent-control-plane/project.json`; this list is a copy for",
        "readers, and the manifest is the contract.",
        "",
    ]
    lines += [f"- `{c['id']}` — `{' '.join(c['argv'])}`" for c in commands]
    lines += [
        "",
        "## Branches",
        "",
        "`main` carries release history. `dev` is the integration branch and the default.",
        "Work lands through `feature/*`, `task/*`, `fix/*`, `release/*` and `hotfix/*` with the",
        "bases and targets the manifest declares.",
        "",
    ]
    return "\n".join(lines)


def _agents(project_id: str, commands: List[Dict[str, Any]]) -> str:
    """프로젝트 로컬 정보만. 운영 정보는 제어평면이 갖는다."""
    lines = [
        f"# {project_id} — working rules",
        "",
        "Project-local only. Session identity, provider routing, review assignment and merge",
        "authority are not recorded here; they belong to the control plane and would rot in two",
        "places if copied.",
        "",
        "## Layout",
        "",
        "- `.agent-control-plane/project.json` — the portable project contract. Changing it is a",
        "  contract change and takes effect on the next run, never on the one judging it.",
        "- `.github/workflows/project-ci.yml` — publishes the `project-ci` check.",
        "",
        "## Verification",
        "",
    ]
    lines += [f"- `{c['id']}` — `{' '.join(c['argv'])}`" for c in commands]
    lines += [
        "",
        "## Rules",
        "",
        "- Do not weaken a verification command, a workflow, or the manifest in the same change",
        "  that those artifacts are judging. The control plane pins the previously approved",
        "  contract, so a self-weakening change is judged by the contract it tried to replace.",
        "- Branch from the base the manifest declares for that branch pattern, not from wherever",
        "  the tree happens to sit.",
        "",
    ]
    return "\n".join(lines)


def materialize(
    manifest: Dict[str, Any],
    *,
    seed: str,
    stack: Optional[str] = None,
    ci_values: Dict[str, str] = None,
) -> Dict[str, str]:
    """path → content. 스택을 모르면 CI 를 만들지 않는다(§14.1) — 조용히 Node 로 대체하지 않는다."""
    project_id = manifest["projectId"]
    commands = manifest["verificationCommands"]
    files = {
        MANIFEST_PATH: json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "README.md": _readme(project_id, seed, commands),
        "AGENTS.md": _agents(project_id, commands),
    }
    if stack is not None:
        if stack not in available_stacks():
            raise ValueError(
                f"no reviewed template for stack {stack!r}; available: {available_stacks()}"
            )
        files[CI_PATH] = render(stack, ci_values or {})
    return files
