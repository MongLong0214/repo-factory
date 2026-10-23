# Shared contract: genesis bundle v1 (Repo Factory -> ACP PROJECT_BOOTSTRAP)

Owner decisions (2026-10-22, amended 23:5xZ): genesis goes through a PR — NEVER a direct push to the default branch
(the CEO's standing rule "default branch direct push ... 절대 금지" stands; owner chose "초기 파일도 PR로").
The repository is created with autoInit=true (README on main); the genesis commit is pushed to branch `genesis`
(parent = the autoInit head), a PR genesis -> main is opened, verified (the manifest's local commands + CI at the
PR head, including required checks with Actions provenance) and merged by ACP inside the bootstrap (merge commit);
then `dev` is created from the merged main and becomes
the GitHub default branch;
branches main + dev, standard git flow (feature/* -> dev -> main; release/*, hotfix/*); PUBLIC repositories only;
no repo-local governance kernel (ACP is the sole merge/verification authority); issues + milestones + labels are created
so development can start immediately; changing an approved CI workflow later = independent review + CEO CONFIRM.

The bootstrap PLAN carries the bundle INLINE (bytes, not file paths: the receiving CTO has only ACP tools):
PLAN.genesis = {
  "schema": "acp.genesis-bundle.v1",
  "commitMessage": str,                       # the single genesis commit on branch `genesis`, merged by PR
  "files": [ { "path": str (repo-relative, no "..", no leading "/"), "mode": "100644"|"100755",
               "contentBase64": str, "sha256": "sha256:<hex of decoded bytes>" } ],
  "branches": { "main": "main", "integration": "dev", "genesis": "genesis" },  # dev from merged main; default = dev
  "labels": [ { "name": str, "color": "rrggbb", "description": str } ],
  "milestones": [ { "key": str, "title": str, "description": str } ],
  "issues": [ { "key": str, "title": str, "body": str, "labels": [str], "milestoneKey": str|null } ],
  "productIntent": { "summary": str, "references": [str], "ownerInstructions": [str] }   # carried into handoff
}
genesisDigest = "sha256:" + sha256(canonical JSON of PLAN.genesis with keys sorted, no whitespace) — computed by ACP's
parseGenesis parser; Repo Factory must produce the identical normalized form (test digest equality, not just parsing).

RF emits six operations: create repository, protect main, push genesis, create dev, protect dev, set default dev.
ACP's planForSubmission inserts PR open/verify/merge after push genesis and backlog creation after set default dev.
preflightGitHubOperations accepts that expanded execution plan; the unexpanded six operations are submission input.

The project manifest in the same PLAN must:
- use a resident-safe `projectId` matching `^[a-z0-9][a-z0-9._-]{0,80}$` (1–81 characters).
  The optional request `projectId` is honoured verbatim or refused; it is never normalized.
  Without it, derive only from `repositories[0].name`: lowercase, replace characters outside
  `[a-z0-9._-]` with `-`, trim leading/trailing `.`, `_`, `-`, truncate to 81 characters,
  and refuse if still invalid. The request schema pattern is checked against pinned ACP's
  `parseGenesis` resident rule. The GitHub name and remote `github:<owner>/<Name>` retain
  exact case, independently of this key; repository titles, CI, issues, product intent and
  the resident handoff must continue to refer to that repository and the owner's intent.
- branchProfile: longLived ["main","dev"], defaultBranch "dev", mergeStrategy "merge_commit", releaseTagPolicy "semver".
- declare real verification commands that exist in the genesis files (e.g. the test command) and ciWorkflows whose
  approvedDigest = the bundle file's sha256 for that workflow path (ACP SHA-256 over bytes; never git-blob ids).
- commitlore: { "mode": "required" } only if the genesis includes what CommitLore needs; otherwise "preferred".
Secret screen: ACP refuses a bundle whose file bytes or issue/milestone text contain credential-shaped content.
RF additionally requires labels, milestones and issues to each be non-empty.

ACP project ids are permanent primary keys; retirement does not free an id. Re-bootstrap
of the same repository after retirement requires a new explicit request `projectId`, e.g.
`freshreadme-2`. ACP links the retired project via `supersedes_project_id` using repository
identity. RF cannot see registry state at compile time and performs no automatic suffixing.
An invalid derived slug is a compile-time refusal. A valid derived or explicit id is passed
unchanged to ACP; if an existing project (active or retired) already owns it, ACP refuses the
collision when it registers the project. The operator must then supply a new explicit
`projectId`, for example `freshreadme-2`. Derived ids can also collide after normalization
or truncation; RF never silently chooses a different id.

RF reports local id refusal as `PROJECT_ID_INVALID: manifest.projectId`. The ACP bridge
reports only fixed safe categories: `ACP_SCHEMA: manifest.projectId`,
`manifest.verificationCommands`, `manifest.ciWorkflows`, `manifest.branchProfile`, or the
broader `manifest`/`genesis`. No rejected values, credentials, dynamic paths or raw ACP
messages are forwarded. Unrecognized bridge failures retain the generic ACP_SCHEMA error.
