#!/usr/bin/env bash
# One real bootstrap, driven by the published commands and nothing else.
#
# The previous capture was assembled by hand from a session. That is why it could claim three
# finished bootstraps while every ledger held a single receipt: the stages after `apply` were
# dead on the command line, so nothing that produced those numbers could have been the pipeline.
# This script exists so the evidence and the product are the same thing — if a documented
# command does not work, this stops.
#
#   scripts/dogfood.sh <name> <profile> <stack> [remote-owner]
#
# Artifacts land in .rf-state/<name>/. Nothing here is a fixture: it creates a real repository.
set -euo pipefail

NAME="${1:?repository name}"
PROFILE="${2:?SIMPLE|STANDARD|GUARDED}"
STACK="${3:?node|python|go}"
OWNER="${4:-MongLong0214}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/.rf-state/$NAME"
mkdir -p "$OUT"
cd "$ROOT"

case "$STACK" in
  node)   CI_VALUES='{"RUNTIME_LOWER":"20","RUNTIME_LATEST":"22","INSTALL_CMD":"npm install","TEST_CMD":"npm test","BUILD_CMD":"node --check index.js"}'
          VERIFY='[{"id":"test","argv":["npm","test"],"repositoryRole":"primary","cwd":".","timeoutSeconds":600,"envAllowlist":["CI"],"network":"deny","required":true}]' ;;
  python) CI_VALUES='{"RUNTIME_LOWER":"3.9","RUNTIME_LATEST":"3.12","INSTALL_CMD":"python -m pip install -e .","TEST_CMD":"python -m pytest -q","BUILD_CMD":"python -m compileall -q ."}'
          VERIFY='[{"id":"test","argv":["python","-m","pytest","-q"],"repositoryRole":"primary","cwd":".","timeoutSeconds":600,"envAllowlist":["CI"],"network":"deny","required":true}]' ;;
  go)     CI_VALUES='{"RUNTIME_LOWER":"1.21","RUNTIME_LATEST":"1.23","INSTALL_CMD":"go mod download","TEST_CMD":"go test ./...","BUILD_CMD":"go build ./..."}'
          VERIFY='[{"id":"test","argv":["go","test","./..."],"repositoryRole":"primary","cwd":".","timeoutSeconds":600,"envAllowlist":["CI"],"network":"deny","required":true}]' ;;
  *) echo "unknown stack: $STACK" >&2; exit 2 ;;
esac

printf '%s\n' "$CI_VALUES" > "$OUT/ci.json"
printf '%s\n' "$VERIFY"    > "$OUT/verification.json"
python3 - "$OUT/request.json" "$NAME" "$PROFILE" "$STACK" "$OWNER" <<'PY'
import json, sys
path, name, profile, stack, owner = sys.argv[1:6]
json.dump({
    "schema": "repo-factory.bootstrap-request.v1", "runId": f"dogfood-{name}",
    "seed": f"a {stack} project bootstrapped by repo-factory's own pipeline",
    "bootstrapProfile": profile, "priority": "NORMAL",
    "repositories": [{"role": "primary", "name": name, "stack": stack}],
    "visibility": "private", "remoteOwner": owner, "origin": {"channel": "cli"},
}, open(path, "w"), ensure_ascii=False, indent=2)
PY

# The operation id is stable per repository. A retry has to be the same operation or the ledger
# cannot recognise it as a resume — a fresh uuid every run is how a retry becomes a second
# bootstrap of the same name.
if [ ! -f "$OUT/operation-id" ]; then uuidgen | tr 'A-Z' 'a-z' > "$OUT/operation-id"; fi
OPERATION_ID="$(cat "$OUT/operation-id")"

echo "── 1. compile"
python3 scripts/plan.py --request "$OUT/request.json" --verification "$OUT/verification.json" \
  --ci-values "$OUT/ci.json" --operation-id "$OPERATION_ID" --observe > "$OUT/compiled.json"
