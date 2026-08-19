"""Slice 2 — stack-specific CI and the gate that keeps a green tick honest (PRD §14.1)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

import render_ci  # noqa: E402
from render_ci import CiRenderError, available_stacks, ci_findings, render, required_tokens  # noqa: E402

VALUES = {
    "node": {"RUNTIME_LOWER": "20", "RUNTIME_LATEST": "22",
             "INSTALL_CMD": "npm install", "TEST_CMD": "npm test", "BUILD_CMD": "npm run build"},
    "python": {"RUNTIME_LOWER": "3.11", "RUNTIME_LATEST": "3.13",
               "INSTALL_CMD": "pip install -e .[dev]", "TEST_CMD": "pytest -q", "BUILD_CMD": "python -m build"},
    "go": {"RUNTIME_LOWER": "1.22", "RUNTIME_LATEST": "1.23",
           "INSTALL_CMD": "go mod download", "TEST_CMD": "go test ./...", "BUILD_CMD": "go build ./..."},
    "rust": {"RUNTIME_LOWER": "1.80.0", "RUNTIME_LATEST": "stable",
             "INSTALL_CMD": "cargo fetch --locked", "TEST_CMD": "cargo test --locked", "BUILD_CMD": "cargo build --locked --release"},
}


def rendered(stack: str) -> str:
    return render(stack, VALUES[stack])


def test_v1_1_ships_the_four_default_stacks():
    # PRD §14.1 names them. A missing one is not a gap to fill at apply time, because an
    # unknown stack is refused rather than defaulted.
    assert available_stacks() == ["go", "node", "python", "rust"]


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_each_default_stack_renders_clean(stack: str):
    assert ci_findings(rendered(stack)) == []


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_every_action_is_pinned_to_a_full_sha(stack: str):
    for action, ref in re.findall(r"uses:\s*(\S+)@(\S+)", rendered(stack)):
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{action}@{ref} is a tag, not a pinned commit"


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_the_check_the_control_plane_waits_on_is_the_job_name(stack: str):
    # The control plane requires the check-run name, which comes from the job's `name:`, not
    # from the workflow's. A workflow named project-ci whose job is named something else
    # satisfies nothing and blocks the merge silently.
    assert re.search(r"^\s+name: project-ci$", rendered(stack), re.MULTILINE)


# --- the gate finds each shape §14.1 names ---------------------------------------------

def test_an_unresolved_token_is_a_finding():
    broken = rendered("node").replace("npm install", "{{INSTALL_CMD}}")
    assert ("UNRESOLVED_TOKEN", "INSTALL_CMD") in ci_findings(broken)


def test_a_github_expression_is_not_mistaken_for_a_token():
    # `${{ matrix.runtime }}` is the workflow doing its job, not an unrendered placeholder.
    assert "${{ matrix.runtime }}" in rendered("node")
    assert not [f for f in ci_findings(rendered("node")) if f[0] == "UNRESOLVED_TOKEN"]


def test_dropping_the_setup_action_is_a_finding():
    broken = re.sub(r"      - uses: actions/setup-node@[^\n]*\n(?:        [^\n]*\n)*", "", rendered("node"))
    codes = [c for c, _ in ci_findings(broken)]
    assert "IMPLICIT_RUNTIME" in codes


def test_dropping_the_install_step_is_a_finding():
    broken = rendered("node").replace("install dependencies", "warm up")
    assert "MISSING_INSTALL" in [c for c, _ in ci_findings(broken)]


def test_an_action_tag_instead_of_a_sha_is_a_finding():
    broken = rendered("node").replace("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683", "actions/checkout@v4")
    assert ("UNPINNED_ACTION", "actions/checkout@v4") in ci_findings(broken)


def test_a_setup_step_that_only_echoes_is_a_finding():
    broken = rendered("node").replace(
        "      - name: install dependencies\n        run: npm install",
        '      - name: install dependencies\n        run: echo "replace me"',
    )
    assert "PLACEHOLDER_ECHO" in [c for c, _ in ci_findings(broken)]


def test_the_legacy_kit_template_is_what_this_gate_was_written_against():
    # Kept as a live anchor rather than a fixture: a gate proved only against inputs its author
    # constructed has not been shown to catch anything real. Skips once §20.2 removes the kit.
    legacy = SKILL / "templates" / "kit" / ".github" / "workflows" / "ci.yml"
    if not legacy.is_file():
        pytest.skip("the repo-local kit has been removed (PRD §20.2)")
    codes = {c for c, _ in ci_findings(legacy.read_text(encoding="utf-8"))}
    assert {"PLACEHOLDER_ECHO", "IMPLICIT_RUNTIME", "MISSING_INSTALL", "UNRESOLVED_TOKEN"} <= codes


# --- rendering refuses rather than guessing --------------------------------------------

def test_an_unknown_stack_is_refused_and_not_defaulted_to_node():
    with pytest.raises(CiRenderError, match="never defaulted to Node"):
        render("cobol", VALUES["node"])


def test_a_missing_value_is_refused_rather_than_rendered_empty(monkeypatch):
    """호출자가 값을 빼먹는 것은 이제 기본값이 메운다. 메울 기본값조차 없는 토큰이 남는
    경로가 이 거부가 지키는 자리다 — 새 템플릿 토큰이 추가되고 기본값이 안 따라온 경우."""
    monkeypatch.setitem(render_ci.DEFAULT_VALUES, "node",
                        {k: v for k, v in render_ci.DEFAULT_VALUES["node"].items()
                         if k != "TEST_CMD"})
    partial = {k: v for k, v in VALUES["node"].items() if k != "TEST_CMD"}
    with pytest.raises(CiRenderError, match="TEST_CMD"):
        render("node", partial)


def test_every_token_a_reviewed_template_uses_has_a_factory_default():
    """기본값이 토큰을 못 따라가면 `--ci-values` 없이 부르는 순간 렌더가 죽는다. 그리고
    기본값이 있어야 골격과 CI 가 같은 값을 읽을 수 있다 — 그것이 이 기본값들의 이유다."""
    for stack in available_stacks():
        missing = sorted(set(required_tokens(stack)) - set(render_ci.DEFAULT_VALUES.get(stack, {})))
        assert not missing, f"{stack}: template tokens with no factory default: {missing}"


def test_an_empty_command_is_refused_because_it_verifies_nothing():
    blanked = dict(VALUES["node"], TEST_CMD="   ")
    with pytest.raises(CiRenderError, match="empty value"):
        render("node", blanked)


@pytest.mark.parametrize("stack", ["node", "python", "go", "rust"])
def test_every_stack_declares_the_values_it_needs(stack: str):
    assert set(required_tokens(stack)) <= set(VALUES[stack]), "the fixture is missing a token the template requires"


def test_no_genesis_template_demands_a_lockfile_it_does_not_produce():
    # Measured, not reasoned: the first dogfood run died on "Dependencies lock file is not
    # found" because setup-node's cache option hard-requires one and a repository at genesis
    # has none. A template that cannot be green on the commit that creates it is not a template.
    for stack in ["node", "python", "go", "rust"]:
        text = rendered(stack)
        # rust-cache is the exception: it tolerates a tree with no Cargo.lock.
        enabled = [line for line in text.splitlines() if line.strip().startswith("cache:")]
        assert not enabled, f"{stack} enables a package-manager cache that presumes a lockfile: {enabled}"
