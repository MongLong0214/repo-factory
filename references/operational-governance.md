# 운영 거버넌스 — Autonomous Policy-Delegated Governance

Phase 4가 생성하는 모든 저장소에 이식되는 운영 커널의 정본 계약.
구현: `templates/kit/` (scripts + workflows) · 스키마: `templates/governance/` ·
설치: `scripts/install-governance.py` · 검증: `tests/`.

## 공식 거버넌스 모드 — 단 하나, 영구

```text
single_owner_policy_delegated_autonomy
```

- 임시 Bootstrap 이 아니다. 생성 시점부터 프로젝트 종료까지 의미가 바뀌지 않는다.
  `Bootstrap → post-C` 같은 mode switch 없음. 거버넌스를 만들기 위한 별도
  거버넌스 프로젝트(D0-004A/B/C류) 없음.
- 오너는 **Phase 4에서 한 번** 승인한다: Genesis Contract Bundle + GitHub
  external-write plan + 자율권 위임 범위. 이후 routine PR 승인은 없다.
- 기계는 "사람이 승인했다"고 가장하지 않는다. Genesis policy 가 위임한 범위에서만
  `policy_authorized=true` 를 주장할 수 있다.
- 정확한 자율성 = 위임된 범위는 무인 처리 + 범위 밖·모호함·위험은 정확한 이유로
  정지. 모든 상황에서 억지로 전진하는 것은 자율성이 아니라 fail-open 이다.

## 진실·권한·실행의 네 경계

| Authority | 정본 | 계산기/실행기 |
|---|---|---|
| Static Contract | `governance/policy.v1.json` · ADR · PRD · 티켓 metadata · 봉인 oracle | — |
| Technical Truth | Git tree/diff · PR facts · check facts · protection facts | `scripts/governance.py` |
| Policy-Delegated Execution | `policy.autonomy` | `scripts/autopilot.py` |
| Human Exception | HUMAN_DECISION_REQUIRED · SECURITY_QUARANTINE · POLICY_CHANGE_REQUIRED · EXTERNAL_CAPABILITY_MISSING | 오너 (이 상태에서 멈추는 것이 올바른 동작) |

**비권위 표면** (projection/audit 일 뿐 readiness 입력이 아니다): GitHub issue
state, labels, milestones, Roadmap/Board, PR comment 의 PASS 문자열, agent 이름,
모델 이름, 자가 작성 승인 문구.

## Policy SSOT

정본은 `governance/policy.v1.json` 하나다. 금지: resolver 하드코딩, 티켓/ADR 로
policy 복사, workflow 와 check 이름 중복 정의, 문서별 risk 정의 복제. 문서는 값
대신 policy path 를 가리킨다. 스키마와 cross-field 불변식(auto_merge ⊆
auto_start, any_registered_agent ⇒ raw credential 금지, critical_default 규칙,
PAT 금지 등)은 `templates/governance/policy.v1.schema.json` 과
`governance.py validate` 가 강제한다.

## 신뢰 경계와 자격증명 분리

- **Factory Setup Credential**: Phase 4 1회용 (repo 생성·protection/ruleset·
  issue sync). 이후 routine controller 가 보유 금지.
- **Runtime Merge writer = 로컬 controller (운영자 자격증명, target repo 밖)**:
  merge 자격증명은 운영자 machine 의 `gh`(또는 fine-grained token)에만 있고
  **target repo secret 에 저장하지 않는다**(같은 repo 의 PR 이 workflow 를 수정할
  수 있으므로). GitHub App 은 쓰지 않는다 — 단일 오너 모델에서 App 의 실익
  (운영자 토큰 유출 방어)이 작고, 핵심 불변식(agent 는 GitHub write 자격증명 0)은
  App 없이도 성립한다. Broker(`merge-broker.py`)는 candidate code 를 실행하지 않고
  requester verdict 를 믿지 않으며 branch protection/ruleset 을 우회하지 않는다.
- **Worker**: ticket branch 만. protected push/merge credential 없음. merge
  intent 제출은 가능(자기 PR 포함 — 단 독립 review/check green 없이는 broker 가
  거부). kernel/oracle 수정 불가.
- **Reviewer**: read-only. 입력은 immutable ticket digest·base/head SHA·diff·CI
  artifact 뿐. worker transcript 접근 금지, chain-of-thought 저장 금지.
- **Privileged workflow**: trusted integration branch 의 workflow 만 쓰기.
  `pull_request_target` 에서 PR code checkout 금지. check 의 creator app/workflow
  identity 를 API 로 검증.