python3 -c "import json,sys; d=json.load(open('$OUT/compiled.json')); print('   digest', d['diffSummary']['planDigest']); print('   files ', len(d['files'])); sys.exit(1 if d['unresolvedGaps'] else 0)" \
  || { echo "   unresolved gaps — stopping"; python3 -c "import json;print(json.load(open('$OUT/compiled.json'))['unresolvedGaps'])"; exit 1; }

echo "── 2. approve"
python3 scripts/authorize.py --plan "$OUT/compiled.json" --authority HERMES \
  --actor "dogfood:repo-factory" > "$OUT/authorization.json"

echo "── 3. apply before-files"
python3 scripts/apply.py --plan "$OUT/compiled.json" --ledger "$OUT/receipts.json" \
  --phase before-files --authorization "$OUT/authorization.json" > "$OUT/apply-before.json"

echo "── 4. genesis push"
rm -rf "$OUT/work"
python3 scripts/publish.py --plan "$OUT/compiled.json" --workdir "$OUT/work" \
  --remote-url "git@github.com:$OWNER/$NAME.git" --ledger "$OUT/receipts.json" \
  --author-name "Repo Factory" --author-email "factory@users.noreply.github.com" > "$OUT/publish.json"
HEAD_SHA="$(python3 -c "import json;print(json.load(open('$OUT/publish.json'))['head'])")"
echo "   head $HEAD_SHA"

echo "── 5. apply after-files"
python3 scripts/apply.py --plan "$OUT/compiled.json" --ledger "$OUT/receipts.json" \
  --phase after-files --authorization "$OUT/authorization.json" > "$OUT/apply-after.json"

echo "── 6. verification, in the tree that was pushed, at that head"
python3 - "$OUT" "$HEAD_SHA" "github:$OWNER/$NAME" <<'PY'
import json, subprocess, sys
out, head, identity = sys.argv[1:4]
commands = json.load(open(f"{out}/verification.json"))
results = []
for command in commands:
    done = subprocess.run(command["argv"], cwd=f"{out}/work/{command['cwd']}",
                          capture_output=True, text=True, timeout=command["timeoutSeconds"])
    print(f"   {command['id']}: exit {done.returncode}")
    if done.returncode != 0:
        # A failing verification has no place in a result. Report it here rather than assembling
        # a document that cannot carry it.
        sys.stderr.write(done.stdout[-2000:] + done.stderr[-2000:])
        sys.exit(1)
    results.append({"commandId": command["id"], "repositoryIdentity": identity,
                    "exactHead": head, "status": "PASS"})
json.dump(results, open(f"{out}/bootstrap-verification.json", "w"), ensure_ascii=False, indent=2)
PY

echo "── 7. result"
python3 - "$OUT" "github:$OWNER/$NAME" <<'PY'
import json, sys
out, identity = sys.argv[1:3]
compiled = json.load(open(f"{out}/compiled.json"))
published = json.load(open(f"{out}/publish.json"))
json.dump({
    "runId": json.load(open(f"{out}/request.json"))["runId"],
    "plan": compiled["planCore"], "planDigest": compiled["diffSummary"]["planDigest"],
    "repositories": [{"role": "primary", "identity": identity,
                      "defaultBranch": published.get("defaultBranch", "dev"),
                      "createdBranches": sorted(published["remoteHeads"])}],
    "receipts": json.load(open(f"{out}/receipts.json")),
    "bootstrapVerification": json.load(open(f"{out}/bootstrap-verification.json")),
}, open(f"{out}/result-input.json", "w"), ensure_ascii=False, indent=2)
PY
python3 scripts/result.py --input "$OUT/result-input.json" \
  --verification "$OUT/verification.json" > "$OUT/result.json"

echo "── done: $OUT/result.json"
python3 -c "
import json; r=json.load(open('$OUT/result.json'))
print('   receipts  ', [x['operationId'] for x in r['externalWriteReceipts']])
print('   gaps      ', r['unresolvedGaps'])
"
