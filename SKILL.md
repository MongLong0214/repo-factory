---
name: "repo-factory"
description: "Compile an approved bootstrap request into a deterministic repository genesis — plan, external writes with receipts, a single genesis commit, and a result the control plane can accept. Stops at genesis; the long-running runtime belongs to the control plane."
---

# Repo Factory

**요청을 하나의 승인 가능한 Plan 으로 컴파일하고, 그 Plan 이 저장소가 되는 것까지만 한다.**

씨앗을 받아 문서를 쓰는 도구가 아니다. 승인된 `BootstrapRequest` 하나를 결정적인
Plan 으로 바꾸고, 그 Plan 이 지배하는 외부 쓰기·genesis 커밋·Result 까지 끌고 간다.
같은 요청은 같은 Plan digest 를 낸다 — 그것이 "승인이 무엇을 승인했는가" 를 말할 수
있게 하는 유일한 방법이다.

## 경계 — 여기서 끝난다

```
Repo Factory   요청 → Plan → 외부 쓰기 → genesis 커밋 → Result
Control Plane  그 뒤 전부 — 런, 티켓, 리뷰, 머지, 세션, 용량
```

**생성된 저장소에 운영 커널을 복제하지 않는다.** 저장소가 받는 것은 계약이다 —
포터블 매니페스트, 검증 명령, 브랜치 계약, CI. 그 계약을 읽고 오래 도는 것은
agent-control-plane 이고, 그쪽이 세션·권한·용량을 소유한다. 공장이 런타임을 같이
심으면 권위가 두 곳에 생기고, 두 권위는 언젠가 서로 다른 답을 낸다.

## 프로파일 — SIMPLE / STANDARD / GUARDED

되돌리기 쉬운가, 오래 유지될 것인가, 틀렸을 때 값이 비싼가. 요청이 고르고
`profiles/*.json` 이 각 프로파일이 **요구하는 산출물**을 정본으로 갖는다.

| 프로파일 | 이럴 때 | 추가로 요구하는 것 |
|---|---|---|
| `SIMPLE` | 프로토타입·CLI·실험·작은 OSS. 되돌리기 싸다 | — (매니페스트·README·AGENTS·CI·검증 명령·브랜치 계약·Result) |
| `STANDARD` | 오래 유지될 보통 프로젝트 | 명세 문서(compact PRD 또는 동급) |
| `GUARDED` | 보안·인증·프로토콜·마이그레이션·민감 데이터·연구급 | + 아키텍처 ADR · acceptance oracle · 롤백 전략 · 보안 검증 명령 |

CommitLore 는 `SIMPLE` 에서 `preferred`(실패 시 WARN), 나머지에서 `required`
(실패 시 REVISE)다. 프로파일이 요구하는데 만들지 못한 산출물은 조용히 빠지지 않고
`unresolvedGaps` 로 Plan 에 남는다.

## 파이프라인

```
BootstrapRequest ──▶ plan.py ──▶ apply.py --phase before-files ──▶ publish.py
                        │                                              │
                        │                                              ▼
                        └────────────────▶ apply.py --phase after-files
                                                       │
                                                       ▼
                                                   result.py
```

단계 순서가 의도다. `project-ci` 를 요구하는 ruleset 이 그 워크플로를 실어 나르는
커밋보다 먼저 존재하면, 저장소에 내용을 넣는 바로 그 푸시를 저장소가 거부한다.
그래서 ruleset 은 `after-files` 다.

### 1. 컴파일

```bash
python3 scripts/plan.py \
  --request request.json \
  --verification verification.json \
  --ci-values ci.json \
  --operation-id "$(uuidgen)" \
  --observe > compiled.json
```

`--operation-id` 는 호출자가 대야 한다. 매번 새로 만들면 같은 의도의 재시도가 **다른
Operation** 이 되고, 원장이 그것을 재개로 못 알아본다.

나오는 것: `planCore`(승인 대상) · `files`(올라갈 바이트) · `humanGate` · `unresolvedGaps`
· `diffSummary` · `--observe` 를 줬으면 `environmentObservation`.

### 2. 외부 쓰기

```bash
python3 scripts/apply.py --plan compiled.json --ledger receipts.json \
  --phase before-files [--dry-run]
```

각 Operation 은 자기가 만들 것을 `desiredState` 로 싣고 있다. 생성 파라미터가 Plan
밖에서 오면 승인된 digest 가 실행될 effect 를 결정하지 못한다 — private 으로 승인된
Plan 이 public 저장소를 만들어도 digest 는 같다.

쓰고 나서 다시 읽고, **승인된 상태와 대조**한다. 존재만 확인하면 `disabled` 로 만들어진
ruleset 이 `active` 로 만들어진 것과 같은 통과를 받는다.

### 3. genesis 커밋

```bash
python3 scripts/publish.py --plan compiled.json --workdir /tmp/genesis \
  --remote-url git@github.com:owner/name.git \
  --author-name "..." --author-email "..."
```