## Role-Agnostic Evidence-Gated Merge

```text
누가 merge 를 요청했는가 ≠ merge 권한의 근거
merge 권한의 유일한 근거 = 현재 exact-head evidence + current policy
```

worker·reviewer·controller·specialist·scheduler·recovery — **등록된 어떤
에이전트든** exact PR/head 에 merge intent 를 제출할 수 있다. "CEO만 merge" 같은
역할 고정 규칙을 만들지 않는다. requester identity 는 audit 정보다. intent 제출은
check/review 를 만들지도 덮어쓰지도 않는다.

Merge intent: `templates/governance/merge-intent.v1.schema.json`.
`approved/authorized/checks_passed/review_passed/safe_to_merge` 필드는 schema 금지
— broker 가 계산한다.

**Merge Broker** (`scripts/merge-broker.py`)의 계약:
- unregistered caller 거부, intent TTL, deterministic idempotency key
- 같은 intent replay → duplicate merge 가 아니라 기존 receipt 반환
- 실행 직전 모든 predicate 재검사 (spec §14.3 전체 목록), head/base/policy/artifact
  변경 시 STALE
- queue 지원 시 queue candidate SHA 재검증, 미지원 시 strict up-to-date 직렬 merge
- 결과: `MERGED | QUEUED | DEFERRED | REFUSED | STALE | ALREADY_MERGED | UNKNOWN`
  — 조건이 덜 찼으면 DEFERRED, 잘못됐으면 REFUSED, facts 불완전이면 UNKNOWN(merge 금지)
- 실행 우선순위: GitHub native auto-merge → merge queue → broker exact merge API
  (sha 파라미터로 마지막 optimistic head check)
- merge method 는 merge commit 만. squash/rebase 비활성.
- single writer: merge requester 는 N, credentialed merge writer 는 repo 당 1
  (`concurrency: repo-merge-broker-<repo>` + writer lock)

## Governance Kernel 은 routine worker 가 수정할 수 없다

kernel = `governance/**`, `scripts/{governance,autopilot,merge-broker,install-governance}.py`,
`.github/workflows/{governance,autopilot,merge-broker,post-merge}.yml`(+ 구현
PR은 모든 workflow 수정 금지), `.github/PULL_REQUEST_TEMPLATE.md`, AGENTS.md 의
governance section. 수정은 `kind=governance-change, risk=critical` 티켓 전용이고
제품 코드와 섞을 수 없다.

**Contract self-expansion 방지**: 구현 PR은 자기 티켓 metadata·ADR/PRD·
owned_paths·oracle·risk·autonomy policy·required CI·governance scripts 를 수정할
수 없다. material contract change 는 `kind=contract-change` 별도 PR (제품 코드
혼합 금지, invalidates/supersedes 명시, merge+post-merge 검증 후에만 새 구현 시작).

## Acceptance Oracle 분리

티켓 metadata 가 `owned_paths / coordinated_paths / oracle_paths` 를 분리한다.
oracle 은 worker 가 수정할 수 없는 판정 경계다(secret 아님, immutable). 기존
oracle 수정·named case 개명/삭제는 contract-change 만 가능. high/critical 은 구현
시작 전 oracle 존재 필수. test count 0 = 실패(zero-test lane 금지).

## 한 원자 티켓 = 한 파일 = 한 PR

`docs/tickets/**/*.md` 파일당 정확히 하나의
`<!-- repo-governance-ticket:v1 {json} -->` metadata
(`templates/governance/ticket.v1.schema.json`). PR linkage 는 body 의
`Ticket: <ID>` 정확히 1줄 + hidden operation marker. 여러 PR 이 필요하면 티켓을
먼저 나눈다. Ticket-Completion/partial receipt/sub-ticket merge count 금지.
경로는 repository-relative POSIX, `..`/absolute/symlink escape 금지, glob 은
trailing `/**` 만, active 티켓 간 overlap 금지.

브랜치: `feat|fix/<ID>-slug`, `contract/<ID>-slug`, `governance/<ID>-slug`,
`revert/<ID>-slug`, `hotfix/<ID>-slug`, `release/<semver>`.

## Revert·Invalidation

과거 merge ancestry ≠ 현재 완료. Revert PR 도 원자 티켓(kind=rollback,
invalidates 필수)을 가진다. post-merge 실패 시: dependents 시작 금지 → controller
가 rollback ticket 생성(`autopilot.py rollback`) → revert PR → CI → 정책 범위 내
자동 merge → 원 티켓 invalidated → repair ticket. protected branch 직접 revert
push 금지.

