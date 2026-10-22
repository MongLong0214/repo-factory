# Shared contract: genesis bundle v1 (Repo Factory -> ACP PROJECT_BOOTSTRAP)

Owner decisions (2026-10-22): genesis = ONE direct push into the EMPTY new repository (created with autoInit=false);
branches main + dev, standard git flow (feature/* -> dev -> main; release/*, hotfix/*); PUBLIC repositories only;
no repo-local governance kernel (ACP is the sole merge/verification authority); issues + milestones + labels are created
so development can start immediately; changing an approved CI workflow later = independent review + CEO CONFIRM.

The bootstrap PLAN carries the bundle INLINE (bytes, not file paths: the receiving CTO has only ACP tools):
PLAN.genesis = {
  "schema": "acp.genesis-bundle.v1",
  "commitMessage": str,                       # the single genesis commit on main
  "files": [ { "path": str (repo-relative, no "..", no leading "/"), "mode": "100644"|"100755",
               "contentBase64": str, "sha256": "sha256:<hex of decoded bytes>" } ],
  "branches": { "main": "main", "integration": "dev" },   # dev is created from the genesis commit
  "labels": [ { "name": str, "color": "rrggbb", "description": str } ],
  "milestones": [ { "key": str, "title": str, "description": str } ],
  "issues": [ { "key": str, "title": str, "body": str, "labels": [str], "milestoneKey": str|null } ],
  "productIntent": { "summary": str, "references": [str], "ownerInstructions": [str] }   # carried into handoff
}
genesisDigest = "sha256:" + sha256(canonical JSON of PLAN.genesis with keys sorted, no whitespace) — computed by ACP's
schema-normalized parser; Repo Factory must produce the identical normalized form (test digest equality, not just parsing).

The project manifest in the same PLAN must:
- branchProfile: longLived ["main","dev"], defaultBranch "dev", mergeStrategy "merge_commit", releaseTagPolicy "semver".
- declare real verification commands that exist in the genesis files (e.g. the test command) and ciWorkflows whose
  approvedDigest = the bundle file's sha256 for that workflow path (ACP SHA-256 over bytes; never git-blob ids).
- commitlore: { "mode": "required" } only if the genesis includes what CommitLore needs; otherwise "preferred".
Secret screen: ACP refuses a bundle whose file bytes or issue/milestone text contain credential-shaped content.