작업 디렉토리는 비어 있어야 한다. `git add -A` 는 거기 있는 것을 전부 담으므로,
남아 있던 파일 하나가 genesis 커밋에 섞이면 Plan 의 `contentDigest` 집합이 실제로
착지한 바이트를 더 이상 가리키지 않는다. 커밋과 푸시 사이에서 실제 경로 집합을
계획된 집합과 대조한다.

커밋에 세션 식별자를 남기지 않는다. 생성 저장소는 공개일 수 있고, 그 경우 트레일러는
저장소 안에 운영 정보를 넣는 것이 된다.

### 4. Result

```bash
python3 scripts/result.py --input result-input.json
```

제어평면이 받는 문서다. **활성화를 주장하지 않는다** — 공장은 저장소를 만들었을 뿐
그 저장소가 제어평면 위에서 돈다고 말할 수 있는 위치에 있지 않다. 받는 쪽이
그런 주장을 키 존재만으로 거부한다.

## 오너 게이트

되돌리기 쉬운 private setup 은 Hermes 권한이다. 아래는 아니다 — 컴파일러가
`authorization` 을 `OWNER` 로 올리고, `apply` 가 실행 직전에 **다시** 본다.
계획을 만든 코드와 실행하는 코드가 같은 가정을 공유하면, 그 가정이 틀렸을 때
아무도 안 막는다.

- 공개 노출 (public 저장소 생성, 또는 private → public)
- 되돌리기 어려운 파괴적 작업
- 오너가 요청서에 직접 표시한 사실

## 불변식

1. **못 본 것은 못 봤다고 말한다.** 원격을 확인하지 않은 관측은 `null` 이지 "없음" 이
   아니다. 둘을 같은 값으로 적으면 관측되지 않은 것이 관측된 것처럼 읽힌다.
2. **Plan 이 effect 를 결정한다.** 승인된 digest 밖에서 결정되는 생성 파라미터는 없다.
3. **쓰고 나서 다시 읽고 대조한다.** exit 0 은 원격이 기대대로라는 증거가 아니다.
4. **재개는 Operation 정체성에 걸린다.** 같은 `bootstrapOperationId` 와 같은
   `requestDigest` 일 때만 영수증이 재개로 읽힌다. 영수증은 과거에 썼다는 증거이지
   지금 있다는 증거가 아니므로, 재개할 때 원격을 다시 읽는다.
5. **조용한 누락 금지.** 프로파일이 요구하는데 못 만든 것은 `unresolvedGaps` 에 남는다.
6. **가드는 죽일 수 있어야 한다.** 모든 거부문에는 그것을 지웠을 때 죽는 테스트가
   `tests/test_guards_are_falsifiable.py` 에 행으로 있다. 없으면 CI 가 빨개진다.

## 구성

```
scripts/
  plan.py            요청 → Plan (자기 입력과 자기 출력을 둘 다 스키마로 검증)
  apply.py           단계별 외부 쓰기, 영수증 원장, 재조회 대조
  publish.py         계획된 파일 집합만 담은 genesis 커밋
  result.py          제어평면이 받는 Result
  materialize.py     프로파일 → 산출물 파일
  render_ci.py       스택별 CI 렌더
  canonical.py       정본 digest (volatile: forbid / strip / allow)
  github_port.py     gh CLI 포트 — 읽기와 생성만, 판단은 apply 가 갖는다
profiles/            SIMPLE · STANDARD · GUARDED 요구 산출물 정본
schemas/             request · plan · profile · result 계약
governance/          제어평면 계약 pin (exact commit)
templates/           CI · governance 템플릿
tests/               컴파일러·포트·계약 회귀 + 뮤테이션 하네스
```

## 아직 안 하는 것

- **issue · milestone · tag · setting 외부 쓰기.** 포트는 저장소와 ruleset 만 관측하고
  만든다. 다시 읽을 수 없는 쓰기는 §16.2 를 만족할 수 없으므로, 포트는 그 타입들을
  흉내내지 않고 거부한다.
- **기본 브랜치 전환의 외부 write 관리.** `publish` 가 푸시 순서로 다루고 있고,
  Operation 으로 승인되지는 않는다.
- **다중 저장소는 생성 단계까지다.** 순서 있는 다중 저장소 genesis 는 아직이다.
- **제어평면 PROJECT_BOOTSTRAP 런 안에서의 실행.** 공장 쪽 준비는 끝났고 제어평면 쪽
  진입점이 남아 있다.

## 레거시

`scripts/{phase-gate,create-issues,verify-citations,install-governance,run-canary}.py`,
`templates/kit/`, `references/` 의 대부분은 **은퇴한 제품**의 것이다 — S/M/L 티어,
생성 저장소에 심는 autopilot·merge-broker 운영 커널, repo-local 런타임 권위. 위
파이프라인은 그것들을 import 하지도 실행하지도 않는다. 아직 지우지 않은 이유는
제어평면 쪽 Trusted Validator 가 서기 전까지 참조로 남겨두기 위해서다.
**새 작업에서 그 경로를 실행하지 않는다.**
