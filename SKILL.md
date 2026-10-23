---
name: repo-factory
description: "Turn an owner's product idea into an inline genesis PLAN for ACP PROJECT_BOOTSTRAP, then await ACP's verified activation and resident CTO handoff."
---

# Repo Factory

This file is the canonical skill. Distribute it with its compiler dependencies using
`scripts/stage_skill.py`; never maintain a separate installed instruction set.
RF compiles the product intent, runnable scaffold, tests, CI, manifest and initial backlog.
ACP alone executes GitHub writes, verification, review, merge and runtime provisioning.

## Owner conversation: exactly three confirmations

1. **Phase 0 questions.** Establish the product idea/reference, intended behavior, name,
   public GitHub owner/repository, constraints and risk profile (SIMPLE/STANDARD/GUARDED).
   Preserve the owner's words in `seed`. Preserve references and free-form instructions
   in `productIntent: {summary, references, ownerInstructions}`. Refuse PRIVATE explicitly;
   do not silently change visibility, upgrade a paid plan or substitute another product.
2. **Dossier.** Present the evidence, product interpretation and practical stack choice.
   Scale the dossier to the idea; research only what resolves real uncertainty. Confirm
   that it still describes the owner's product, including the original reference.
3. **Final plan bundle.** Present the complete PLAN below, including inline files,
   normalized manifest, digests, initial issues/milestones/labels and remaining limits.
   Obtain the owner's final confirmation of this bundle. ACP's independent review and
   authenticated CEO CONFIRM consume this approved intent; do not ask the owner again.

There is no fourth confirmation, separate RF authority receipt, T3 approval or Genesis
approval ceremony. A material product-scope correction returns to the relevant existing
confirmation, rather than creating a new approval stage.

## Compile the final PLAN

Run from this skill directory with Python 3.9+, `jsonschema==4.23.0`, Node and an ACP
checkout with its dependencies installed. `ACP_SRC` must identify the ACP source used
by the receiving runtime. The compiler calls that checkout's schema via its own tsx.
It refuses unsupported capabilities before creating any repository.

```bash
python3 scripts/bootstrap.py --request request.json --verification verification.json \
  --operation-id 11111111-2222-3333-4444-555555555555 \
  --acp-src /path/to/agent-control-plane/src > PLAN.json
```

Choose a new UUID for a new bootstrap; reuse it for retries of that same operation.
`request.json` follows `schemas/bootstrap-request.schema.json`. It carries the ACP run ID,
verbatim `seed`, profile, priority, one primary repository (with `stack: "node"`),
`visibility: "public"`, `remoteOwner`, origin, and optional structured `productIntent`.
Optional `genesis` request fields supply approved commitMessage/labels/milestones/issues;
otherwise RF seeds an implementation issue tied to the owner's idea and first milestone.
Optional `projectId` is the permanent ACP key and must match
`^[a-z0-9][a-z0-9._-]{0,80}$` (1–81 characters). An explicit id is validated verbatim:
never lowercase, slug or trim it; refuse it if invalid. When omitted, RF uses only
`repositories[0].name`: lowercase, replace characters outside `[a-z0-9._-]` with `-`,
trim leading/trailing `.`, `_`, `-`, then truncate to 81 characters. If still invalid,
compilation refuses with `PROJECT_ID_INVALID: manifest.projectId`; supply an explicit id.
The GitHub name, remote `github:<owner>/<Name>`, document titles and repository intent
retain the owner's exact case, including in the resident handoff. Normalization applies only
when deriving an omitted ACP key.
ACP ids are permanent primary keys, including after retirement. Re-bootstrap of a retired
project's repository must supply a **new** `projectId` (for example `freshreadme-2`);
ACP links the old project through `supersedes_project_id` by repository identity.
RF cannot see ACP registry state at compile time and never invents a suffix. An invalid
derived slug is refused during compilation. A valid derived or explicit id is passed unchanged
even if already occupied; ACP refuses that collision when it registers the project, including
when the existing project is retired. Normalization/truncation can also produce collisions.
For a confirmed collision, the CEO/CTO choose and supply a new explicit `projectId`
(e.g. `freshreadme-2`) within existing delegated scope; the owner is never asked for input.
If registration's outcome is unclear, they first reconcile durable ACP state and never
resubmit under a new id or new key. Never auto-suffix.
`verification.json` is a list such as `[{"id":"test","argv":["npm","test"]}]`.
Optional `--ci-values ci.json` overrides reviewed template values; `--optional` selects
profile-supported artifacts. Missing artifacts or unsupported verification stop compilation.
The current ACP adapter admits seeded Node test commands;
other toolchains need ACP support before this bootstrap path can accept them.

The output **is the PLAN**, not a filename-only handoff. It carries:

- `genesis.schema: "acp.genesis-bundle.v1"`, with repository-relative files as base64 bytes,
  file modes and SHA-256 digests; commit message; labels, milestones, issues and productIntent.
