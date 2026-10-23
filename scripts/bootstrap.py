#!/usr/bin/env python3
"""Compile the inline PLAN submitted by the bound CTO in ACP PROJECT_BOOTSTRAP.

RF performs no external writes. ACP owns review, CEO CONFIRM, genesis execution,
verification, write-ledger recovery and activation. No RF approval receipt is needed.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from canonical import digest
from materialize import CI_PATH, MANIFEST_PATH
from plan import compile_plan, content_digest, validate_request
from secret_screen import screen_text, screen_value
from render_ci import effective_values


def acp_normalize(payload, acp_src):
    """Execute the installed ACP schema, including every default, rather than duplicating it."""
    src = Path(acp_src or os.environ.get("ACP_SRC", Path.home() / "projects/agent-control-plane/src")).resolve()
    tsx = src.parent / "node_modules/.bin/tsx"
    if not (src / "contracts/manifest.ts").is_file() or not tsx.is_file():
        raise ValueError("ACP_SCHEMA_UNAVAILABLE: set ACP_SRC to the deployed ACP src directory with tsx installed")
    done = subprocess.run([str(tsx), str(Path(__file__).with_name("acp_bridge.mts"))],
                          input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True,
                          env={**os.environ, "ACP_SRC": str(src)}, timeout=60)
    if done.returncode:
        # Treat bridge output as untrusted: forward only an exact fixed category.
        try:
            reason = json.loads(done.stdout).get("reason")
        except (ValueError, AttributeError):
            reason = None
        if reason in ("manifest", "manifest.projectId", "manifest.verificationCommands",
                      "manifest.ciWorkflows", "manifest.branchProfile", "genesis"):
            raise ValueError(f"ACP_SCHEMA: {reason} refused by ACP's authoritative schema "
                             "(VERIFICATION may be unsupported)")
        raise ValueError("ACP_SCHEMA: VERIFICATION or manifest/genesis is unsupported by this ACP checkout")
    return json.loads(done.stdout)


def _product_intent(request):
    """Private provenance for ACP's resident handoff; never render this into public bytes."""
    intent = request.get("productIntent")
    if intent is not None:
        # Explicit structured intent supplements the verbatim seed; never replace the owner's idea.
        intent = copy.deepcopy(intent)
        if request["seed"] not in intent["summary"]:
            intent["summary"] = request["seed"] + "\n\n" + intent["summary"]
        for reference in re.findall(r"https?://[^\s<>]+", request["seed"]):
            if reference not in intent["references"]:
                intent["references"].append(reference)
        return intent
    return {"summary": request["seed"],
            "references": re.findall(r"https?://[^\s<>]+", request["seed"]),
            "ownerInstructions": []}


_PRIVATE_VALUE = re.compile("|".join((
    r"(?<![\w:/])/(?:Users|home|tmp|private|var|etc|opt|mnt|root|usr|Volumes|workspace|srv)/",
    r"~/|\b[A-Za-z]:[\\/]",
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    r"\b\d{9,}\b",
    r"\b(?:session|run|actor|room)[_-](?:id[\s:=_-]*)?[\w-]+|\buser[_-]id[\s:=_-]*[\w-]+",
    r"\b(?:actor|user|session|room|model|runtime|environment|provider)(?:[_ -]?id)?\s*[:=]\s*[\w/~][^\s,]*",
    r"\b(?:gpt-\d[\w.-]*|claude-(?:sonnet|opus|haiku)[\w.-]*)\b",
    r"\b(?:ACP|Hermes|Claude Code)\b",
)), re.IGNORECASE)


def _screen_public(value, seed=None):
    """Refuse private value shapes without echoing the offending input. Secrets are screened separately."""
    if isinstance(value, dict):
        for item in value.values():
            _screen_public(item, seed)
    elif isinstance(value, list):
        for item in value:
            _screen_public(item, seed)
    elif isinstance(value, str):
        if _PRIVATE_VALUE.search(value) or (seed and seed in value):
            raise ValueError("PUBLIC_TEXT: private provenance is not a public product requirement")


