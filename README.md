# repo-factory

승인된 부트스트랩 요청 하나를 결정적인 저장소 genesis 로 컴파일하는 스킬.

계획을 문서로 남기는 것이 아니라 **기계가 판정할 수 있는 상태**로 만드는 것이 목적이다.
"완료했습니다" 는 완료가 아니고, 확인하지 못한 것은 통과가 아니다.

## 무엇을 하는가

`BootstrapRequest` 를 받아 하나의 Plan 으로 컴파일하고, 그 Plan 이 지배하는 외부 쓰기와
genesis 커밋을 거쳐 제어평면이 받는 Result 까지 간다. 같은 요청은 같은 Plan digest 를
낸다 — 그것이 "승인이 무엇을 승인했는가" 를 말할 수 있게 하는 유일한 방법이다.

```
BootstrapRequest ──▶ plan.py ──▶ authorize.py ──▶ apply.py --phase before-files ──▶ publish.py
                        │                                              │
                        │                                              ▼
                        └────────────────▶ apply.py --phase after-files ──▶ result.py
```

## 여기서 끝난다

```
Repo Factory   요청 → Plan → 외부 쓰기 → genesis 커밋 → Result
Control Plane  그 뒤 전부 — 런, 티켓, 리뷰, 머지, 세션, 용량
```

생성된 저장소에 운영 커널을 복제하지 않는다. 저장소가 받는 것은 계약이다 — 포터블
매니페스트, 검증 명령, 브랜치 계약, CI. 그 계약을 읽고 오래 도는 것은
[agent-control-plane](https://github.com/MongLong0214/agent-control-plane) 이다.
공장이 런타임을 같이 심으면 권위가 두 곳에 생기고, 두 권위는 언젠가 서로 다른 답을 낸다.

## 설계 원칙

**Plan 이 effect 를 결정한다.**
Operation 은 자기가 만들 것을 `desiredState` 로 싣는다. 생성 파라미터가 Plan 밖에서
오면 승인된 digest 가 실행될 effect 를 결정하지 못한다 — private 으로 승인된 Plan 이
public 저장소를 만들어도 digest 는 같다. (`scripts/apply.py`)

**쓰고 나서 다시 읽고 대조한다.**
exit 0 은 원격이 기대대로라는 증거가 아니다. 존재만 확인하면 `disabled` 로 만들어진
ruleset 이 `active` 로 만들어진 것과 같은 통과를 받는다. (`scripts/apply.py`)

**확인하지 못한 것은 통과가 아니다.**
원격을 확인하지 않은 관측은 `null` 이지 "없음" 이 아니다. 둘을 같은 값으로 적으면
관측되지 않은 것이 관측된 것처럼 읽힌다. (`scripts/plan.py`)

**재개는 Operation 정체성에 걸린다.**
같은 `bootstrapOperationId` 와 같은 `requestDigest` 일 때만 영수증이 재개로 읽힌다.
영수증은 과거에 썼다는 증거이지 지금 있다는 증거가 아니므로, 재개할 때 원격을 다시
읽는다. (`scripts/apply.py`)

**genesis 커밋은 계획된 집합이고 그 이상이 아니다.**
작업 디렉토리가 비어 있어야 하고, 커밋과 푸시 사이에서 실제 경로 집합을 계획된 집합과
대조한다. (`scripts/publish.py`)

**가드는 죽일 수 있어야 한다.**
모든 거부문에는 그것을 지웠을 때 죽는 테스트가 행으로 있다. 뮤테이션이 더 이상 안
맞거나 가드 파일에 행이 없으면 그것도 실패다. (`tests/test_guards_are_falsifiable.py`)

## 구성

```
SKILL.md              스킬 진입점 — 경계·프로파일·파이프라인·불변식
scripts/
  plan.py             요청 → Plan (자기 입력과 자기 출력을 둘 다 스키마로 검증)
  authorize.py        승인자가 만드는 영수증. Plan digest 에 묶인다
  apply.py            단계별 외부 쓰기, 영수증 원장, 재조회 대조
  publish.py          계획된 파일 집합만 담은 genesis 커밋
  result.py           제어평면이 받는 Result
  materialize.py      프로파일 → 산출물 파일
  render_ci.py        스택별 CI 렌더
  canonical.py        정본 digest (volatile: forbid / strip / allow)
  github_port.py      gh CLI 포트 — 읽기와 생성만
profiles/             SIMPLE · STANDARD · GUARDED 요구 산출물 정본
schemas/              request · plan · profile · result 계약
governance/           제어평면 계약 pin (exact commit)
tests/                컴파일러·포트·계약 회귀 + 뮤테이션 하네스
```

## 실행

```bash
python3 scripts/plan.py \
  --request request.json --verification verification.json \
  --ci-values ci.json --operation-id "$(uuidgen)" --observe > compiled.json

python3 scripts/authorize.py --plan compiled.json \
  --authority HERMES --actor "hermes:ceo" > authorization.json

python3 scripts/apply.py --plan compiled.json --ledger receipts.json \
  --phase before-files --authorization authorization.json

python3 scripts/publish.py --plan compiled.json --workdir /tmp/genesis \
  --remote-url git@github.com:owner/name.git \
  --author-name "Repo Factory" --author-email "factory@example.invalid"
```

## 테스트

```bash
python3 -m pytest tests/ -q     # unittest discover 는 parametrize 를 건너뛴다
```

## 알려진 한계

- **외부 쓰기는 저장소와 ruleset 뿐이다.** issue·milestone·tag·setting 은 포트가
  흉내내지 않고 거부한다 — 다시 읽을 수 없는 쓰기는 재조회 요구를 만족할 수 없다.
- **기본 브랜치 전환이 Operation 으로 승인되지 않는다.** `publish` 가 푸시 순서로
  다룬다.
- **다중 저장소는 생성 단계까지다.** 순서 있는 다중 저장소 genesis 는 아직이다.
- **제어평면 PROJECT_BOOTSTRAP 런 안에서 실행된 적이 없다.** 공장 쪽 준비는 끝났고
  제어평면 쪽 진입점이 남아 있다.

## 레거시

`scripts/{phase-gate,create-issues,verify-citations,install-governance,run-canary}.py`,
`templates/kit/`, `references/` 의 대부분은 **은퇴한 제품**의 것이다 — S/M/L 티어,
생성 저장소에 심는 autopilot·merge-broker 운영 커널, repo-local 런타임 권위. 위
파이프라인은 그것들을 import 하지도 실행하지도 않는다. 새 작업에서 그 경로를 실행하지
않는다.

## 라이선스

MIT
