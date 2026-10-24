#!/usr/bin/env python3
"""Compile the inline PLAN submitted by the bound CTO in ACP PROJECT_BOOTSTRAP.

RF performs no external writes. ACP owns review, CEO CONFIRM, genesis execution,
verification, write-ledger recovery and activation. No RF approval receipt is needed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

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
            refusal = json.loads(done.stdout)
            reason = refusal.get("reason")
            field = refusal.get("field")
        except (ValueError, AttributeError):
            reason = field = None
        if reason == "planIntent":
            if isinstance(field, str) and re.fullmatch(
                r"(?:summary|repositoryIntent|verificationIntent|risks)(?:\[\d+\])?", field
            ):
                raise ValueError(f"ACP_PLAN_INTENT: {field} refused by ACP's authoritative review bounds; no truncation")
            raise ValueError("ACP_PLAN_INTENT: generated intent refused by ACP's authoritative review bounds")
        if reason in ("manifest", "manifest.projectId", "manifest.verificationCommands",
                      "manifest.ciWorkflows", "manifest.branchProfile", "genesis"):
            raise ValueError(f"ACP_SCHEMA: {reason} refused by ACP's authoritative schema "
                             "(VERIFICATION may be unsupported)")
        raise ValueError("ACP_SCHEMA: VERIFICATION or manifest/genesis is unsupported by this ACP checkout")
    return json.loads(done.stdout)


DEFAULT_PUBLIC_SUMMARY = "Implement the product requirements described in the project backlog."


def _product_intent(request):
    """Pinned ACP shape: public summary/references, with provenance only in private instructions."""
    intent = request.get("productIntent", {})
    return {"summary": intent.get("summary", DEFAULT_PUBLIC_SUMMARY),
            "references": list(intent.get("references", [])),
            "ownerInstructions": [request["seed"], *intent.get("ownerInstructions", [])]}


_PRIVATE_VALUE = re.compile("|".join((
    r"(?<![\w:/])/(?:Users|home|tmp|private|var|etc|opt|mnt|root|usr|Volumes|workspace|srv)/",
    r"~/|\b[A-Za-z]:[\\/]",
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    r"\b\d{9,}\b",
    r"\b(?:session|run|actor|room)[_-](?:id[\s:=_-]*)?[\w-]+|\buser[_-]id[\s:=_-]*[\w-]+",
    r"\b(?:actor|identity|user|session|room|model|runtime|environment|provider)(?:[_ -]?id)?[\"']?\s*[:=]\s*[\"']?[\w/~][^\s,]*",
    r"\brun[_ -]?id[\"']?\s*[:=]\s*[\"']?\S+",
    r"\bdecision[_ -]?id[\"']?\s*[:=]\s*[\"']?\S+",
    r"\b(?:session|decision)(?:[_ -]?id)?[\"']?(?:\s*[:=_-]\s*|\s+)[\"']?[0-9a-f]{8,}\b",
    r"\b(?:gpt-\d[\w.-]*|claude-(?:sonnet|opus|haiku)[\w.-]*)\b",
    r"\b(?:ACP|Hermes|Claude Code)\b",
    r"\b(?:actor|identity)(?:[_ -]?id)?\s+is\s+[\w]+(?:[-_][\w]+)+\b",
    r"file://",
)), re.IGNORECASE)


# Compare words, not inferred intent. Four-word windows also catch partial sentences.
PRIVATE_FRAGMENT_WORDS = 4


def _words(text):
    return re.findall(r"\w+(?:[-'’]\w+)*", text.casefold())


def _private_fragments(request):
    """Legacy seed/owner-instruction protection; review data has identifier rules only."""
    fragments = set()
    for text in [request["seed"], *request.get("productIntent", {}).get("ownerInstructions", [])]:
        for sentence in re.split(r"[.!?。！？;]+", text):
            words = _words(sentence)
            fragments.update(" ".join(words[i:i + PRIVATE_FRAGMENT_WORDS])
                             for i in range(len(words) - PRIVATE_FRAGMENT_WORDS + 1))
    return fragments


def _private_review_text(request):
    return [*request.get("reviewEvidence", []),
            *([request["stagePlan"]] if "stagePlan" in request else [])]


def _private_identifiers(texts):
    """Rule A: identifier shapes or explicit ID keys, never bare declarations/prose overlap."""
    identifiers = set()
    for text in texts:
        identifiers.update(re.findall(r"\b[0-9a-f]{7,}\b", text, re.IGNORECASE))
        identifiers.update(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", text, re.IGNORECASE))
        identifiers.update(re.findall(r"\b(?:ses|run|msg|prj|asg|art|task|operation)_[\w-]+", text))
        identifiers.update(re.findall(r"(?<![\w:/~])(?:~/|/|[a-z]:[\\/])[^\s\"'<>\[\]{},;]+", text, re.IGNORECASE))
        identifiers.update(re.findall(
            r"(?<![\w-])(?:session|run|decision|message|project|operation|actor|binding|escalation)[_ -]?id"
            r"[\"']?(?:\s*[:=]\s*|\s+)[\"']?(\S+)", text, re.IGNORECASE))
    return {token.rstrip(".,;:!?。！？\"'()[]{}<>") for token in identifiers
            if token.rstrip(".,;:!?。！？\"'()[]{}<>")}


_COMMIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)


def _public_references(request):
    """Rule B: only full SHA-1/SHA-256 references or absolute URLs."""
    references = []
    for reference in request.get("productIntent", {}).get("references", []):
        if _COMMIT_SHA.fullmatch(reference):
            references.append(reference)
        elif not any(c.isspace() for c in reference):
            try:
                url = urlsplit(reference)
            except ValueError:
                continue
            if url.scheme and url.netloc:
                references.append(reference)
    return references


def _without_exact_references(value, references):
    """Exempt whole listed tokens, never their substrings or surrounding prose.

    SHA tokens may have sentence delimiters. URLs must match exactly, optionally inside
    paired enclosures; URL path/query punctuation is never stripped from the value.
    This is a validation copy only; generators always receive the original public text.
    """
    def keep_unlisted(match):
        token = match.group()
        if token in references:
            return ""
        sha = token.strip(".,;:!?。！？\"'()[]{}<>")
        if sha in references and _COMMIT_SHA.fullmatch(sha):
            return ""
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"), ('"', '"'), ("'", "'")):
            if token.startswith(opening):
                enclosed = token[1:].rstrip(".,;:!?。！？")
                if enclosed.endswith(closing) and enclosed[:-1] in references:
                    return ""
        return token
    return re.sub(r"\S+", keep_unlisted, value)


def _screen_public(value, private_fragments=(), private_values=(), public_references=(), *, sink="public"):
    """Reject unsafe public input unchanged; never classify or rewrite private instructions."""
    if isinstance(value, dict):
        for key, item in value.items():
            _screen_public(item, private_fragments, private_values, public_references, sink=f"{sink}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _screen_public(item, private_fragments, private_values, public_references, sink=f"{sink}[{index}]")
    elif isinstance(value, str):
        identifiers = _private_identifiers(private_values)
        # Rule B protects only exact reference occurrences. It grants no global exemption
        # to an identifier contained inside a reference; copies elsewhere are still refused.
        review_screened = _without_exact_references(value, public_references)
        if any(token in review_screened for token in identifiers):
            raise ValueError(f"PUBLIC_TEXT: private identifier at {sink}")
        words = _words(value)
        if any(" ".join(words[i:i + PRIVATE_FRAGMENT_WORDS]) in private_fragments
               for i in range(len(words) - PRIVATE_FRAGMENT_WORDS + 1)):
            raise ValueError("PUBLIC_TEXT: private fragment overlaps public text")
        # Preserve the legacy guard's public URL/SHA handling. It cannot exempt an extracted
        # private identifier: Rule C has already refused those above unless Rule B applies.
        # This validation copy never supplies rendered bytes.
        screened = value
        provenance = re.findall(r"https?://[^\s]+|\b[0-9a-f]{40}\b", value, re.IGNORECASE)
        for token in [*provenance, *public_references]:
            screened = screened.replace(token, "")
        if _PRIVATE_VALUE.search(screened):
            raise ValueError("PUBLIC_TEXT: private identity or runtime value")


def _public_product_text(request):
    """Only the request author's explicit public fields can supply public product prose."""
    intent = request.get("productIntent", {})
    summary = intent.get("summary", DEFAULT_PUBLIC_SUMMARY)
    references = intent.get("references", [])
    requirements = intent.get("publicRequirements", [])
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
    private_values = _private_review_text(request)
    # Primary boundary: public generation never receives private review data. The auxiliary
    # identifier screen additionally refuses private identifiers copied into public input.
    public_request = {k: v for k, v in request.items() if k not in ("reviewEvidence", "stagePlan")}
    public_references = _public_references(public_request)
    private_fragments = _private_fragments(public_request)
    for key in ("summary", "references", "publicRequirements"):
        _screen_public(public_request.get("productIntent", {}).get(key),
                       private_fragments, private_values, public_references, sink=f"productIntent.{key}")
    public_requirement = _public_product_text(public_request)
    _screen_public(public_requirement, private_fragments, private_values, public_references, sink="PLAN.summary")
    _screen_public(public_request.get("genesis", {}), private_fragments, private_values, public_references, sink="genesis")
    legacy_request = {k: v for k, v in public_request.items() if k not in ("productIntent", "genesis")}
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
    repository_name = public_request["repositories"][0]["name"]
    files["AGENTS.md"] = _agents(repository_name, manifest["verificationCommands"], "preferred")
    files["README.md"] = _readme(repository_name, public_requirement, manifest["verificationCommands"])
    intent = _product_intent(public_request)
    backlog = public_request.get("genesis", {})
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
    for index, (path, text) in enumerate(files.items()):
        screen_text(text, "genesis file")
        _screen_public(path, private_fragments, private_values, public_references, sink=f"genesis.files[{index}].path")
        _screen_public(text, private_fragments, private_values, public_references, sink=f"genesis.files[{index}].content")
    _screen_public({key: bundle[key] for key in ("commitMessage", "labels", "milestones", "issues")},
                   private_fragments, private_values, public_references, sink="genesis")
    screen_value({k: v for k, v in bundle.items() if k != "files"}, "genesis text")
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
    # These PLAN strings are private data_only review context, never authority or genesis text.
    verification_intent = [
        "Run declared local tests and required CI with Actions provenance at the genesis PR head "
        "before ACP merges into main; verify merged main before activation",
        *private_values,
        *[
            f"issue {issue['key']}: product acceptance = the seeded issue body's acceptance; "
            f"verified by a follow-on STANDARD_WORK run of project {manifest['projectId']} that the resident CTO "
            "creates after activation; handoff: the issue reaches the resident CTO in the activation "
            "handoff and is not complete until that run's verification passes."
            for issue in bundle["issues"] if "stagePlan" in request
        ],
    ]
    # Run ACP's actual projector, which owns all intent bounds and character-count semantics.
    normalized = acp_normalize({"projectManifest": manifest, "genesis": bundle,
                                "planIntent": {"summary": public_requirement, "repositoryIntent": [identity],
                                               "verificationIntent": verification_intent, "risks": []}}, acp_src)
    return {
        "summary": public_requirement,
        "dependencies": [], "repositoryIntent": [identity],
        "verificationIntent": verification_intent,
        "knownConflicts": [], "risks": [], "removedOverengineering": ["repo-local runtime and extra approval receipts"],
        "bootstrapOperationId": operation_id,
        "requestDigest": digest(request, volatile="strip"),
        "projectManifest": normalized["manifest"], "projectManifestDigest": normalized["manifestDigest"],
        "githubOperations": operations, "genesis": normalized["genesis"], "genesisDigest": normalized["genesisDigest"],
        **({"supersedesProjectId": request["supersedesProjectId"]} if "supersedesProjectId" in request else {}),
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
