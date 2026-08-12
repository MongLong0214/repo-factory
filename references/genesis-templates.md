# Phase 3~4 — ADR / PRD / 티켓 템플릿

## Genesis Bundle 승인 receipt (2026-08-08 — artifact별 CEO PASS 를 supersede)

ADR·PRD·티켓 전량 + policy + oracle inventory + external-write plan + 자율권 위임
범위를 `governance.py manifest` 로 digest 하나로 묶어 **한 번** 승인한다. private
receipt(`repo-factory.genesis-approval.v1`)에는 manifest_digest,
external_write_plan_digest, autonomy_policy_digest, approved_by, approved_at 을
기록하고 **repo 에 커밋하지 않는다** (`governance.py validate` 가 검출). artifact
bytes 가 바뀌면 manifest digest 가 바뀌므로 이전 receipt 는 stale 이다. 이
receipt 는 routine PR 승인에 재사용하는 문자열이 아니다 — Genesis 후 merge 권한의
근거는 오직 current exact-head evidence + current policy 다. public repo 에는
내부 경로·세션·모델·라우팅 메타데이터를 쓰지 않는다.


## ADR

```markdown
# ADR-000N: <결정 한 줄>

- Status: Accepted (YYYY-MM-DD, <근거: 오너 지시 / 실험 결과>)
- Owner: CTO

## Context
왜 이 결정이 필요한가. 재현 실험이나 문헌 근거가 있으면 여기에.

## Decision
확정 사항. 표·코드 블록으로 구체적으로. 애매한 문장 금지.

## Rejected
- 대안 A | 왜 졌는지
- 대안 B | 왜 졌는지

## Consequences
이 결정이 만드는 후속 제약과, 다른 티켓에 거는 요구사항.
```

**ADR을 나중에 뒤집을 때** — 원본을 고치지 말고 새 ADR을 쓴 뒤 원본 상단에 배너를 단다. 어느 절이 대체됐고 어느 절이 살아 있는지 명시하는 것이 핵심이다:

```markdown
> ⚠️ **§1(명칭)은 [ADR-000M](ADR-000M-<slug>.md)로 대체됐다.** ...
> 이 문서에 남은 옛 표기는 결정 이력이므로 의도적으로 보존한다.
> **§2와 그 근거는 그대로 유효하다.**

- Status: Accepted (YYYY-MM-DD) · §1 Superseded by ADR-000M (YYYY-MM-DD)
```

기계 치환 대상에서 **이 두 문서를 반드시 제외**한다. 이력을 치환하면 무엇이 왜 바뀌었는지가 사라진다(`identity-and-renaming.md` §5.4).

**필수 ADR**
| 번호 | 주제 | 놓치면 생기는 일 |
|---|---|---|
| 0001 | 범위·기한 | 기한 압축 시 무엇을 잘랐는지 아무도 모름 → 조용한 누락 |
| 0002 | 언어·런타임·배포 | 에이전트마다 다른 스택으로 구현 |
| 0003 | SSOT | 파생물을 진실로 착각 → 동기화 지옥 |
| 마지막 | 검증·벤치 전략 | 효용 가설이 끝까지 미검증 |

---

## PRD (기능당 1개)

```markdown
# PRD F<n> — <기능명>

- Milestone: M<k> (기한) · ADR: 000N, 000M

## 목표
한 문단. 어떤 결함(D<n>)을 해소하는지 명시.

## 비목표
지금 하지 않는 것. → Backlog 이슈로 연결.

## 사용자 스토리
- <역할>로서, <행동>할 수 있다.

## 요구사항
1. 번호 매긴 구체 요구. 기계 검증 가능하게.

## AC
- [ ] 체크박스. 각 항목은 "무엇을 실행하면 무엇이 나오는가"로 쓴다.
```

---

## 티켓 (한 원자 티켓 = 한 파일 = 한 PR — ⚠️ 단일 몰아넣기 금지)

`docs/tickets/<ID>-<slug>.md` — 파일당 **정확히 하나**의 machine metadata 블록.
스키마 정본: `templates/governance/ticket.v1.schema.json` (`governance.py
validate` 가 강제: 경로 규칙, DAG, oracle 분리, named case 존재).

```markdown
# F1-001 — <제목> (M<k>)

<!-- repo-governance-ticket:v1
{
  "schema": "repo-governance.ticket.v1",
  "id": "F1-001",
  "title": "Parse input",
  "kind": "implementation",
  "risk": "standard",
  "predelegated": true,
  "milestone": "M1",
  "dependencies": [],
  "adr_refs": ["ADR-0002"],
  "prd_ref": "PRD-F1",
  "owned_paths": ["src/core/parser.ts", "test/parser.unit.test.ts"],
  "coordinated_paths": [],
  "oracle_paths": ["conformance/F1-001.acceptance.test.ts"],
  "acceptance": [
    {"id": "AC-F1-001-1",
     "test_path": "conformance/F1-001.acceptance.test.ts",
     "cases": ["parses a valid record"]}
  ],
  "commands": {"focused": "npm test -- parser", "full": "npm test",
               "build": "npm run build", "lint": null,
               "typecheck": "npm run typecheck",
               "manual": "LIVE_NA: pure deterministic parser"},
  "budgets": {"repair_rounds": 2, "wall_minutes": 60, "external_cost": null},
  "invalidates": [],
  "supersedes": []
}
-->

**목적**: 한 줄. exact symbols: `parseThing(input: string): Promise<Thing[]>`

**preconditions / 금지 범위**: 쓰면 안 되는 접근, 넘으면 안 되는 경로.

**RED 와 예상 실패**: 어떤 테스트가 어떻게 깨진 상태로 시작하는가.

**minimum GREEN**: acceptance ↔ named oracle case 1:1.

**rollback/invalidation**: 이 티켓이 뒤집힐 때 무엇을 invalidates 하는가.

**stop/escalation**: 어떤 상태에서 멈추고 무엇을 보고하는가.

**completion evidence**: worker output JSON 의 head_sha·changed_paths·commands_run.
```

