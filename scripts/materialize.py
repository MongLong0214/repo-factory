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

__all__ = ["materialize", "MANIFEST_PATH", "CI_PATH", "SKELETONS", "ARTIFACT_EVIDENCE",
           "SPEC_PATH", "ADR_PATH", "ACCEPTANCE_PATH", "ROLLBACK_PATH", "artifact_coverage"]

MANIFEST_PATH = ".agent-control-plane/project.json"
CI_PATH = ".github/workflows/project-ci.yml"
SPEC_PATH = "docs/PRD.md"
ADR_PATH = "docs/adr/ADR-0001-architecture.md"
ACCEPTANCE_PATH = "docs/ACCEPTANCE.md"
ROLLBACK_PATH = "docs/ROLLBACK.md"

# 프로파일이 요구하는 산출물마다, 그것이 만들어졌다고 말할 수 있는 근거가 무엇인지.
# 이 표가 없으면 `required` 목록은 이름의 나열이고 아무것도 강제하지 않는다 — 오늘
# 그 상태였다.
ARTIFACT_EVIDENCE = {
    "portable-project-manifest": ("file", MANIFEST_PATH),
    "readme": ("file", "README.md"),
    "agents": ("file", "AGENTS.md"),
    "stack-specific-ci": ("file", CI_PATH),
    "compact-prd-or-equivalent-specification": ("file", SPEC_PATH),
    "architecture-adr": ("file", ADR_PATH),
    "acceptance-oracle": ("file", ACCEPTANCE_PATH),
    "rollback-strategy": ("file", ROLLBACK_PATH),
    "verification-commands": ("manifest", "verificationCommands"),
    "branch-contract": ("manifest", "branchProfile"),
    "security-command": ("security", None),
    # 저장소 파일이 아니라 인계 문서다. 여기서 파일로 찾으면 영원히 미충족이 된다.
    "repo-factory-result": ("handoff", None),
    # 아직 만들지 못하는 것들. 표에서 빼면 "근거가 없다" 와 "아직 안 만든다" 가 같은
    # 모양이 되고, 전자는 결함이고 후자는 계획이다.
    "prd": ("unimplemented", None),
    "adr": ("unimplemented", None),
    "tickets": ("unimplemented", None),
    "issue-projection": ("unimplemented", None),
    "research-dossier": ("unimplemented", None),
    "adversarial-evidence-search": ("unimplemented", None),
    "reproduction-experiment": ("unimplemented", None),
    "measurement-preregistration": ("unimplemented", None),
    "provenance-evidence": ("unimplemented", None),
}


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


def artifact_coverage(artifacts, files: Dict[str, str], manifest: Dict[str, Any]):
    """(충족, 미충족). 미충족은 조용히 넘어가지 않고 unresolvedGaps 로 올라간다."""
    satisfied, missing = [], []
    for artifact in artifacts:
        kind, target = ARTIFACT_EVIDENCE.get(artifact, ("unknown", None))
        if kind == "file":
            ok = target in files
        elif kind == "manifest":
            ok = bool(manifest.get(target))
        elif kind == "security":
            ok = any("security" in c["id"] for c in manifest.get("verificationCommands", []))
        elif kind == "handoff":
            ok = True
        elif kind == "unimplemented":
            ok = False
        else:
            ok = False
        (satisfied if ok else missing).append(artifact)
    return satisfied, missing


def _spec(project_id: str, seed: str, commands) -> str:
    return "\n".join([
        f"# {project_id} — specification", "",
        "## Goal", "", seed.strip(), "",
        "## Non-goals", "",
        "- Anything the goal above does not name. A non-goal added later is a scope change and",
        "  belongs in a decision record, not here.", "",
        "## Acceptance", "",
        "The project is acceptable when every verification command below passes at the exact",
        "head being judged, and the acceptance oracle in `docs/ACCEPTANCE.md` holds.", "",
    ] + [f"- `{c['id']}` — `{' '.join(c['argv'])}`" for c in commands] + [""])


def _adr(project_id: str, stack: Optional[str]) -> str:
    return "\n".join([
        f"# ADR-0001 — {project_id} architecture", "",
        "- **Status:** Accepted", "",
        "## Context", "",
        "A genesis decision record exists so the first structural choice is written where it can",
        "be argued with, rather than inferred later from the code that resulted from it.", "",
        "## Decision", "",
        f"The project is built on the `{stack or 'unresolved'}` toolchain, verified by the commands the",
        "portable manifest declares, and integrated through `dev` with releases cut to `main`.", "",
        "## Consequences", "",
        "Changing the toolchain or the branch contract is a contract change: it takes effect on",
        "the next run, never on the run judging it.", "",
    ])


def _acceptance(project_id: str, commands) -> str:
    return "\n".join([
        f"# {project_id} — acceptance oracle", "",
        "An oracle states what would have to be observed for the project to be wrong. Listing",
        "what should pass is not an oracle; anything passes when nothing is checked.", "",
        "## Must stay red", "",
        "- A verification command that exits zero without executing the project's tests.",
        "- A candidate that weakens a verification command in the same change that command judges.",
        "- A release tag that does not point at the exact merge commit on `main`.", "",
        "## Must stay green", "",
    ] + [f"- `{c['id']}` at the exact candidate head" for c in commands] + [""])