## 위험도 기반 자율권 (요약)

| risk | 필수 evidence | 기본 |
|---|---|---|
| low | governance+ownership+focused+project-ci+non-vacuous | auto-start/merge, quorum 0 |
| standard | +full test/build lane, negative case, 독립 reviewer 1 PASS | auto-start/merge |
| high | +immutable oracle, property/mutation test, fail-open 반례, reviewer 2(상호 transcript 차단, 가능하면 타 provider), canary/live evidence, rollback 경로, `predelegated=true` | auto-start; auto-merge 는 predelegated 만 |
| critical | governance kernel·protection·release·secrets·배포·불가역 외부 행위 | `critical_default=halt` — 정확한 profile+rollback 을 Genesis 가 사전 승인한 경우에만 무인 |

high 인데 predelegated 아니면 `HUMAN_DECISION_REQUIRED`. 모호한 critical 을 AI 가
임의 전진하는 것은 fail-open 이다.

## State Machine

상태는 커밋하지 않고 매 reconciliation 마다 계산한다:
planned → ready → claimed → executing → pr_open → review ⇄ repair → ci →
merge_ready → merge_requested → merging → post_merge → verified. 종결:
invalidated/superseded. 예외: blocked, quarantined, human_decision_required,
unknown. 실패 매핑: API outage→unknown, ownership conflict→blocked, budget
소진→quarantined, policy gap→human_decision_required, post-merge 실패→rollback.

## Reconciliation·Claim·Retry·Budget

- 이벤트를 순서대로 믿지 않는다. 매번 policy/tickets/branches/PRs/checks/merge
  commits 를 전수 재계산 — 중복·역순·누락 webhook 에도 결과 동일.
- operation id = sha256(repository + ticket + dependency_verified_heads +
  policy_digest). branch/PR hidden marker 로 결박. 같은 operation rerun 은 기존
  branch/PR resume — duplicate 생성 금지.
- lease(TTL·heartbeat·attempt)는 coordination 일 뿐 readiness authority 아님.
  만료 시 무조건 새 PR 이 아니라 inspect 후 resume 또는 quarantine.
- 실패 분류: TRANSIENT(backoff, max 3) / REPAIRABLE(worker repair, ticket budget)
  / CONTRACT(정지, contract-change 필요) / SECURITY(quarantine) / POLICY(human).
  **테스트 assertion 실패를 transient 로 재실행해 숨기지 않는다.** CI rerun 은
  cancelled·runner lost·platform outage·pre-test infra timeout 만.
- Loop budget: repair rounds, wall time, cost, no-progress 반복 초과 → quarantined.
  무한 reviewer↔worker 대화 금지.

## Agent Adapter Contract

모델/provider 하드코딩 금지 — `templates/governance/agent-adapter.schema.v1.json`.
작업: execute/review/repair/cancel/health (stdin/stdout JSON). worker output 은
head_sha·changed_paths·commands_run·evidence 필수 — 자연어 보고만으로 완료 인정
금지. reviewer verdict 는 PASS|REVISE|BLOCK + exact head 결박, head 가 바뀌면
stale. high risk 에서 reviewer 2명 충돌 시 merge 금지.

## Workflows (templates/kit/.github/workflows/) — 전부 read-only evidence 생산자

target repo 의 workflow 는 **증거만 만든다**(GITHUB_TOKEN, 쓰기 최소). 자율 루프
(reconcile→claim→dispatch→merge intent→merge→rollback)는 **로컬 controller**가
운영자 자격증명으로 돌린다 — target repo 에 merge 자격증명을 두지 않기 위함이다.

| 파일 | check | 핵심 |
|---|---|---|
| governance.yml | `governance` | validate + check-pr, read-only |
| ci.yml | `project-ci` | lower+latest runtime matrix, aggregate |
| agent-review.yml | `agent-review` | reviewer quorum, read-only, artifact 업로드 |
| security-gate.yml | `security-gate` | profile 인지 security scan, read-only |
| post-merge.yml | `post-merge` | dev push full CI + OOB marker audit(fetch-depth 0) |

merge 실행: 로컬 `merge-broker.py execute --online` (public=native auto-merge/merge,
private=exact-sha + marker). reconcile/dispatch/rollback: 로컬 `autopilot.py`.

모든 action 은 full commit SHA pin (`governance.py validate` 가 강제).

## Genesis Bundle 과 한 번의 위임