def _public_product_text(request):
    """Project structured product requirements, with a neutral fallback instead of conversation."""
    intent = request.get("productIntent", {})
    summary = intent.get("summary", "").strip()
    if not summary or summary == request["seed"].strip():
        summary = "Implement the product requirements described in the project backlog."
    else:
        _screen_public(summary, request["seed"])
    references = intent.get("references", [])
    _screen_public(references, request["seed"])
    requirements = []
    for instruction in intent.get("ownerInstructions", []):
        # Dated attribution and bootstrap directions belong to the private handoff.
        instruction = re.sub(r"\s*\(owner \d{4}-\d{2}-\d{2}\)", "", instruction, flags=re.IGNORECASE)
        for sentence in re.split(r"(?<=[.!?])\s+", instruction):
            if not re.search(r"\b(?:create|bootstrap|provision|dispatch)\b.*\b(?:ACP|Repo Factory|CTO|Hermes|Buzz|session|room|identity)\b|^Motivation:",
                             sentence, re.IGNORECASE):
                # Repository creation conditions are not capabilities or restrictions of the product.
                if re.search(r"^(?:Name|(?:Repository|Repo) name|Visibility)\s*:|"
                             r"^(?:Public|Private)\s+(?:GitHub\s+)?(?:repository|repo)\b|"
                             r"^(?:The\s+)?(?:repository|repo)\s+(?:must|should)\s+be\s+(?:public|private)\b|"
                             r"^(?:Create|Name|Rename)\b.*\b(?:repository|repo)\b", sentence, re.IGNORECASE):
                    continue
                _screen_public(sentence, request["seed"])
                requirements.append(sentence)
    sections = [summary]
    if references:
        sections.append("References:\n" + "\n".join(f"- {ref}" for ref in references))
    if requirements:
        sections.append("Requirements:\n" + "\n".join(f"- {item}" for item in requirements))
    return "\n\n".join(sections)


def _verification_in_files(commands, files):
    """Admit only invocations this scaffold actually supplies; fail before repository creation.

    ACP currently supports Node verification executables. Other toolchains require ACP
    capability support before they can enter this path, even if RF can render their CI.
    """
    if not commands:
        raise ValueError("UNSUPPORTED_VERIFICATION: at least one real project test command is required")
    package = json.loads(files.get("package.json", "{}"))
    scripts = package.get("scripts", {})
    has_test = False
    for command in commands:
        argv = command["argv"]
        if command["cwd"] != "." or command["repositoryRole"] != "primary":
            raise ValueError("UNSUPPORTED_VERIFICATION: genesis verification must target primary at the root")
        if argv in (["npm", "test"], ["npm", "run", "test"]):
            valid = scripts.get("test") == "node --test" and "test/smoke.test.js" in files
            has_test = has_test or valid
        elif len(argv) == 3 and argv[:2] == ["node", "--check"]:
            valid = argv[2] in files and argv[2].endswith(".js")
        elif len(argv) >= 2 and argv[:2] == ["node", "--test"]:
            valid = len(argv) == 2 or all(path in files and path.endswith(".test.js") for path in argv[2:])
            has_test = has_test or (valid and "test/smoke.test.js" in files)
        else:
            valid = False
        if not valid:
            raise ValueError("UNSUPPORTED_VERIFICATION: command is not implemented by the genesis scaffold")
    if not has_test:
        raise ValueError("UNSUPPORTED_VERIFICATION: declare the scaffold's real test command")