- `projectManifest` normalized by ACP's `projectManifestSchema`, and its `manifestDigest`.
  Every declared verification command runs against files actually in this genesis.
- CI `approvedDigest` equal to SHA-256 of the workflow bytes in the bundle, approved as part
  of the final bundle. Later workflow changes require independent review and CEO CONFIRM.
- `branchProfile`: main/dev, default dev, merge_commit, semver. Standard flow is
  feature/* -> dev -> main, plus release/* and hotfix/*. No force pushes or ref replacement.
- ACP-supported `githubOperations`, in order: create PUBLIC with `autoInit:true` (README on
  main), protect main, push the genesis commit (parent = autoInit head) to branch `genesis`,
  then ACP opens its PR into main, runs local verification and required CI with Actions provenance,
  and merges it inside the bootstrap; dev from the merged main, protect
  dev, default dev. The default branch is never pushed directly (owner 2026-10-22: "초기 파일도
  PR로"). No repo-local governance kernel.

Secret screening covers owner input before rendering, outgoing file bytes and backlog text.
Remove credential-shaped content instead of bypassing the refusal. Never use `--no-verify`.
Public files, backlog text and the commit message carry neutral structured product requirements,
never the conversation seed or private identities, local paths or runtime details. With no
structured product intent, use a neutral backlog requirement. The verbatim seed, references
and original instructions stay in the PLAN summary and non-repository `genesis.productIntent`;
they must reach the resident CTO handoff and must not be copied into committed files.
CommitLore is preferred unless the bundle actually includes its required setup.
ACP refusals report fixed safe field categories such as `ACP_SCHEMA: manifest.projectId`
(also `manifest.verificationCommands`, `manifest.ciWorkflows`, `manifest.branchProfile`,
or the broader `manifest`/`genesis`). These name the failing field without echoing
rejected values, credential-shaped content or raw ACP error details.

The exact wire contract is [genesis-bundle-v1](contracts/genesis-bundle-v1.md).
The bound CTO must receive the JSON body, including base64 bytes, through ACP-readable
artifacts/tool input. A local path such as `PLAN.json` alone is not a deliverable.

## Final phase: submit to ACP and await activation

Through authenticated Hermes ACP tools, create a project-less run with `run_create`:
`kind: "PROJECT_BOOTSTRAP"`, `projectId: null`, selected `executionMode`, `repositories: []`,
and a task contract preserving the product goal/reference, bundle and requested activation.
Dispatch with `run_dispatch`. Use the returned run ID in the bootstrap request.
The bound BOOTSTRAP_CTO reads `contract_get`, compiles/retrieves the final bundle and calls
`plan_submit` with `plan: <full PLAN body>` and a nonempty task list. Each task has key,
title, category, dependsOn and spec; include the owner's product intent in its spec.
Use ACP's authenticated mutation envelope and idempotency keys, never fabricated sessions.

ACP performs independent review, CEO CONFIRM, the genesis branch push and genesis PR merge, branch creation,
backlog projection, verification and activation using its own write ledger, exact target
checks and readbacks. RF must not call manual git/gh publishing as a fallback. If the
receiver strips `genesis` or refuses the bundle/CI, report the incompatibility and stop
before writes; preserve the bundle for retry after ACP support is deployed.

**Do not say done on PLAN acceptance, a repository URL, parser PASS or Buzz connection.**
Require ACP's canonical activation result and readbacks establishing all of:

- Repository identity, genesis PR head, ACP merge commit and main/dev heads at that merge; default dev; actual labels,
  milestones and issues; exact-head tests and CI under the approved workflow digest.
- Active manifest digest equal to the submitted normalized manifest, local binding and
  ACP doctor/activation success. Verification and merge authority remain in ACP.
- A resident interactive Claude Code CTO: canonical project binding, live process,
  Remote Control (RC) availability, fleet recovery, active claim and the incoming CTO's
  `handoff_ack`. The delivered handoff preserves the product idea, references, instructions
  and queued implementation issues. A headless driven turn does not establish this.
- Dedicated project Buzz identity and room, actor ID, room ID, membership and delivery
  readbacks for that CTO. A default room or connection flag does not establish this.

Request provisioning through ACP. Keep credentials, RC state, session IDs and room secrets
outside committed contracts. Missing readbacks mean activation is incomplete; report the
specific missing facts without inventing them. RF owns no long-running runtime.

## Installation staging

```bash
python3 scripts/stage_skill.py
```

This prepares `.installed-skill-staging/` inside the worktree for review. It includes this
exact SKILL.md, compiler and dependencies, profiles, schemas, reviewed CI templates and
the shared contract. It excludes retired kit scripts/templates, local governance runtime,
approval/publish CLIs and operational session data. Installation into the live skill
directory is a separate action after review.