Bundle = policy + ADR + PRD + ticket metadata + oracle inventory + adapter config
+ external-write plan + autonomy delegation. manifest 는
`governance.py manifest → governance/genesis-manifest.json`. 승인 receipt
(`repo-factory.genesis-approval.v1`)는 **repo 에 커밋하지 않는다** (validate 가
검출). receipt 는 정의된 backlog/risk/auto 범위/GitHub writes/예산을 한 번
위임하는 것이지 routine PR 에 재사용하는 승인 문자열이 아니다. policy 변경은
contract/governance-change 로만.

## Branch·Ruleset

dev(integration/default) + main(production) 둘 다 보호(public=ruleset): PR required,
approving review count 0(단일 오너 데드락 방지), strict required checks
(governance/project-ci/agent-review/security-gate/merge-gate), direct/force
push·delete 금지, admin 포함, bypass actor 없음, merge commit only. private Free 는
ruleset 미지원이므로 보완 통제로 대체(§GitHub Free Profile).

## Issue Sync

`scripts/create-issues.py --root <repo>`: 정본은 `docs/tickets/**` + policy.
issue body 의 `<!-- repo-governance-ticket:<ID> -->` marker 기준 0=create /
1=sync / 2+=fail-closed(exit 2). static label 만(risk:*, kind:*, epic:*),
dynamic `status:*` 금지, write 후 API reread, partial write 는 exit 1, API outage
exit 2.

## Observability

audit event 는 `templates/governance/audit-event.v1.schema.json`. deterministic
reason_code, chain-of-thought/secrets 금지, current state authority 아님. 지표:
cycle time, repair rounds, transient retries, quarantine, rollback, duplicate
prevented, cost, no-progress loops.

## Factory Version·Migration

각 생성 repo 는 `governance/factory-lock.json`(factory, version,
governance_schema, template_digest)을 가진다. 새 factory 버전은 기존 repo 를
조용히 바꾸지 않는다: upgrade --plan → migration manifest → governance-change
ticket(critical) → canary → merge. rollback 가능해야 한다.

## GitHub Free Profile (2026-08-08 하드닝 — 기능 매트릭스 정본)

정본: `templates/governance/github-free-capabilities.v1.json` (+schema). 각 생성
레포는 Genesis 시점 확정 `governance/github-profile.lock.json` 을 가진다
(동적 상태 아님 — 드리프트는 `scripts/github-profile.py verify` 가 잡는다).

| profile | 조건 | 강제력 |
|---|---|---|
| `FREE_PUBLIC_USER_NATIVE` | free + User + public | **native**: ruleset(active, bypass 0) · required checks(merge-gate 포함) · native auto-merge · CodeQL/secret scanning/dependency review. merge queue 는 **없음**(org 전용 — 시도 금지) |
| `FREE_PUBLIC_ORG_NATIVE_QUEUE` | free + Org + public | 위 + merge queue (fixture/선택 지원) |
| `FREE_PRIVATE_COMPENSATING` | free + private | **compensating only**: protected branch/ruleset/native auto-merge/CodeQL/secret scanning/attestation **호출 금지·성공 주장 금지**(403 실측). 보완: credential isolation + 외부 broker exact-sha merge + commit marker + OOB audit + merge 직렬화 |

분류 규칙: 403≠기능 미지원, 404≠plan 제한, timeout≠unavailable —
`github-profile.py` 가 `PLAN_MISMATCH / VISIBILITY_MISMATCH /
OWNER_TYPE_UNSUPPORTED / EXTERNAL_STATE_UNAVAILABLE / AUTHENTICATION_FAILED` 로만
분류한다. preview 기능(workflow execution protections 등)은
`DISABLED_EXPERIMENTAL` — gate 조건도, production-ready 근거도 아니다.

**Credential 구조**: target repo secrets 에 merge 자격증명(PAT/write token) 저장
금지(같은 repo PR 이 workflow 를 수정할 수 있다). merge writer 는 로컬 controller
(운영자 자격증명, target 밖)다. target workflow 는 read-only evidence 만: Actions
기본 `GITHUB_TOKEN` read-only + PR workflow write 0 + `pull_request_target` 금지 +
전 외부 action 은 `governance/actions-lock.v1.json` 과 일치하는 full-SHA
(`ACTION_NOT_IN_LOCK`/`ACTION_LOCK_DRIFT` 로 기계 강제).