규칙 요약: `owned_paths` 는 executable 티켓에서 비면 안 되고, `oracle_paths` 와
겹칠 수 없다(oracle 은 구현 PR이 수정 불가 — 변경은 contract-change PR).
governance kernel 경로는 `kind=governance-change, risk=critical` 만 소유한다.
rollback 은 `invalidates` 필수. glob 은 trailing `/**` 만.

**티켓 인덱스** `docs/tickets/TICKETS.md` — 기능별 파일 링크 표 + **의존성 그래프(크리티컬 패스)**. 그래프는 Phase 5 웨이브 설계의 입력이므로 반드시 그린다.

```
T-101 → T-102 → T-201 → T-203 → T-204 → ...
T-701 → T-702 (독립 트랙 — 최우선)
```

---

## repo-factory.json — GitHub Free profile 확정 (installer 입력)

Genesis config 에 GitHub 계정 사실을 명시한다. installer 가
`governance/github-profile.lock.json` 으로 굳히고, 이후 드리프트는
`scripts/github-profile.py verify` 가 잡는다.

```json
{
  "github": { "plan": "free", "owner_type": "User" },
  "repository": { "name": "owner/repo", "owner": "owner", "visibility": "private" },
  "security_commands": { "sast": null, "dependency_audit": null,
                          "secret_scan": "python3 scripts/scan-secrets.py --diff" }
}
```

visibility=private ⇒ profile `FREE_PRIVATE_COMPENSATING` — native 기능 호출 금지,
high/critical 은 sast·dependency_audit 없이 auto-merge 금지. public+User ⇒
`FREE_PUBLIC_USER_NATIVE` — ruleset/native auto-merge/CodeQL 강제, merge queue 없음.

---

## 마일스톤·이슈

```bash
# 마일스톤 (due_on은 ISO8601 UTC)
gh api repos/$R/milestones -f title="M1 · <이름>" -f due_on="2026-08-02T14:59:00Z" -f description="..."
gh api repos/$R/milestones -f title="Backlog (post-v0.1)" -f description="범위 컷 항목 — 조용한 드롭 금지"
```

이슈는 `scripts/create-issues.py --root <repo>` 사용 — 정본은 티켓 파일이고 이슈
body 의 `<!-- repo-governance-ticket:<ID> -->` marker 로 동기화한다(0 create /
1 sync / 2+ fail-closed). 라벨은 **static 만**: `kind:*`, `risk:*`, `epic:F<n>`
(+수동으로 `stretch`/`backlog` 가능). 동적 `status:*` 라벨 금지 — issue state 는
projection 이지 readiness 입력이 아니다.

---

## README 초판

이 시점의 README는 "무엇을 만들 것인가"다. 프로덕션 README(Phase 6)와 구분한다.
단, **지금 되는 것과 나중 것의 구분은 초판부터 지킨다** — 나중에 고치려면 4개 언어를 다 고쳐야 한다.

### ⚠️ README의 예제는 기계로 검증되게 만들어라

스펙·포맷·프로토콜을 정의하는 프로젝트라면 **README의 대표 예제가 자기 스펙을 위반하는 것**이 가장 비싼 결함이다. 사용자가 제일 먼저 복사하는 게 그 예제이고, 그걸 그대로 쓰면 도구가 거부한다.

실측 사례: 4개 언어 README의 대표 예제에 `Certainty: high` · `Blast: narrow` · `Undo: clean` 이 실려 있었다. 셋 다 스펙 enum 밖의 값이었고, **두 개는 프로젝트 자신의 거부 픽스처에 들어 있는 값**이었다. 산문 리뷰 두 번과 개명 한 번을 통과해서 살아남았다.

**따라서:**
1. 예제를 **적합성 픽스처로 승격**한다 (`spec/fixtures/valid/NN-readme-example.txt`).
2. 검증 스크립트가 README 블록과 픽스처의 **바이트 일치**를 확인하게 한다.
3. 어휘·필드 표가 있으면 그 표도 스펙과 대조한다(양방향: 스펙에 있는데 표에 없음 / 표에 있는데 스펙에 없음).
4. 번역본이 있으면 **전부** 검사한다. 코드 블록은 번역하지 않는 것이 규칙이고, 그 규칙을 검사로 강제한다.
5. 검사가 실제로 실패를 잡는지 **양쪽을 변조해서** 확인한다. 통과만 보고 넘어가면 아무것도 안 하는 검사를 배포한다.

일반화: **문서의 주장 중 기계로 확인 가능한 것은 전부 검사에 넣는다.** 수치는 로그에서, 예제는 픽스처에서, 명령어는 실행에서. 산문 리뷰는 이 셋을 잡지 못한다.

## CONTRIBUTING.md

README가 링크하면 반드시 만든다. 필수 항목: 프로젝트의 리뷰 기준(무엇이 PR을 반려시키는가), 커밋 규약(도그푸딩), 시작 지점.
