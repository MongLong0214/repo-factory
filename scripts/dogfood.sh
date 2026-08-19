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
# Public, because that is the shape these repositories take. It is not a test convenience:
# `visibility: public` makes the compiler classify the plan as OWNER-gated with a
# `public-exposure` reason, so the approval this drives is the one a public repository needs.
# A private dogfood would exercise a weaker authority than anything real.
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
  python) CI_VALUES='{"RUNTIME_LOWER":"3.9","RUNTIME_LATEST":"3.12","INSTALL_CMD":"python3 -m pip install -e .","TEST_CMD":"python3 -m pytest -q","BUILD_CMD":"python3 -m compileall -q ."}'
          VERIFY='[{"id":"test","argv":["python3","-m","pytest","-q"],"repositoryRole":"primary","cwd":".","timeoutSeconds":600,"envAllowlist":["CI"],"network":"deny","required":true}]' ;;
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
    "visibility": "public", "remoteOwner": owner, "origin": {"channel": "cli"},
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
# 공개 노출은 오너 게이트다(PRD §7 Phase F). 컴파일러가 `authorization: "OWNER"` 로
# 분류하므로 HERMES 영수증은 AUTHORIZATION_INSUFFICIENT 로 거부된다 — 그게 맞다.
python3 scripts/authorize.py --plan "$OUT/compiled.json" --authority OWNER \
  --actor "owner:isaac" > "$OUT/authorization.json"

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

echo "── 6. verification, against the bytes GitHub has at that head"
# 로컬 스테이징 디렉토리가 아니라 원격에서 다시 받아서 돌린다. 스테이징 디렉토리는
# 우리가 만든 것이고, 검증이 물어야 하는 것은 **거기 착지한 것이 도는가** 다. resume 에서는
# 스테이징 디렉토리가 아예 없기도 하다.
rm -rf "$OUT/verify"
git clone -q "git@github.com:$OWNER/$NAME.git" "$OUT/verify"
git -C "$OUT/verify" checkout -q "$HEAD_SHA"
python3 - "$OUT" "$HEAD_SHA" "github:$OWNER/$NAME" <<'PY'
import json, subprocess, sys
out, head, identity = sys.argv[1:4]
commands = json.load(open(f"{out}/verification.json"))
results = []
for command in commands:
    done = subprocess.run(command["argv"], cwd=f"{out}/verify/{command['cwd']}",
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

echo "── 7. CI on the pushed head"
# 생성 저장소가 자기 CI 를 통과하는지까지가 증거다. 여기 없으면 Result 는 "파일을 올렸다"
# 까지만 말하고, 그 파일들이 도는지는 아무도 확인하지 않은 채로 남는다.
python3 - "$OUT" "$OWNER/$NAME" "$HEAD_SHA" "github:$OWNER/$NAME" <<'CIPY'
import json, subprocess, sys, time
out, slug, head, identity = sys.argv[1:5]
deadline = time.monotonic() + 900
run = None
while time.monotonic() < deadline:
    listed = json.loads(subprocess.run(
        ["gh", "run", "list", "-R", slug, "--limit", "20", "--json",
         "status,conclusion,name,headSha,url"],
        capture_output=True, text=True, check=True).stdout)
    done = [r for r in listed
            if r["headSha"] == head and r["name"] == "project-ci" and r["status"] == "completed"]
    if done:
        run = done[0]
        break
    print("   waiting for project-ci ...")
    time.sleep(20)
if run is None:
    sys.exit("project-ci did not complete on the pushed head within the window")
print(f"   project-ci {run['conclusion']} {run['url']}")
if run["conclusion"] != "success":
    sys.exit(1)
workflow = subprocess.run(["git", "-C", f"{out}/verify", "hash-object",
                           ".github/workflows/project-ci.yml"],
                          capture_output=True, text=True, check=True).stdout.strip()
json.dump([{"repositoryIdentity": identity, "checkName": "project-ci", "head": head,
            "conclusion": "PASS", "workflowDigest": f"git-blob:{workflow}",
            "runUrl": run["url"]}],
          open(f"{out}/ci-evidence.json", "w"), ensure_ascii=False, indent=2)
CIPY

echo "── 8. result"
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
    # 수신자의 ciEvidence 스키마는 `.strict()` 다. `runUrl` 은 사람이 확인하라고 남기는
    # 것이므로 증거 파일에는 두고 Result 에는 넣지 않는다.
    "ciEvidence": [{k: v for k, v in row.items() if k != "runUrl"}
                   for row in json.load(open(f"{out}/ci-evidence.json"))],
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