**Public native merge**: 로컬 broker 가 evidence predicate 통과 후 exact head 에
`merge-gate`(commit status)를 세우고 native auto-merge 를 켠다 — **ruleset 을
우회해 merge API 를 직접 부르지 않는다.** head 가 바뀌면 이전 merge-gate 는 새
head 를 만족하지 못한다. agent 는 GitHub write 자격증명 0 이라 merge-gate 를
만들 수 없다(핵심 불변식).

**Private compensating merge**: 외부 broker 가 single-writer lock → 전수 재검증
→ REST merge(`sha=<exact head>` guard, merge_method=merge) → merge commit 에
`Repo-Factory-Operation/Ticket/Policy-Digest/PR-Head` marker. post-merge audit
(`merge-broker.py audit`, first-parent 체인)가 marker 없는 commit 을
`OUT_OF_BAND_WRITE` 로 검출 → dependent 중지·quarantine·(policy
`autonomy.auto_revert_out_of_band`) rollback 또는 HUMAN_DECISION_REQUIRED.
허용 예외: GENESIS · 명시 등록 migration.

**Security profile**: 공통 `security-gate` check (`governance.py security-scan`)
— SHA pin/lock, permissions 감사, secret-like diff, forbidden file, private
metadata, lockfile 일관성. public 은 + CodeQL default setup(미지원 언어는
`CODEQL_NOT_APPLICABLE_NO_SUPPORTED_LANGUAGE` — PASS 로 표시 금지)·dependency
review·secret scanning. private 은 native 를 시도하지 않고
`policy.security_commands` custom lane 만 — **null lane 은 NOT_APPLICABLE 이지
PASS 가 아니며**, high/critical 은 sast·dependency_audit 없이 auto-merge 금지
(`SECURITY_LANE_MISSING`). `UNAVAILABLE_BLOCKING` 은 절대 success 가 아니다.
Dependabot(alerts+security updates+`dependabot.yml`)은 모든 profile 활성이고
Dependabot PR 에 trusted shortcut 은 없다.

## 검증 수준 (검증 안 한 상위 수준을 주장하지 않는다)

```text
DESIGN_ONLY → LOCAL_VERIFIED
  → FREE_PRIVATE_COMPENSATING_VERIFIED   (native 주장 금지 · 9_9 발급 불가)
  → FREE_PUBLIC_NATIVE_VERIFIED          (ruleset 강제 + 로컬 controller merge-gate 전 항목)
  → FREE_PUBLIC_ORG_QUEUE_VERIFIED
  → MULTI_REPO_DOGFOOD_VERIFIED → 9_9_CANDIDATE
```

- LOCAL: `tests/` 전체 + fixture + fake gh (phase-gate v4 판정)
- profile 수준: 해당 profile 의 live canary v2 evidence
  (`repo-factory.canary-evidence.v2`, profile 필드 결박 — 다른 profile 의
  evidence 로 보증 이전 금지). `FREE_PRIVATE_COMPENSATING_VERIFIED` 에서 금지되는
  주장: GitHub-native enforced · branch-protected · ruleset-enforced ·
  unbypassable · 9.9 candidate.
- DOGFOOD: 실제 3 repo · 30+ lifecycle · unauthorized/false-verified/duplicate/
  wrong-check-source 전부 0 — `governance/evidence/dogfood.json`
- `9_9_CANDIDATE`: public native + dogfood + native enforcement drift 0.

## 가져오지 말 것 (이전 세대 규칙 — 부활 금지)

routine human merge authority · per-PR 오너 merge 조작 · Gate-Batch ·
PENDING/ACCEPTED registry PR · mode switch · Ticket-Completion · status-only PR ·
ready-set/current-SHA Markdown 커밋 · formal APPROVED review 요구 · policy 다중
복제 · literal file count/global test count gate · worker 의 자기 ticket/oracle/CI
수정 · fake gh 만으로 production-ready 판정 · 검증기만 있고 controller 없는 구조 ·
PAT automation · candidate code 에 write token · 모든 agent 에 raw merge
credential · merge requester 역할 고정 · requester identity 를 authorization 으로
사용 · 무한 retry/review loop.

> **최종 원칙 — 오너는 방향과 자율권을 한 번 위임한다. 구현자는 자기 판정 기준을
> 바꾸지 못하고 리뷰어는 구현을 수정하지 못한다. 등록된 어떤 에이전트든 merge
> intent 를 제출할 수 있지만, 실제 merge 는 역할이 아니라 current evidence 를
> 재검증하는 중립 Broker/Queue 가 수행한다. 상태는 매번 현재 사실에서 재계산하고,
> 실패는 복구하거나 정확히 멈춘다.**