def compile_bootstrap_plan(request, verification_commands, *, operation_id, acp_src=None,
                           requested_optional=None, ci_values=None):
    # Screen every owner-supplied string before compile_plan can render seed-derived bytes.
    screen_value(request, "bootstrap request")
    screen_value(verification_commands, "verification commands")
    screen_value(ci_values, "CI values")
    validate_request(request)
    if not verification_commands:
        raise ValueError("UNSUPPORTED_VERIFICATION: at least one real project test command is required")
    if len(request["repositories"]) != 1 or request["repositories"][0]["role"] != "primary":
        raise ValueError("ACP genesis currently supports exactly one primary repository")
    public_requirement = _public_product_text(request)
    _screen_public(request.get("genesis", {}), request["seed"])
    legacy_request = {k: v for k, v in request.items() if k not in ("productIntent", "genesis")}
    legacy_request["seed"] = public_requirement
    compiled = compile_plan(legacy_request, verification_commands, operation_id=operation_id,
                            requested_optional=requested_optional, ci_values=ci_values)
    if compiled["unresolvedGaps"]:
        raise ValueError("BOOTSTRAP_GAPS: " + "; ".join(compiled["unresolvedGaps"]))
    files = compiled["files"]
    manifest = compiled["projectManifest"]
    manifest["commitlore"] = {"mode": "preferred"}  # No hooks/configuration are seeded.
    manifest["ciWorkflows"] = [{"path": CI_PATH, "checkName": "project-ci", "repositoryRole": "primary",
                                "approvedDigest": content_digest(files[CI_PATH]),
                                "unapprovedFirstActivation": False}]
    normalized = acp_normalize({"projectManifest": manifest}, acp_src)
    manifest = normalized["manifest"]
    _verification_in_files(manifest["verificationCommands"], files)
    values = effective_values("node", ci_values)
    if (values["INSTALL_CMD"] != "npm install" or values["TEST_CMD"] != "npm test"
            or values["BUILD_CMD"] != "node --check index.js"):
        raise ValueError("UNSUPPORTED_VERIFICATION: CI commands must match the seeded tests and syntax check")
    files[MANIFEST_PATH] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # Regenerate local guidance from the same normalized commands and CommitLore policy.
    from materialize import _agents, _readme
    repository_name = request["repositories"][0]["name"]
    files["AGENTS.md"] = _agents(repository_name, manifest["verificationCommands"], "preferred")
    files["README.md"] = _readme(repository_name, public_requirement, manifest["verificationCommands"])
    intent = _product_intent(request)
    backlog = request.get("genesis", {})
    bundle = {
        "schema": "acp.genesis-bundle.v1",
        "commitMessage": backlog.get("commitMessage", "chore: initialize project"),
        "files": [{"path": path, "mode": "100644",
                   "contentBase64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                   "sha256": content_digest(text)} for path, text in sorted(files.items())],
        "branches": {"main": "main", "integration": "dev", "genesis": "genesis"},
        "labels": backlog.get("labels", [{"name": "enhancement", "color": "a2eeef", "description": "Product development"}]),
        "milestones": backlog.get("milestones", [{"key": "first-product", "title": "First product increment", "description": public_requirement}]),
        "issues": backlog.get("issues", [{"key": "implement-product", "title": "Implement the owner's product intent",
                                         "body": public_requirement, "labels": ["enhancement"], "milestoneKey": "first-product"}]),
        "productIntent": intent,
    }
    for rows, key in ((bundle["labels"], "name"), (bundle["milestones"], "key"), (bundle["issues"], "key")):
        # The project must be developable immediately: an explicitly empty collection erases it.
        if not rows:
            raise ValueError("GENESIS_BACKLOG: labels, milestones and issues must each be nonempty")
        if len({row[key] for row in rows}) != len(rows):
            raise ValueError("GENESIS_BACKLOG: duplicate resource key")
    labels = {row["name"] for row in bundle["labels"]}
    milestones = {row["key"] for row in bundle["milestones"]}
    for issue in bundle["issues"]:
        if set(issue["labels"]) - labels or (issue["milestoneKey"] is not None and issue["milestoneKey"] not in milestones):
            raise ValueError("GENESIS_BACKLOG: issue references an undeclared label or milestone")
    for path, text in files.items():
        screen_text(text, "genesis file")
        _screen_public(path)
        _screen_public(text)
    _screen_public({key: bundle[key] for key in ("commitMessage", "labels", "milestones", "issues")})
    screen_value({k: v for k, v in bundle.items() if k != "files"}, "genesis text")
    normalized = acp_normalize({"projectManifest": manifest, "genesis": bundle}, acp_src)
    identity = manifest["repositories"][0]["remote"]
    # Strict ACP shapes only, in execution order. Genesis never pushes the default branch (owner
    # 2026-10-22 "초기 파일도 PR로"; the standing no-default-branch-push rule stands): GitHub's own
    # initialization puts a README on main, main is protected, the genesis commit goes to its own
    # branch, and ACP opens, verifies and merges the genesis PR inside the bootstrap. dev is then
    # created from the merged main, protected, and made the default branch for git flow.
    def protect(branch):
        return {"operationId": f"protect-{branch}", "resourceType": "branch-protection",
                "resourceIdentity": identity + "#" + branch,
                "desiredState": {"requiredStatusChecks": {"strict": True, "contexts": ["project-ci"]},
                                 "enforceAdmins": True, "requiredApprovingReviewCount": None,
                                 "allowForcePushes": False, "allowDeletions": False}}
    operations = [
        {"operationId": "create-repository", "resourceType": "repository", "resourceIdentity": identity,
         "desiredState": {"visibility": "public", "autoInit": True}},
        protect("main"),
        {"operationId": "push-genesis-branch", "resourceType": "branch", "resourceIdentity": identity + "#genesis"},
        {"operationId": "create-dev", "resourceType": "branch", "resourceIdentity": identity + "#dev"},
        protect("dev"),
        {"operationId": "set-default-branch", "resourceType": "setting", "resourceIdentity": identity + "#default-branch",
         "desiredState": {"defaultBranch": "dev"}},
    ]
    return {
        "summary": "\n\n".join([request["seed"], intent["summary"], *intent["references"], *intent["ownerInstructions"]]),
        "dependencies": [], "repositoryIntent": [identity],
        "verificationIntent": ["Run declared local tests and required CI with Actions provenance at the genesis PR head "
                               "before ACP merges into main; verify merged main before activation"],
        "knownConflicts": [], "risks": [], "removedOverengineering": ["repo-local runtime and extra approval receipts"],
        "bootstrapOperationId": operation_id,
        "requestDigest": digest(request, volatile="strip"),
        "projectManifest": normalized["manifest"], "projectManifestDigest": normalized["manifestDigest"],
        "githubOperations": operations, "genesis": normalized["genesis"], "genesisDigest": normalized["genesisDigest"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--verification", required=True, type=Path)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--acp-src", type=Path, default=None)
    parser.add_argument("--ci-values", type=Path)
    parser.add_argument("--optional", nargs="*", default=[])
    args = parser.parse_args(argv)
    try:
        plan = compile_bootstrap_plan(json.loads(args.request.read_text()), json.loads(args.verification.read_text()),
                                      operation_id=args.operation_id, acp_src=args.acp_src,
                                      requested_optional=args.optional,
                                      ci_values=json.loads(args.ci_values.read_text()) if args.ci_values else None)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
