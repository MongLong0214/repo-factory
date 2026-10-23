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
        raise ValueError("ACP_SCHEMA: VERIFICATION or manifest/genesis is unsupported by this ACP checkout")
    return json.loads(done.stdout)


def _product_intent(request):
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
    legacy_request = {k: v for k, v in request.items() if k not in ("productIntent", "genesis")}
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
        raise ValueError("UNSUPPORTED_VERIFICATION: CI commands must run the seeded install, tests and syntax check")
    files[MANIFEST_PATH] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # Regenerate local guidance from the same normalized commands and CommitLore policy.
    from materialize import _agents, _readme
    files["AGENTS.md"] = _agents(manifest["projectId"], manifest["verificationCommands"], "preferred")
    files["README.md"] = _readme(manifest["projectId"], request["seed"], manifest["verificationCommands"])
    intent = _product_intent(request)
    body = "\n\n".join([request["seed"], intent["summary"], *intent["references"], *intent["ownerInstructions"]])
    backlog = request.get("genesis", {})
    bundle = {
        "schema": "acp.genesis-bundle.v1",
        "commitMessage": backlog.get("commitMessage", "chore: initialize project"),
        "files": [{"path": path, "mode": "100644",
                   "contentBase64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                   "sha256": content_digest(text)} for path, text in sorted(files.items())],
        "branches": {"main": "main", "integration": "dev", "genesis": "genesis"},
        "labels": backlog.get("labels", [{"name": "enhancement", "color": "a2eeef", "description": "Product development"}]),
        "milestones": backlog.get("milestones", [{"key": "first-product", "title": "First product increment", "description": request["seed"]}]),
        "issues": backlog.get("issues", [{"key": "implement-product", "title": "Implement the owner's product intent",
                                         "body": body, "labels": ["enhancement"], "milestoneKey": "first-product"}]),
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
        "summary": request["seed"] + "\n\n" + body,
        "dependencies": [], "repositoryIntent": [identity],
        "verificationIntent": ["Run declared tests and trusted CI at the genesis head before activation"],
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
