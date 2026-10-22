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
For a Node repository, a real verification list is `[{"id":"test","argv":["npm","test"]}]`.
The output includes the full normalized manifest plus `PLAN.genesis` with base64 file bytes,
SHA-256 digests, labels, milestones and issues. It follows the exact
[genesis bundle v1 contract](contracts/genesis-bundle-v1.md). Workflow approvedDigest is
SHA-256 over file bytes, never a git-blob ID. The genesis is one commit/direct push into an
empty public repository created with autoInit=false, main/dev at the same head and default dev.
CommitLore is preferred because RF does not seed its hooks/configuration.

Input and outgoing bytes are secret-screened before writing. Git hooks remain enabled.
Unsupported verification stops compilation before repository creation. Currently this
ACP adapter admits the seeded Node tests and optional syntax checks; Python/Go/Rust CI
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

Contract tests invoke ACP's own manifestDigest and planForSubmission through tsx. Bundle
tests compare normalized digests, decode and hash each file, and run the generated tests.
The Node install witness accepts both `# pass N` and `ℹ pass N` summary formats (#57).
These are local compatibility tests; they do not claim a live activation or first merge.

`.installed-skill-staging/` is a reviewable complete skill copy with SOURCE.json identifying
the RF checkout and hashes of shipped files. The staging command never installs into
`~/.claude/skills/repo-factory/`. Old receipt/publish helpers and retired kit assets remain
in this repository for historical tests, but are excluded from the installed skill surface.
