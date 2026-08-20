"""A profile's `required` list only means something if something checks it (PRD §6)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from materialize import (  # noqa: E402
    ACCEPTANCE_PATH, ADR_PATH, ARTIFACT_EVIDENCE, CI_PATH, ROLLBACK_PATH, SKELETONS, SPEC_PATH,
    artifact_coverage,
)
from plan import compile_plan, load_profile  # noqa: E402
from render_ci import effective_values  # noqa: E402

VER = [{"id": "test", "argv": ["npm", "test"], "repositoryRole": "primary", "cwd": ".",
        "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "deny", "required": True}]
SECURITY = {"id": "security", "argv": ["npm", "audit", "--audit-level=high"], "repositoryRole": "primary",
            "cwd": ".", "timeoutSeconds": 600, "envAllowlist": ["CI"], "network": "allowlist", "required": True}
CI = {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22", "INSTALL_CMD": "npm install",
      "TEST_CMD": "npm test", "BUILD_CMD": "node --check index.js"}


def compiled(profile: str, commands=None, **kwargs):
    request = {"schema": "repo-factory.bootstrap-request.v1", "runId": "r", "seed": "a demo project",
               "bootstrapProfile": profile, "priority": "NORMAL",
               "repositories": [{"role": "primary", "name": "demo"}],
               "visibility": "public", "origin": {"channel": "cli"}}
    return compile_plan(request, commands or VER, stack="node", ci_values=CI,
                        operation_id="11111111-2222-3333-4444-555555555555", **kwargs)


def test_every_artifact_any_profile_requires_has_a_stated_evidence():
    # An artifact with no entry is uncheckable, so it would sit in `required` forever while
    # nothing looked for it — the state this whole file exists to end.
    for profile in ["simple", "standard", "guarded"]:
        data = load_profile(profile)
        for artifact in data["required"] + data["optional"]:
            if artifact in ARTIFACT_EVIDENCE:
                continue
            pytest.fail(f"{profile}: {artifact} has no stated evidence in ARTIFACT_EVIDENCE")


def test_simple_and_standard_are_fully_covered():
    for profile in ["SIMPLE", "STANDARD"]:
        assert compiled(profile)["unresolvedGaps"] == [], profile


def test_guarded_is_covered_because_the_factory_plans_a_security_control():
    """GUARDED 는 호출자의 툴체인에 기대지 않는다.

    한동안 `security-command` 는 호출자가 대는 검증 명령으로만 충족됐고, 그래서 GUARDED 는
    **한 번도 부트스트랩된 적이 없었다** — genesis 시점에 돌 수 있는 내장 보안 명령이 없다.
    실측: `npm audit` 은 lockfile 을 요구하고(`ENOLOCK`) 갓 만든 저장소에는 없다. 의존성이
    하나도 없는 트리에 의존성 감사를 거는 것이 애초에 맞지 않았다.

    저장소가 보안 통제를 갖는다는 것은 공장이 계획하고 재조회로 확인할 수 있는 사실이다."""
    assert compiled("GUARDED")["unresolvedGaps"] == []


def test_security_is_uncovered_when_nothing_plans_a_control():
    """계획한 통제도 없고 호출자가 댄 명령도 없으면 충족되지 않는다.

    이게 없으면 `security-command` 는 무엇을 넣어도 통과하는 칸이 된다 — 요구 목록에 이름만
    있고 아무것도 안 보는 상태로 돌아간다."""
    manifest = {"verificationCommands": [{"id": "test"}]}

    _, missing = artifact_coverage(["security-command"], {}, manifest, security_controls=[])
    assert missing == ["security-command"]

    covered, _ = artifact_coverage(["security-command"], {}, manifest,
                                   security_controls=["enable-secret-scanning:demo"])
    assert covered == ["security-command"]


def test_a_profile_only_gets_the_documents_it_asks_for():
    # §6.1 — producing a document to satisfy a count is a defect, so SIMPLE has no ADR.
    simple, guarded = compiled("SIMPLE")["files"], compiled("GUARDED", commands=VER + [SECURITY])["files"]

    for path in [SPEC_PATH, ADR_PATH, ACCEPTANCE_PATH, ROLLBACK_PATH]:
        assert path not in simple, f"SIMPLE should not carry {path}"
        assert path in guarded, f"GUARDED should carry {path}"
    assert SPEC_PATH in compiled("STANDARD")["files"]
    assert ADR_PATH not in compiled("STANDARD")["files"]


def test_a_missing_file_is_reported_rather_than_assumed():
    manifest = compiled("STANDARD")["projectManifest"]
    _, missing = artifact_coverage(["readme", "agents"], {"README.md": "x"}, manifest)

    assert missing == ["agents"]


def test_coverage_reads_the_manifest_for_contracts_that_are_not_files():
    # `verification-commands` and `branch-contract` live inside the manifest. Looking for a
    # file named after them would make them permanently missing.
    manifest = compiled("SIMPLE")["projectManifest"]
    satisfied, missing = artifact_coverage(["verification-commands", "branch-contract"], {}, manifest)

    assert missing == [] and len(satisfied) == 2


def test_the_handoff_document_is_not_looked_for_in_the_repository():
    # `repo-factory-result` is produced at handoff. Treating it as a repository file would
    # leave every profile permanently short by one.
    _, missing = artifact_coverage(["repo-factory-result"], {}, {})

    assert missing == []


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_every_default_stack_has_a_runnable_skeleton(stack: str):
    # A workflow with nothing to run is a red first CI that blames the project for the
    # factory having built half a repository.
    assert stack in SKELETONS
    files = SKELETONS[stack]("demo-project", effective_values(stack), "demo-owner")
    assert files, stack
    # Rust puts unit tests inside the module under `#[cfg(test)]`, so a path check would call a
    # conventional Rust crate untested. What matters is that something asserts.
    assert any("assert" in body or "test" in path.lower() for path, body in files.items()), \
        f"{stack} skeleton has nothing that tests"


@pytest.mark.parametrize("stack", ["python", "go", "rust"])
def test_a_stack_with_a_skeleton_reports_no_skeleton_gap(stack: str):
    request = {"schema": "repo-factory.bootstrap-request.v1", "runId": "r", "seed": "demo",
               "bootstrapProfile": "SIMPLE", "priority": "NORMAL",
               "repositories": [{"role": "primary", "name": "demo"}],
               "visibility": "public", "origin": {"channel": "cli"}}
    values = {"RUNTIME_LOWER": "1", "RUNTIME_LATEST": "2", "INSTALL_CMD": "true",
              "TEST_CMD": "true", "BUILD_CMD": "true"}
    result = compile_plan(copy.deepcopy(request), VER, stack=stack, ci_values=values,
                         operation_id="11111111-2222-3333-4444-555555555555")

    assert not any("skeleton" in gap for gap in result["unresolvedGaps"])


# --- 생성물은 자기 CI 와 같은 사실을 말한다 -------------------------------------------

# 생성 프로젝트가 런타임 하한을 선언하는 자리. 없으면 이 스택은 약속을 안 한 것이고,
# 그건 검사할 것이 없다는 뜻이지 통과가 아니다.
# 기본값과 반드시 다른 하한. 같으면 하드코딩을 구별하지 못한다.
OVERRIDDEN_LOWER = {"python": "3.10", "go": "1.21", "rust": "1.75", "node": "18"}

RUNTIME_DECLARATIONS = {
    "python": ("pyproject.toml", r'requires-python = ">=([^"]+)"'),
    "go": ("go.mod", r"^go (\S+)$"),
    "rust": ("Cargo.toml", r'rust-version = "([^"]+)"'),
    "node": ("package.json", r'"node":\s*">=([^"]+)"'),
}


@pytest.mark.parametrize("stack", sorted(RUNTIME_DECLARATIONS))
def test_the_skeleton_declares_the_runtime_the_ci_matrix_actually_runs(stack: str):
    """공장이 만든 프로젝트가 공장이 만든 CI 와 모순되면 안 된다.

    실측: 생성된 `pyproject.toml` 이 `requires-python = ">=3.11"` 을 선언했고 CI 매트릭스
    하한은 3.9 였다. 생성 저장소의 **첫 CI 가 빨간색**이었다 —
    `Package 'rf-dogfood-c-python' requires a different Python: 3.9.25 not in '>=3.11'`.
    두 값이 서로를 모르는 두 곳에서 나왔기 때문이고, 그것을 확인하는 것이 없었다.

    `references/dogfooding-loop.md` §1 이 이름까지 붙여 경고하는 결함을 공장이 생성하고
    있었다: 런타임 하한은 약속이고, 확인하는 것이 없으면 거짓말이 된다.
    """
    import re

    path, pattern = RUNTIME_DECLARATIONS[stack]
    # 기본값 하나만 보면 하드코딩된 값이 마침 기본값과 같을 때 통과한다 — 실제로 깨진
    # 경우는 **호출자가 하한을 덮었을 때**이고, 그 경우가 여기 없으면 검사가 자기 대상을
    # 안 쥔다. 덮어쓴 값으로도 본다.
    for override in (None, {"RUNTIME_LOWER": OVERRIDDEN_LOWER[stack]}):
        values = effective_values(stack, override)
        files = SKELETONS[stack]("demo-project", values, "demo-owner")
        assert path in files, f"{stack} skeleton has no {path}"
        found = re.search(pattern, files[path], re.MULTILINE)
        assert found, f"{stack}: {path} declares no runtime lower bound"
        assert found.group(1) == values["RUNTIME_LOWER"], (
            f"{stack}: {path} claims {found.group(1)} and the CI matrix runs "
            f"{values['RUNTIME_LOWER']}. One of them is a lie and CI is where it is discovered."
        )


def test_the_python_skeleton_declares_the_dependency_its_test_command_needs():
    """실측: 3.12 잡이 `No module named pytest` 로 죽었다. 설치 명령은 프로젝트가 선언한
    것만 설치할 수 있고, 프로젝트가 pytest 를 선언하지 않았다."""
    values = effective_values("python")
    files = SKELETONS["python"]("demo-project", values, "demo-owner")

    assert "pytest" in files["pyproject.toml"], "the test lane installs nothing that can run pytest"
    # 설치 명령이 그 extra 를 실제로 부르는가. 선언만 하고 안 부르면 같은 자리에서 같이 죽는다.
    assert "[test]" in values["INSTALL_CMD"], (
        f"the skeleton declares a `test` extra and the install command does not ask for it: "
        f"{values['INSTALL_CMD']!r}"
    )