def _rollback(project_id: str) -> str:
    return "\n".join([
        f"# {project_id} — rollback", "",
        "## What can be undone", "",
        "- A merge to `dev` is reverted by a revert commit through the same gate that admitted it.",
        "- A release on `main` is withdrawn by cutting a new release; the tag is never moved,",
        "  because a moved tag makes every earlier reference to it a lie.", "",
        "## What cannot", "",
        "- A published package version. Withdraw by publishing a superseding version.",
        "- A repository made public. Making it private again does not un-observe it.", "",
    ])


def _python_skeleton(project_id: str) -> Dict[str, str]:
    module = project_id.replace("-", "_")
    return {
        "pyproject.toml": (
            '[project]\n'
            f'name = "{project_id}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            '\n[build-system]\n'
            'requires = ["setuptools>=68"]\n'
            'build-backend = "setuptools.build_meta"\n'
            '\n[tool.setuptools]\n'
            f'py-modules = ["{module}"]\n'
        ),
        f"{module}.py": 'def greet(who: str) -> str:\n    return f"hello, {who}"\n',
        f"tests/test_{module}.py": (
            f'from {module} import greet\n\n\n'
            'def test_greet_names_its_argument():\n'
            '    assert greet("world") == "hello, world"\n'
        ),
    }


def _go_skeleton(project_id: str) -> Dict[str, str]:
    return {
        "go.mod": f"module github.com/MongLong0214/{project_id}\n\ngo 1.22\n",
        "greet.go": ('package main\n\nimport "fmt"\n\n'
                     'func Greet(who string) string { return fmt.Sprintf("hello, %s", who) }\n\n'
                     'func main() { fmt.Println(Greet("world")) }\n'),
        "greet_test.go": ('package main\n\nimport "testing"\n\n'
                          'func TestGreetNamesItsArgument(t *testing.T) {\n'
                          '\tif got := Greet("world"); got != "hello, world" {\n'
                          '\t\tt.Fatalf("got %q", got)\n\t}\n}\n'),
    }


def _rust_skeleton(project_id: str) -> Dict[str, str]:
    return {
        "Cargo.toml": f'[package]\nname = "{project_id.replace("-", "_")}"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": ('pub fn greet(who: &str) -> String { format!("hello, {who}") }\n\n'
                       '#[cfg(test)]\nmod tests {\n    use super::greet;\n\n'
                       '    #[test]\n    fn greet_names_its_argument() {\n'
                       '        assert_eq!(greet("world"), "hello, world");\n    }\n}\n'),
    }


def _node_skeleton(project_id: str) -> Dict[str, str]:
    """`npm install` 과 `npm test` 가 실제로 무언가를 하는 최소 골격.

    골격 없이 CI 를 만들면 워크플로는 렌더되는데 첫 실행이 빨간색이고, 그 빨간색은
    프로젝트의 문제가 아니라 공장이 저장소를 반만 만들었다는 뜻이다."""
    package = {
        "name": project_id,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "main": "index.js",
        "scripts": {"test": "node --test"},
    }
    return {
        "package.json": json.dumps(package, indent=2, sort_keys=True) + "\n",
        "index.js": "export const greet = (who) => `hello, ${who}`;\n",
        "test/smoke.test.js": (
            'import { strictEqual } from "node:assert";\n'
            'import { test } from "node:test";\n'
            '\n'
            'import { greet } from "../index.js";\n'
            '\n'
            'test("greet names its argument", () => {\n'
            '  strictEqual(greet("world"), "hello, world");\n'
            '});\n'
        ),
    }


SKELETONS = {"node": _node_skeleton, "python": _python_skeleton,
             "go": _go_skeleton, "rust": _rust_skeleton}


def materialize(
    manifest: Dict[str, Any],
    *,
    seed: str,
    stack: Optional[str] = None,
    ci_values: Dict[str, str] = None,
    artifacts: Optional[list] = None,
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
        skeleton = SKELETONS.get(stack)
        if skeleton is not None:
            files.update(skeleton(project_id))

    # 프로파일이 요구한 것만 만든다. §6.1 이 금지하는 것은 형식 충족용 문서 생성이므로,
    # SIMPLE 에 ADR 을 끼워 넣지 않는다.
    wanted = set(artifacts or [])
    if "compact-prd-or-equivalent-specification" in wanted:
        files[SPEC_PATH] = _spec(project_id, seed, commands)
    if "architecture-adr" in wanted:
        files[ADR_PATH] = _adr(project_id, stack)
    if "acceptance-oracle" in wanted:
        files[ACCEPTANCE_PATH] = _acceptance(project_id, commands)
    if "rollback-strategy" in wanted:
        files[ROLLBACK_PATH] = _rollback(project_id)
    return files
