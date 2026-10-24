# Repo Factory

Repo Factory compiles an owner's product idea into an inline genesis PLAN for ACP's
`PROJECT_BOOTSTRAP` run. ACP executes, verifies and activates it; RF ships no local
merge or governance runtime. [SKILL.md](SKILL.md) is the sole operational instruction set.

The owner confirms Phase 0 questions, the dossier and the final plan bundle. There is
no additional authority receipt or T3 approval stage. Only PUBLIC repositories are supported.
Product ideas, references and instructions stay in the PLAN's genesis `productIntent`
and initial implementation issue, and must reach the resident CTO handoff.

## Compile

Install the runtime dependency declared in `pyproject.toml` (`jsonschema==4.23.0`) and
ACP's Node dependencies. The compiler uses the receiving ACP checkout's actual schema;
it does not hash a second, incomplete Python representation of the manifest.

```bash
python3 scripts/bootstrap.py --request request.json --verification verification.json \
  --operation-id 11111111-2222-3333-4444-555555555555 \
  --acp-src /path/to/agent-control-plane/src > PLAN.json
```

The request schema is [bootstrap-request.schema.json](schemas/bootstrap-request.schema.json).
Optional `reviewEvidence` entries and `stagePlan` stay verbatim in private, review-visible
`PLAN.verificationIntent`; a supplied `stagePlan` also adds one responsibility line per seeded issue.
Without either field, the original PLAN intent is unchanged. These strings are data_only and
grant no authority. Public generation never receives these fields. Auxiliary validation extracts
hex tokens (7+ characters), UUIDs, ACP-prefixed IDs, absolute paths and values of explicit ID keys
(such as `session_id` or `decision ID`). Bare declarations such as `Decision: support exports.`
and prose overlap do not identify private review data. An extracted identifier in any public sink
is refused with the sink location, without echoing its value. The only exemption is a whole URL
or standalone commit-SHA token exactly matching a value in `productIntent.references`.
Reference prose and identifiers contained in a reference receive no exemption elsewhere. ACP's actual
intent projector validates the complete generated intent before compile success, without truncation.
Bootstrap ends at genesis, activation, resident CTO, dedicated Buzz and handoff verification;
product acceptance remains required in follow-on `STANDARD_WORK` runs after activation.
Optional `supersedesProjectId` goes to the same-named PLAN field; RF checks shape, ACP authority/state.
For a Node repository, a real verification list is `[{"id":"test","argv":["npm","test"]}]`.
The output includes the full normalized manifest plus `PLAN.genesis` with base64 file bytes,
SHA-256 digests, labels, milestones and issues. It follows the exact
[genesis bundle v1 contract](contracts/genesis-bundle-v1.md). Workflow approvedDigest is
SHA-256 over file bytes, never a git-blob ID. The genesis is one commit on branch `genesis`,
with the autoInit main head as its parent. ACP opens its PR into main, runs local verification
and required CI with Actions provenance, and merges it with a merge commit inside the bootstrap
(the public repository is created with autoInit=true; the default branch is never pushed directly); dev is
then created from the merged main and becomes the default branch.
Node CI checks out the exact PR head, or `github.sha` for push and merge-group events.
CommitLore is preferred because RF does not seed its hooks/configuration.

Input and outgoing bytes are secret-screened before writing. Git hooks remain enabled.
Unsupported verification stops compilation before repository creation. Currently this
ACP adapter admits the seeded Node tests; Python/Go/Rust CI
renderers remain available as library functionality, outside this supported bootstrap path.

## ACP entry (#19)

Hermes uses authenticated `run_create(kind="PROJECT_BOOTSTRAP", projectId=null)` and
`run_dispatch`; the bound bootstrap CTO submits the **full PLAN body** via `plan_submit`.
ACP owns independent review, CEO CONFIRM, write receipts/recovery, genesis execution,
exact-head verification and activation. Filenames alone are not a handoff.

PLAN acceptance is not completion. Require ACP activation readbacks for the repository,
genesis, matching manifest, tests/CI, resident interactive CTO with RC, fleet recovery,
claim and handoff ACK, and a dedicated Buzz identity/room with membership and delivery.
The CTO handoff must preserve the owner's product intent and queued issues. If deployed
ACP lacks genesis execution, stop before writes and report it. Do not publish through
the legacy local pipeline. See [SKILL.md](SKILL.md) for the complete interaction.

## Test and stage

```bash
ACP_SRC=/path/to/agent-control-plane/src python3 -m pytest -q
python3 scripts/stage_skill.py
```

Contract tests invoke the ACP commit in `governance/acp-contract.lock.json` through tsx.
ACP's parseGenesis checks normalized digests; planForSubmission expands RF's six operations
with PR open/verify/merge and backlog steps before producer preflightGitHubOperations.
Bundle tests also decode and hash each file, run the generated tests, and check ACP refuses
a direct push to main, a missing PR step and private-key-shaped content. ACP_SRC is required.
The Node install witness accepts both `# pass N` and `ℹ pass N` summary formats (#57).
These are local compatibility tests; they do not claim a live activation or first merge.

`.installed-skill-staging/` is a reviewable complete skill copy with SOURCE.json identifying
the RF checkout and hashes of shipped files. The staging command never installs into
`~/.claude/skills/repo-factory/`. Old receipt/publish helpers and retired kit assets remain
in this repository for historical tests, but are excluded from the installed skill surface.
