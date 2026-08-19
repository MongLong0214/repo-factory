# repo-factory

증거 기반으로 저장소를 생성하고, 생성된 저장소가 스스로 티켓 그래프를 돌리게 하는
Claude Code 스킬.

계획을 문서로 남기는 것이 아니라 **기계가 판정할 수 있는 상태**로 만드는 것이 목적이다.
"완료했습니다"는 완료가 아니고, 확인하지 못한 것은 통과가 아니다.

## 무엇을 하는가

씨앗(자연어 요청)에서 시작해 조사 → 도시에 → ADR → PRD → **티켓** → 저장소 창세까지
간다. 티켓은 추적용 메모가 아니라 의존성과 기계 판정 AC를 가진 **그래프 노드**다.

생성된 저장소에는 운영 커널이 설치되어, 그 뒤로는 티켓 그래프가 스스로 돈다.

```
tickets(dependencies) → compute_ready → 정책·위험도 필터 → dispatch
                                                              ↓
                                                        worker adapter
                                                              ↓
                                       governance CI / project CI / agent-review
                                                              ↓
                                                   merge-broker (exact-head)
                                                              ↓
                                            post-merge check @ merge SHA
                                                              ↓
                                                    technical_state = verified
                                                              ↓
                                                   의존 티켓이 ready 로
```

## 설계 원칙

**확인하지 못한 것은 통과가 아니다.**
`--offline`으로 못 본 원격 상태는 `NOT_CHECKED`이지 `PASS`가 아니다.
도구가 없으면 침묵 통과가 아니라 `FAIL`이다. (`scripts/phase-gate.py`)

**자연어 보고만으로 완료를 인정하지 않는다.**
워커 출력은 `status / operation_id / ticket_id / base_sha / head_sha / branch /
changed_paths / commands_run / evidence` 9필드를 갖춰야 한다.
누락되면 거부한다. (`templates/kit/scripts/autopilot.py`)

**상태를 따로 저장하지 않는다.**
티켓 상태는 GitHub facts(PR, check run, merge SHA)에서 매번 유도한다.
별도 상태 파일이 없으므로 드리프트가 생길 곳도 없다.

**머지 직전에 다시 확인한다.**
head/base staleness를 실행 직전 exact 재검사한다. 오래된 base 위에서 만들어진
변경이 조용히 머지되지 않는다. (`templates/kit/scripts/merge-broker.py`)

**사이클과 끊어진 참조를 창세 시점에 잡는다.**
의존성 사이클(`TICKET_DAG_CYCLE`)과 존재하지 않는 의존(`TICKET_DEP_MISSING`)은
색칠 DFS로 검출한다. (`templates/kit/scripts/governance.py`)

## 구성

```
SKILL.md                              Phase 0~6 흐름과 게이트
references/                           실행 프로토콜, 도시에 규격, 템플릿
scripts/
  phase-gate.py                       로컬·원격 게이트와 assurance 등급
  install-governance.py               운영 커널 설치
  create-issues.py                    티켓 → GitHub 이슈 동기화 (정본은 티켓)
  run-canary.py                       실제 저장소로 전 경로 카나리
  verify-citations.py                 인용 검증
templates/kit/scripts/
  autopilot.py                        ready 계산, dispatch, lease, 복구, 롤백
  merge-broker.py                     머지 중재, exact-head, idempotency
  governance.py                       계약 검증, 온라인 상태 유도, manifest
tests/                                컴파일러·포트·계약 회귀
```

## 실행

```bash
# 생성된 저장소에서
python3 scripts/autopilot.py reconcile --root . --online
```

```json
{
  "state": "reconciled",
  "ready": ["T-02", "T-05"],
  "startable": ["T-02"],
  "held": [{"ticket": "T-05", "reason_code": "RISK_NOT_DELEGATED", "risk": "high"}],
  "blocked": [{"ticket": "T-07", "waiting_on": ["T-02"]}],
  "progress": {"verified": 3, "remaining": 6, "total": 9},
  "critical_path": {"depth": 4, "chain": ["T-02", "T-07", "T-09", "T-12"]},
  "adapter": {"state": "UNWIRED", "unwired": [{"adapter": "default", "operations": ["execute"]}],
              "detail": "execute 가 비어 있어 dispatch 가 아무것도 실행하지 않는다"},
  "wip": {"cap": 3, "active": []}
}
```

`critical_path.depth`가 남은 최소 라운드 수다. 병렬 폭을 늘려도 이 값은 줄지 않는다.

`adapter.state`가 `UNWIRED`면 `startable`이 있어도 실행되지 않는다.
`install-governance.py`는 `invoke`를 비운 채 설치하고 운영자가 채우기를 기대한다.

## 테스트

```bash
python3 -m pytest tests/ -q     # unittest discover 는 parametrize 를 건너뛴다
```

## 알려진 한계

- **워커 어댑터는 기본 미배선이다.** `governance/adapters/*.json`의 `invoke.execute`를
  채워야 자율 실행이 돈다. 채우기 전까지 이 시스템은 계획하고 검증하는 그래프이지
  실행하는 런타임이 아니다. `reconcile`이 그 상태를 명시한다.
- **격리는 브랜치 수준이다.** git worktree 격리와 API/타입/픽스처 같은 semantic
  conflict 검출은 없다. 소유 경로(`owned` / `coordinated` / `oracle`) 선언으로만 다룬다.
- 실행 telemetry 기반 자기 최적화는 없다.

## 라이선스

MIT
