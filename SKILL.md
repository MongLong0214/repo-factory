---
name: "repo-factory"
description: "Create or repair Phase 0–4 repositories with evidence, one-time Genesis-gated planning, verified GitHub setup, CommitLore, and an autonomous policy-delegated governance kernel (role-agnostic evidence-gated merge)."
---

# Repo Factory

아이디어를 실행 가능한 GitHub 레포로 만드는 공장. **Phase 0부터 4까지만** —
씨앗을 받아 ADR·PRD·티켓·이슈·브랜치·**운영 커널**까지 만든다. Phase 4가 끝나면
일상 개발(ready 계산→claim→구현→독립 리뷰→CI→merge intent→자동 merge→post-merge
→verified→다음 티켓)은 레포에 이식된 자율 루프가 사람 개입 없이 돈다 — 공장이
아니라 **생성된 레포 자신**이 돌린다. → `references/operational-governance.md`

## 20초 요약 — 들어가는 것 / 나오는 것

| 입력 | 산출물 |
|---|---|
| 레퍼런스 레포 · 논문 · 아이디어 한 줄 | ADR(≥1) · PRD + 구현착수급 원자 티켓(metadata 포함) · GitHub 이슈(marker 동기화) · `dev`/`main` 브랜치 + 보호 규칙 · **운영 커널**(policy SSOT · governance/autopilot/merge-broker 스크립트 · 워크플로 6종) · (M/L) 근거 도시에 |

## 크기부터 고른다 — S / M / L

되돌리기 쉬운 주말 아이디어인가, 근거를 붙여야 하는 표준 기능인가, 레퍼런스·논문에서
출발하는 큰 결정인가 — **직접 고른다.** 헷갈리면 한 단계 위로 올린다.

| 티어 | 이럴 때 | 생략하는 Phase | 시간 |
|---|---|---|---|
| **S** | 되돌리기 쉬운 작은 저장소. 근거 도시에 없이 빠르게 착수하되 계획 gate는 유지 | Phase 1(증거 수집) · Phase 2(도시에) | 최소 산출물 기준 |
| **M** | 표준 기능/프로젝트. 근거는 필요하지만 전면 계보 조사는 불필요 | Phase 1 축약(반증 C·재현 D 필수, A·B·E 선택) | 표준 산출물 기준 |
| **L** | 레퍼런스 레포·논문에서 출발, 오리지널리티 판단과 전면 근거 수집 필요 | 없음 — 전체 시퀀스 | 전체 산출물 기준 |

`scripts/phase-gate.py 4 --tier {S,M,L}`가 티어를 안다. **모든 티어는 최소 ADR·PRD·
원자 티켓·README·AGENTS·CI·이슈를 요구한다.** S만 CONTRIBUTING·마일스톤·
크리티컬 패스를 생략할 수 있다. `--tier`를 안 넘기면 기본값은 `L`이다.

각 티어가 실제로 만드는 것:
- **S** — ADR 1개 · micro-PRD 1개 · 원자 티켓 1개 이상 · README.md · AGENTS.md ·
  최소 CI · 이슈 소수 · `dev`/`main` 브랜치 + 보호. Phase 0 질문은 한 번에 묶는다.
- **M** — S 전부 + PRD(기능별) · 기능별 티켓 파일 · CONTRIBUTING.md · 마일스톤 ·
  CI 스켈레톤 · 축약 도시에 + 오너 컨펌 게이트.
- **L** — M 전부 + 학술 계보·경쟁 조사·반증 탐색 전량 + 재현 실험 + 정체성(이름)
  실측 ADR.

> **라우팅 권위:** 위임 worker/provider/model/effort/runtime/tool/session topology와
> 병렬화는 현재 작업에서 오너가 가장 최근에 명시한 지시만 따른다. 고정 모델·고정
> 실행환경을 가정하지 않는다. provider가 지정되지 않았을 때만 가용성·품질·속도로
> 선택한다. 지정 provider가 불가하면 조용히 대체하지 말고 evidence와 함께 차단한다.

---

## 파이프라인

```
Phase 0 씨앗 접수 ──▶ 1 증거 수집(M/L) ──▶ 2 도시에 + 오너 컨펌(M/L) ──▶
3-A 정체성 ──▶ 3 결정(ADR) ──▶ 4 레포 창세 ──▶ (공장은 여기서 끝난다 — 프로젝트가 이어받는다)
```

---

## 불변식 (모든 Phase에 적용 — 위반 시 그 Phase는 미완료)

1. **증거 없는 주장 금지.** 결함은 *재현*하고, 인용은 *API로 검증*하고, 수치는 *실행 로그*에서만 가져온다. "아마", "~일 것이다" 금지.
2. **반증을 먼저 찾는다** (M/L). 아이디어를 지지하는 자료만 모으면 공장은 쓰레기를 찍어낸다. 반대 증거 탐색은 선택이 아니라 필수 단계다(§Phase 1-C). S는 Phase 1 자체를 생략하므로 해당 없음.
3. **티켓은 모든 티어에서 구현 착수급 원자 단위다.** 정확한 파일·심볼, 선행조건, 금지 범위, RED와 예상 실패, 최소 GREEN, acceptance↔test 1:1, focused/full/release/manual 검증, stale-evidence 무효화, 중단·escalation 조건과 완료 evidence가 없으면 티켓이 아니다.
4. **조용한 누락 금지.** 범위에서 자른 것은 전부 Backlog 이슈로 남긴다.
5. **오너 게이트는 건너뛸 수 없다 — 그리고 딱 세 번이다.** ① 누락된 Phase 0 결정 4가지는 한 번에 확정 ② M/L 도시에 승인 ③ **Genesis Contract Bundle 한 번의 승인**(Phase 4): policy + ADR 전량 + PRD 전량 + 티켓 metadata 전량 + oracle inventory + GitHub external-write plan + 자율권 위임 범위를 `governance.py manifest` digest 로 한 묶음 승인한다. 승인 receipt 는 repo 에 커밋하지 않는다. 바이트가 바뀌면 manifest digest 가 바뀌므로 해당 승인은 stale 이다. **Genesis 이후 routine PR 에는 오너 승인이 없다** — merge 는 evidence 와 policy 만으로 판정되고, 사람이 필요한 상태(HUMAN_DECISION_REQUIRED 등 4종)에서는 멈추는 것이 올바른 동작이다. 공개 전환·배포는 여전히 별도 승인이다.
6. **위임은 선택 사항이다.** 현재 작업 지시가 허용할 때만 fresh isolated closed packet으로 사용하고, 목표·exact source·소유 파일·acceptance·검증·금지 범위를 고정한다. 스폰 성공이나 보고는 증거가 아니다. 산출물과 검증 결과를 직접 재조회한다.
7. **도그푸딩은 관행이 아니라 게이트다.** 프로젝트가 정의하는 규약을 자기 산출물에 적용하는 **검사**를 만들고 모든 티어의 CI에 넣는다. 범위는 산출물에서 유도하고(하드코딩 금지), 검사가 못 돈 경우는 실패로 보고하고, 위반 유형마다 뮤테이션으로 검출을 증명한다. 실패의 해석은 둘뿐이다 — *산출물이 틀렸다* 또는 *규칙이 틀렸다*. **단언을 약화시켜 초록을 만드는 것은 둘 중 어느 것도 아니다.** → `references/dogfooding-loop.md`
8. **정체성은 코드보다 먼저 굳힌다.** 이름·어휘 결정은 미룰수록 기하급수적으로 비싸진다(§Phase 3-A). 가용성은 실측하고, 결정 이력은 절대 기계 치환하지 않는다.
9. **라우팅 실패는 fail-closed다.** 오너가 provider/runtime을 지정했으면 불가 evidence를 보고하고 대체 승인을 기다린다. 지정하지 않았을 때만 직접 수행 또는 다른 가용 경로를 선택하며 범위와 gate는 유지한다.
10. **결정은 ADR과 CommitLore로 남긴다.** 모든 ADR에 `Rejected:` 절을 넣고, 이 스킬이 만드는 모든 Git 저장소에는 Phase 4에서 **CommitLore를 기본 활성화**한다. 첫 프로젝트 커밋 전에 `commitlore init`과 `commitlore doctor`가 exit 0이어야 하며, 편집 전 `commitlore context <path>`, 의존성·서비스·접근 제안 전 `commitlore guard --proposal "<proposal>" -- <path>`, 커밋 전 `commitlore validate --message-file <file>`을 사용한다. 모든 커밋에 억지 기록을 만들지는 않는다. diff만으로 보존되지 않는 제한·기각 대안·경고가 있을 때만 CommitLore trailer를 남긴다. CommitLore가 없거나 초기화·검증이 실패하면 조용히 건너뛰지 말고 그 Phase를 미완료로 판정한다. 사용 중 CommitLore 자체의 재현 가능한 결함을 발견하면 아래 Phase 4의 **CommitLore 결함 등록 흐름**에 따라 `MongLong0214/commitlore`에 중복 없는 이슈를 등록하고 재조회 검증한다. 목적은 이미 검토하고 버린 접근이 다른 사람·세션에서 근거 없이 부활하는 것을 막는 것이다.
11. **단계 완료는 명령으로 판정한다.** 체크리스트는 요약으로 읽히고, 명령은 정지선으로 읽힌다. 완료를 명령으로 검사할 수 없는 Phase는 완료 여부가 의견인 Phase다.
12. **GitHub Free 의 public/private 는 같은 보증이 아니다** (2026-08-08 하드닝). 모든 생성 레포는 Genesis 에 `governance/github-profile.lock.json` 으로 profile 을 확정한다: public 은 **native 강제**(active ruleset·bypass 0·required checks integration-id 결박·native auto-merge·CodeQL/secret scanning — merge queue 는 org 전용이라 개인 계정에 시도 금지), private 은 **보완 통제만**(protected branch/ruleset/native auto-merge/CodeQL/secret scanning/attestation 호출·성공 주장 금지 — 403 실측 근거). 보완 통제 = credential isolation(agent write token 0, target repo 에 broker key 0) + 외부 broker exact-sha merge + commit marker + OUT_OF_BAND_WRITE audit + merge 직렬화. `GITHUB_TOKEN` merge 금지(post-merge 미트리거). 전 외부 action 은 `governance/actions-lock.v1.json` 결박 full-SHA, PR workflow write 권한 0, `pull_request_target` 금지. NOT_APPLICABLE 을 PASS 로 표시하지 않는다. → `references/operational-governance.md` §GitHub Free Profile
13. **거버넌스 모드는 하나뿐이고 영구다.** 생성되는 모든 레포는 `single_owner_policy_delegated_autonomy` 로 태어난다. policy SSOT 는 `governance/policy.v1.json` 하나(복제 금지), merge 권한의 유일한 근거는 현재 exact-head evidence + current policy 다. 등록된 어떤 에이전트든 merge intent 를 제출할 수 있지만(역할 고정 금지), 실제 merge write 는 branch protection/ruleset 을 우회할 수 없는 GitHub native auto-merge/queue 또는 **로컬 controller(운영자 자격증명, target repo 밖)**만 수행한다 — merge 자격증명은 target repo secret 에 두지 않는다(PAT 금지). 구현 PR 은 자기 티켓·oracle·CI·governance kernel 을 수정할 수 없다. → `references/operational-governance.md`
14. **못 본 것은 못 봤다고 말한다.** 모든 검사는 자신이 답하려면 무엇이 있어야 하는지(입력만·레포·전후 상태·이 프로젝트의 티어)를 선언한다. 그 정보가 없거나 그 티어가 요구하지 않아 일부를 확인하지 못했다면 조용히 통과가 아니라, **통과와 구별되는 출력으로** 못 봤다고 밝힌다 — exit 0과 침묵은 답이 아니다. 같은 검사를 맥락이 다른(레포 없음·이력 절단·깨진 입력) 두 번 돌렸을 때 출력이 우연히 같다면 그것부터 의심한다. 이 원칙은 특정 도구가 아니라 **이 스킬이 내놓는 모든 검사**(게이트·CI 스텝·검증 스크립트)에 적용된다 — `scripts/phase-gate.py`의 티어별 `SKIP` 표기, `create-issues.py`/`verify-citations.py`의 `ERROR:` 종료가 그 실천이다. → `references/self-improvement-loop.md` §무언의 스킵

---

## Phase 0 — 씨앗 접수 (10분, 모든 티어)

입력 유형을 판정하고 작업 규모를 고정한다.

| 입력 | 첫 행동 |
|---|---|
| GitHub 레포 URL | `git clone --depth 50` + `gh api repos/{owner}/{repo}` (★·포크·기여자·라이선스·최근 push). **실체 파일 수와 실행 가능 코드 유무를 먼저 센다** — README가 크다고 프로젝트가 큰 게 아니다 |
| 논문 URL/ID | **실존 검증 먼저** (arXiv API / Crossref). 존재하면 전문 수집, 없으면 "미확인"으로 명시하고 진행 |
| 아이디어 문장 | 가장 가까운 선행 사례 3개를 찾아 좌표를 잡는다 (S는 5분 안에 끝낸다) |
| 참고자료 동봉(X 글·블로그) | 차단 사이트면 `insane-search` 스킬 경유. 원문 확보 실패 시 미러·자매편으로 우회하고, 실패는 실패로 보고 |

### Phase 0 필수 질문 4가지 — 하나라도 빠뜨리면 나중에 전면 재작성이다

늦게 물어서 치른 대가가 실제로 있는 것만 남겼다. 넷 다 **여기서** 묻는다 —
S든 L이든 예외 없다.

| 질문 | 늦게 물으면 치르는 비용 |
|---|---|
| **기한** | 4주 설계와 1일 설계는 범위·아키텍처가 통째로 다르다 |
| **수익 모델** (유료/무료/미정) | 보고서의 사업화 섹션 전체 재작성 |
| **공개/비공개** | 라이선스·시크릿 처리·README 톤이 전부 달라진다 |
| **오리지널리티** ⚠️ | 아래 참조 — 가장 비싼 항목 |

#### 오리지널리티 게이트 ⚠️ 필수 (레퍼런스에서 출발하는 M/L)

레퍼런스 레포·논문에서 출발하는 경우 **반드시** 묻는다:

> "이 프로젝트는 레퍼런스의 **개선판**입니까, 아니면 **독립 설계**입니까?
> 독립 설계라면 이름과 핵심 어휘를 처음부터 자체 유도하고, 레퍼런스는 참고문헌으로만 인용합니다."

**실측 비용**: 이 질문을 문서가 다 만들어진 뒤에 받은 사례에서, README 4개 언어 + ADR 7 + PRD 8 + 티켓 9 + GitHub 이슈 28건을 전부 다시 만졌다. 어휘가 프로토콜의 본체인 프로젝트였으므로 스펙 초안·픽스처·하니스 작업도 폐기됐다.

"독립 설계"면 Phase 3에서 **정체성 결정(§Phase 3-A)** 을 ADR로 굳히고, 계승 어휘는 그 시점에 전부 재유도한다. 코드가 0줄일 때 30분이면 되는 일이다.

---

## Phase 1 — 증거 수집 (M: C·D만 필수, A·B·E 선택 · L: 전량 병렬 · S: 생략)

필수 lane을 실행한다. 병렬화 수준과 실행 주체는 현재 작업의 라우팅 지시를 따른다. 상세 프로토콜: `references/research-protocol.md`

### A. 학술 계보 (리서치 lane) — L 필수, M 선택
문제의 40년 계보를 8~10축으로 수집. 축당 4~8편, 핵심(seminal) + 최신(최근 3년) 균형.
**모든 인용은 `scripts/verify-citations.py`로 Crossref/arXiv API 대조** — 날조·오매칭(재출판본이 원판을 가리는 경우 등)을 기계로 잡는다.

### B. 경쟁·선행 제품 (리서치 lane) — L 필수, M 선택
같은 문제를 푸는 상용 제품·OSS·학술 시스템. 가격·조달·규모 신호까지. **가장 가까운 인접자(nearest neighbor)를 반드시 찾아라** — 없다고 결론 내리기 전에 3가지 다른 검색 각도를 써라.

### C. 반증 증거 ⚠️ M/L 필수 (직접 수행, 위임 금지)
"이 아이디어가 이미 실패한 기록"을 적극적으로 찾는다. 검색어 예: `negative results`, `pitfalls`, `does not outperform`, `harmful`, `ablation`.
찾은 반증은 숨기지 말고 보고서에 **가장 불리한 증거부터** 싣고, 설계가 그 실패 조건을 어떻게 뒤집는지로 답한다. 답할 수 없으면 그건 프로젝트의 존재론적 리스크이고 최우선 검증 대상이 된다.

### D. 재현 실험 (직접 수행) ⚠️ M/L 필수, 최고 가치 단계
레퍼런스의 주장을 **격리 환경에서 직접 깨뜨려 본다.** git 저장소든 CLI든 API든, 임시 환경을 만들어 실제로 실행하라.
- 재현된 결함에는 `재현됨` 표기 + 명령어·출력 로그를 부록으로
- 재현 실패한 의심은 "의심"으로 남기고 결함으로 승격하지 않는다
- 이 단계가 보고서의 신뢰도를 만든다. 남의 주장을 받아쓴 보고서는 누구나 만든다

### E. 인접 개념 차용 (오너가 자료를 줄 때) — L 필수, M 선택
각 자료마다 **차용/기각 판정표**를 만든다: `{개념, 판정, 귀속된 설계 위치}`. 기각도 이유와 함께 기록 — 나중에 같은 논의가 반복되는 것을 막는다.

---

## Phase 2 — 도시에 + 오너 컨펌 게이트 (M/L 필수 · S는 생략)

단일 HTML 아티팩트로 발행한다. 사용 가능한 문서/아티팩트 도구가 있으면 사용하고, 없으면 repo-local HTML을 생성해 브라우저로 검증한다. 구조·필수 섹션: `references/dossier-spec.md`. M은 아래 골격을 축약해도 되지만(예: 학술 지형·차용 검토 보론 생략), 결함 목록·반증·오너 컨펌 게이트는 M도 유지한다.

필수 골격 (L 전체):
1. **Executive Summary** — 실체 / 그럼에도 옳은 것 / 고도화 방향
2. **대상 해부** — 실체 목록, 약속 대비 구현 매트릭스, **결함 목록(재현 증거 포함)**, 그럼에도 옳은 것
3. **학술 지형** — 계보, 경쟁 삼각 구도, **반증 증거와 설계의 응답**
4. **차용 검토 보론** — 오너 제공 자료별 판정표
5. **아키텍처** — 계층 설계, 각 결함 → 해소 위치 매핑
6. **배포·채택 전략** — 포지셔닝, 경로, 리스크 대장
7. **로드맵** — 기간·마일스톤
8. **참고문헌** — 축별, 검증된 ID만
9. **부록** — 재현 실험 로그

🚦 **오너 컨펌 게이트**: 발행 후 반드시 승인을 받는다. 이 시점에 나오는 지시(무료화, 범위 축소, 기한 변경)는 이후 전부에 파급되므로 여기서 확정한다.

### 발행 아티팩트 드리프트 방지 ⚠️

발행본은 **그 순간의 스냅샷**이고 소스 파일은 계속 움직인다. 실측 사례: 보고서를 발행한 뒤 아직 끝나지 않은 백그라운드 작업이 소스를 더 고쳐 발행본과 어긋났다.

- **발행 전**: 백그라운드 작업이 전부 끝났는지 확인한다. 진행 중이면 **끝난 뒤에 발행한다.** 발행은 몇 초, 재발행 누락은 몇 시간이다.
- **발행 후 소스를 고쳤으면**: 같은 턴에 재발행한다(같은 파일 경로로 다시 발행하면 같은 URL로 갱신된다). "나중에"는 오지 않는다.
- **발행본이 정본이 아니다**: 오너가 아티팩트를 보고 결정하지만, 결정의 근거는 레포의 ADR이다. 아티팩트에만 있고 레포에 없는 내용은 존재하지 않는 것으로 취급한다.

---

## Phase 3-A — 정체성 확정 (이름 + 어휘) ⚠️ 코드 작성 전에 (모든 티어)

상세 절차: **`references/identity-and-renaming.md`** — 이름을 고르거나 바꿀 때 반드시 읽는다. S는 아래 1·4만 빠르게(가용성 실측은 충돌 확인 수준으로 축약 가능), 어휘 재유도(2·3)는 M/L에서만 의미가 있다.

**개명 비용 곡선** — 이 결정은 미룰수록 기하급수적으로 비싸진다: 코드 0줄이면 **30분**, 문서 완성 후면 반나절, 티켓 절반 구현 후면 며칠, 배포 후면 사실상 불가. **Phase 4 진입 전에 끝낸다.**

1. **가용성은 실측한다** — npm·GitHub HTTP 코드. 200이면 그 패키지의 **실체까지** 본다(죽은 스쿼팅인가, 같은 도메인의 활성 도구인가). 후자면 배제한다.
2. **npm이 비어 있어도** 기존 개발도구와의 이름 충돌을 따로 확인한다.
3. **독립 설계면 어휘를 재유도한다** — 죽은 필드 금지: 모든 필드는 그것을 읽고 행동을 바꾸는 소비자 라우트를 최소 1개 가진다. 라우트 열이 빈 행은 삭제한다.
4. **ADR로 굳힌다** — 채택 이름 + 배제 후보와 이유 + 어휘표. 나중에 또 바꾸게 되면 원본 ADR을 기계 치환하지 말고 수퍼시드 배너만 단다.

---

## Phase 3 — 결정 (ADR) (모든 티어 — S는 최소 1개, M/L은 아래 세트 전부)

컨펌된 도시에를(S는 Phase 0의 답을) **되돌릴 수 없는 결정 문서**로 굳힌다. 템플릿: `references/genesis-templates.md`

최소 ADR 세트 (M/L):
| ADR | 내용 |
|---|---|
| 0001 | **범위·기한** — 기한 압축 시 자른 항목을 표로 명시(→ Backlog) |
| 0002 | 언어·런타임·배포 채널 |
| 0003 | **데이터 진실 저장소(SSOT)** — 무엇이 진실이고 무엇이 파생물인가 |
| 0004~ | 도메인 핵심 결정 (도시에의 결함 대응별로 1개씩) |
| 마지막 | 검증·벤치마크 전략 |

S는 위 항목을 한 ADR 안에 압축한다 — 범위·언어/런타임·핵심 결정을 한 문서에 넣되 `Rejected:` 절은 그대로 지킨다.

모든 ADR에 `Rejected:` 대안과 이유를 넣는다. 이유 없는 기각은 나중에 반드시 재논의된다.

---

## Phase 4 — 레포 창세 (모든 티어)

### 외부 쓰기 승인 gate

먼저 모든 명령을 dry-run 또는 읽기 전용 조회로 고정한다. repo 이름·owner·visibility·
license·default branch·생성할 이슈/마일스톤/보호 규칙을 한 묶음으로 제시한다. 현재
사용자 요청이 그 exact scope를 명시적으로 승인하지 않았다면 승인을 받은 뒤에만
`gh repo create`, push, branch protection, issue/milestone 생성 같은 외부 쓰기를 한다.
실행 후 API로 repo·브랜치·보호 규칙·이슈를 재조회한다. 기존 저장소의 default
branch merge/direct push는 별도 명시 승인 없이는 금지한다.


### 브랜치·보호 모델 (2026-08-08 교정 — 운영 커널과 결박)

`dev`(통합·기본)와 `main`(프로덕션) **둘 다** 보호한다: PR required, approving
review count **0**(단일 오너 데드락 방지 — 승인은 사람이 아니라 evidence 다),
strict required checks(`governance`·`project-ci`·`agent-review`), direct/force
push·delete 금지, admin 포함, ruleset bypass actor 없음, **merge commit only**
(squash/rebase 비활성 — 이전 판의 "필요하면 squash/rebase" 지침은 이 결정으로
supersede). 정확한 보호 설정 값의 정본은 installer 가 생성하는
`governance/external-write-plan.json` 이다.

| 브랜치 | 역할 | kind |
|---|---|---|
| `main` | 프로덕션 | — |
| `dev` | 통합·기본 브랜치 | — |
| `feat/<ID>-slug` `fix/<ID>-slug` | 원자 티켓 1개 | implementation |
| `contract/<ID>-slug` | 계약 변경 (제품 코드 혼합 금지) | contract-change |
| `governance/<ID>-slug` | kernel 변경 (risk=critical) | governance-change |
| `revert/<ID>-slug` | rollback (invalidates 필수) | rollback |
| `release/<semver>` `hotfix/<ID>-slug` | 릴리스·긴급 수정 | — / implementation |

브랜치명이 **티켓 ID**를 요구하므로 티켓 없이는 브랜치를 자를 수 없다
(`governance.py check-pr` 가 naming·linkage 를 기계 검사한다). 모든 티어는 이슈
0개면 FAIL, M/L은 마일스톤 0개도 FAIL이다.

```bash
repo="<owner/name>"
name="${repo##*/}"
gh repo create "$repo" --private --clone --license mit --description "<한 줄>"
cd "$name"
git checkout -b dev main
git push -u origin dev
gh repo edit "$repo" --default-branch dev
# 보호 규칙은 governance/external-write-plan.json 의 값을 그대로 적용한다
# (dev·main 모두, required checks 는 첫 워크플로 실행 후 contexts 로 등록).
mkdir -p docs/adr   # M/L은 추가로: mkdir -p docs/{prd,tickets}
```

`gh repo create`가 MIT LICENSE 초기 커밋과 `main`을 만들고, `dev`는 프로젝트 내용 커밋 전에 그 `main`에서 갈라진다. 보호 API에는 Administration(write) 권한과 비공개 레포 branch protection을 지원하는 요금제가 필요하다.

산출 순서(각각 커밋):
1. **브랜치 설정** (모든 티어) — 원격 `main`·`dev`, 기본 브랜치 `dev`, 둘 다 보호
2. **ADR** (모든 티어) + README 초판 — S는 1개, M/L은 전량
3. **PRD** (모든 티어) — S는 micro-PRD 1개 이상, M/L은 기능(F1~Fn)별 1개. 목표/비목표/사용자 스토리/요구사항/AC
4. **티켓** (모든 티어) — `docs/tickets/<ID>-<name>.md`, 파일당 정확히 하나의 `repo-governance-ticket:v1` metadata(owned/coordinated/oracle 경로 분리, acceptance↔named case, budgets). 템플릿: `references/genesis-templates.md`
   > 실전 교훈: 티켓을 단일 파일에 몰아넣으면 "추적용 메모"가 되고 구현을 시작할 수 없다. 파일별로 **모듈 경로·함수 시그니처·테스트 케이스 목록·기계 판정 AC**를 넣어라.
5. **운영 커널 설치** (모든 티어) — `python3 scripts/install-governance.py --config repo-factory.json --path . --dry-run` 검토 후 실행. policy SSOT·kernel 스크립트·워크플로 6종·adapter 스텁·external-write-plan 이 들어온다. `python3 scripts/governance.py validate --root .` exit 0 확인.
6. **Genesis Bundle 승인** — `python3 scripts/governance.py manifest --root . --output governance/genesis-manifest.json` 의 digest 를 오너가 한 번 승인(불변식 5-③). receipt 는 커밋 금지.
7. **마일스톤** (M/L) — 기한 있는 N개 + `Backlog` 1개. S는 마일스톤 없이 이슈만 만든다.
8. **이슈** (모든 티어) — `scripts/create-issues.py --root . --dry-run` 검토 후 `--confirm-external-write`. 정본은 티켓 파일이고 이슈는 marker 기반 projection 이다(0 create / 1 sync / 2+ fail-closed). S도 이 스크립트를 쓴다 — 수동 `gh issue create` 는 marker 가 없어 동기화가 깨진다.
9. **CONTRIBUTING.md** (M/L) — README가 링크하는 것은 반드시 존재해야 한다
10. **AGENTS.md** (모든 티어) — 소유 경계·테스트 실행법·커밋 관례 + **governance section**(kernel 수정 금지, merge 는 broker 만, 티켓 밖 작업 금지). 프로젝트마다 새로 쓴다.

주의: kit 워크플로는 **생성 레포 자신의 스크립트**(`scripts/governance.py` 등 — Phase 4에 커밋됨)를 부른다. 이 공장(`~/.claude/skills/repo-factory`)에 원격 의존하는 배선은 여전히 금지다.

다음 명령이 모두 0으로 종료되기 전에는 Phase 4가 끝난 것이 아니다. `commitlore init`은 idempotent하므로 이미 설정된 저장소에서도 그대로 재실행한다:

```bash
commitlore init
commitlore doctor
python3 scripts/governance.py validate --root .
python3 scripts/governance.py doctor --root . --online
python3 scripts/phase-gate.py 4 --repo <owner/name> --path . --tier <S|M|L>
```

phase-gate v3 는 마지막 줄에 **assurance level** 을 찍는다 — `LOCAL_VERIFIED` 를 넘는 수준(GITHUB_CANARY / MULTI_REPO_DOGFOOD)은 `governance/evidence/` 의 실제 evidence 없이는 주장되지 않는다.

`--tier`를 안 넘기면 `L`(전체 요구)로 판정한다 — S/M 프로젝트는 반드시 넘겨라. 실전 교훈: 한 프로젝트 실행에서 체크리스트를 완료 요약으로 읽어 CONTRIBUTING.md·마일스톤·이슈를 건너뛰고 F1 티켓과 크리티컬 패스도 없이 다음 단계를 시작했다 — 그래서 이 게이트가 명령이지 체크리스트가 아니다.

의존성 그래프와 **크리티컬 패스**(M/L)를 티켓 인덱스에 그린다 — 어떤 실행 방식을 쓰든 착수 순서를 정하는 입력이 된다.

**커밋 메시지부터 도그푸딩**: 프로젝트가 주장하는 규약을 자기 히스토리에 적용한다.

### CommitLore 기본 적용 — 모든 티어

1. 설치된 실행 파일과 버전을 기록한다: `command -v commitlore && commitlore --version`.
2. 저장소 루트에서 `commitlore init`을 실행한다. 이 명령은 hook 설치, index rebuild, coding-agent hook 설치, `doctor --fix`를 순서대로 수행하며 idempotent하다.
3. `commitlore doctor` exit 0을 직접 확인한다. 일부 단계가 실행되지 않았거나 doctor가 실패하면 Phase 4는 미완료다.
4. 새 저장소의 `AGENTS.md`에 다음 운영 규칙을 넣는다: 편집 전 `commitlore context <path>`; 새 의존성·서비스·접근 제안 전 `commitlore guard --proposal "<proposal>" -- <path>`; 커밋 전 `commitlore validate`; 기록 가치가 없는 단순 커밋에는 trailer를 강제하지 않음.
5. 첫 프로젝트 커밋과 Phase 4 최종 HEAD를 각각 `commitlore validate --commit <sha>`로 검증한다. 얕은 이력이나 누락 입력 때문에 일부 검사가 불가능하면 PASS로 축약하지 말고 `UNCHECKED`/차단으로 보고한다.

#### CommitLore 결함 등록 흐름

CommitLore 사용 중 도구 자체의 오류·오판·침묵 통과·문서/실동작 불일치가 의심되면 다음 순서를 생략하지 않는다.

1. 의심 결과를 프로젝트 성공 증거로 사용하지 않고 `unverified`로 격리한다. 비밀·개인 경로를 제거한 원명령, exit code, stdout/stderr, `commitlore --version`, OS/Node/Git 버전을 보존한다.
2. 임시 최소 Git 저장소 또는 재현 가능한 기존 fixture에서 같은 현상을 다시 실행한다. 재현되지 않으면 CommitLore 결함으로 단정하거나 이슈를 만들지 않고 관찰로 남긴다.
3. `gh issue list --repo MongLong0214/commitlore --state all --search "<핵심 오류 또는 동작>"`로 open/closed 중복을 검색한다. 동일 원인이 있으면 새 이슈 대신 기존 이슈에 연결한다.
4. 중복이 없고 재현됐으면 public-safe body-file 초안까지만 만든다. 본문에는 버전·환경, 최소 재현 명령, 기대/실제 결과, exit code와 축약 로그, 영향·fail-open 여부, 임시 우회책 유무를 넣고 비밀·개인 경로·내부 라우팅 메타데이터를 제거한다.
5. 현재 요청이 upstream 등록까지 명시적으로 승인하지 않았다면 오너 승인을 받는다. 승인 전에는 이슈를 만들지 않는다.
6. 승인 후 `MongLong0214/commitlore`에 이슈를 만들고 `gh issue view <number> --repo MongLong0214/commitlore --json number,title,state,url,body`로 재조회한다. 재조회하지 못하면 `attempted but unverified`다.
7. 안전한 우회가 검증돼 있으면 프로젝트에 제한과 근거를 CommitLore record로 남기고 진행한다. 검증된 우회가 없거나 fail-open이면 해당 커밋/Phase를 차단한다. 프로젝트 고유 통합 결함은 프로젝트 이슈에도 연결하되 원인 이슈를 중복 복제하지 않는다.

---

## Phase 5 — 일상 개발 (이 스킬의 범위 밖 — 생성된 레포의 자율 루프가 돈다)

Phase 4가 끝나면 레포에는 계약(ADR·PRD·티켓)과 **운영 커널**이 갖춰져 있고,
일상 개발은 사람 개입 없이 돈다:

```text
ready 계산 → claim → worker 구현 → 독립 reviewer → 수정 루프 → exact-head CI
→ 등록된 어떤 agent 든 merge intent → Merge Broker 재검증 → 자동 merge
→ post-merge CI → verified → 다음 티켓 자동 시작
```

이 루프의 실행 주체는 repo-factory 가 아니라 **로컬 controller**(운영자 자격증명)로
도는 `scripts/autopilot.py` + `scripts/merge-broker.py` 다 — target repo 의 workflow
는 read-only evidence(governance/ci/agent-review/security-gate/post-merge)만 만든다.
repo-factory 는 커널을 이식하고 검증할 뿐 루프를 돌리지 않는다. worker/reviewer 를
어떤 provider/model 로 채울지는 adapter 설정(`governance/adapters/`)의 몫이며 이
스킬은 강제하지 않는다. 계약 전문: `references/operational-governance.md`

## Phase 6 — 출하 (이 스킬의 범위 밖)

릴리스·공개 전환·배포 연동은 프로젝트 자신의 절차다. `release/<semver>`/
`hotfix/<ID>-slug` 브랜치 규약(§Phase 4)은 준비된 것일 뿐 이 스킬이 실행하지
않는다. release/배포는 policy 상 **critical** 이므로 predelegated profile 이
없는 한 자율 루프도 여기서 멈춘다 — 공개 전환·배포의 오너 게이트(불변식 5)는
그대로다.

---

## 산출물 체크리스트

- [ ] Phase 0 필수 질문 4가지 확정 (기한 · 수익 모델 · 공개 여부 · **오리지널리티**)
- [ ] M/L 도시에 승인 (발행 시점에 백그라운드 작업 0 + 오너 컨펌, 재현 실험 부록 + 검증된 참고문헌)
- [ ] **정체성 ADR** — 채택 이름 + 배제 후보와 이유 (+ M/L: 어휘표, 라우트 열 빈 칸 0)
- [ ] 이름 가용성 확인 (S: 충돌만 빠르게 · M/L: npm/GitHub HTTP 코드 + 선점 패키지의 실체 확인)
- [ ] 레포: ADR (S 1개 / M·L n개) · PRD(S micro 1개 이상 / M·L 기능별 n개) · metadata 있는 원자 티켓 파일 n · README(+번역) · (M/L) CONTRIBUTING
- [ ] **운영 커널**: `install-governance.py` 설치 + `governance.py validate` exit 0 + policy SSOT 1개 + PAT 0 + raw agent merge credential 0
- [ ] **Genesis Bundle 한 번의 승인** — `genesis-manifest.json` digest + external-write plan + 자율권 위임 범위. receipt 는 커밋하지 않음
- [ ] 외부 쓰기 exact scope 승인(Genesis Bundle 이 커버) + 실행 후 API 재조회 evidence
- [ ] (M/L) 마일스톤 (기한 있는 N + Backlog) · 이슈 전량 marker 동기화(`create-issues.py`, rerun duplicate 0)
- [ ] (M/L) 크리티컬 패스 · (모든 티어) 워크플로 6종 (전 action full-SHA pin)
- [ ] AGENTS.md에 CommitLore 규칙 + governance section (kernel 수정 금지 · merge 는 broker 만)
- [ ] `commitlore init` exit 0 + `commitlore doctor` exit 0 + 첫/최종 커밋 `commitlore validate --commit` PASS
- [ ] `python3 scripts/phase-gate.py 4 --repo <owner/name> --path . --tier <S|M|L>` exit 0 + assurance level 이 실제 evidence 와 일치

---

## 이 스킬이 무엇으로 검증됐는가 (2026-07-26)

이 스킬은 **실제로 프로젝트를 두 번 완주하면서** 고쳐졌다. 첫 실행은 이 문서의
실전 교훈 대부분을 냈고, 대부분은 **이 스킬 자신의 지시가 틀렸다는 것이
드러나서** 들어왔다.

두 번째 실행은 Phase 4 체크리스트가 게이트가 아니어서 오퍼레이터가 완료했다고
믿은 채 CONTRIBUTING.md·마일스톤·이슈 세 산출물을 건너뛴 사실을 드러냈다.

**직접 실행으로 확인한 것**
- `scripts/*.py` 실패 경로 각 6종 → raw traceback 0, exit 규약 0/1/2 일관 (8/8 실측)
- `verify-citations.py` 실 API 회귀 → 진짜 DOI 통과 / 날조 DOI·arXiv ID 거부 / **검색어 오매칭 검출**
- 문서 내 셸·노드 스니펫 8종 → macOS bash 3.2 · BSD/bfs에서 실행 확인
- 파일 간 상호 참조 20/20 해석

**실행 중에 발견해 고친 이 문서의 결함**
- §5-D의 생존 판정이 `execution-protocol.md`와 정면 모순 (판정법은 `teammateMode`마다 다르다)
- `find -newermt '-90 seconds'` 가 BSD/bfs에서 파싱 실패 → `-mmin`
- 파일 소유권만 나누면 `npm test`/`tsc`가 전역이라 소유권 규칙이 검증 단계에서 우회됨
- 불변식 5의 오너 게이트 목록이 Phase 0 확장을 반영하지 못함

**하지 못한 것 (정직하게)**
- **독립 적대적 리뷰어의 보고를 받지 못했다.** 두 번 스폰했고 둘 다 2시간 이상 무응답이었다(같은 환경에서 구현 에이전트 15개는 산출물을 정상 전달했다 — `execution-protocol.md` §완료 보고를 기다리지 마라 참조). 위 검증은 전부 **작성자 자신이 수행한 것**이므로, 작성자가 못 보는 종류의 결함은 아직 남아 있을 수 있다.

**2026-07-28 재조정** (오너 결정) — 스킬 범위를 명시적으로 Phase 0~4로 좁혔다
(Phase 5·6은 프로젝트의 몫으로 이관, §Phase 5·6 참조). 특정 외부 프로젝트에
대한 기능적 결합을 전부 제거했다 — `phase-gate.py`는 더 이상 특정 결정 기록
도구의 설치를 요구하지 않고, CI를 이 스킬의 스크립트에 되묶는 배선도 없앴다.
동시에 S/M/L 크기 티어를 도입해 `phase-gate.py 4 --tier`가 프로젝트 규모에 맞는
산출물만 요구하게 했다.

## 2026-08-06 오너 결정 — CommitLore 기본값 복원

2026-07-28의 “특정 도구 결합 제거” 결정 중 **CommitLore를 기본 요구하지 않는 부분만** 이 결정으로 supersede한다. repo-factory는 여전히 Phase 0~4에만 책임지고 프로젝트 CI가 repo-factory 스크립트를 원격 의존하지 않게 유지한다. 다만 공장이 만든 모든 Git 저장소는 Phase 4에서 CommitLore를 로컬 기본 도구로 초기화·검증하고, 사용 중 재현된 CommitLore 제품 결함은 위 결함 등록 흐름으로 upstream 이슈화한다. CommitLore의 모든 기능을 프로젝트 제품 의존성으로 넣거나 모든 커밋에 기록을 강제한다는 뜻은 아니다.

## 2026-08-08 운영 계약 교정

현재 작업의 라우팅 지시를 정적 모델·runtime 전제보다 우선하도록 교정했다. 모든
티어에 최소 PRD·원자 티켓·CI를 요구하고 ADR/PRD/티켓별 exact-content CEO gate를
명시했다. GitHub 외부 쓰기와 CommitLore upstream 이슈 등록은 명시 승인 후 실행하고
사후 API 재조회로 검증한다. 누락됐던 references/scripts는 제안 패키지에 복원한다.

## 2026-08-08 전면 디벨롭 — Autonomous Policy-Delegated Governance 이식 (오너 결정)

`REPO_FACTORY_AUTONOMOUS_GOVERNANCE_9_9_ROLE_AGNOSTIC_MERGE_FINAL.md` 를 반영해
Phase 4가 생성하는 저장소에 운영 커널을 이식하도록 스킬을 재구성했다.

- **supersede**: 불변식 5의 "ADR/PRD/티켓 각각 exact-content CEO PASS" → Genesis
  Contract Bundle **한 번의 승인**으로 통합. Gitflow 절의 `feat-issue-<id>` 네이밍
  · `--no-ff`/squash 지침 → `feat/<ID>-slug` + merge commit only 로 교체.
  `create-issues.py` 의 issues.json 입력 → 티켓 Markdown 정본 marker 동기화로 교체.
- **신규**: `references/operational-governance.md`(운영 계약 정본),
  `templates/governance/`(policy/ticket/merge-intent/agent-adapter/audit-event
  스키마 + policy 템플릿), `templates/kit/`(governance.py · autopilot.py ·
  merge-broker.py + 워크플로 6종 + PR 템플릿), `scripts/install-governance.py`,
  phase-gate v3(운영 커널 검사 + assurance level), `tests/` 96건
  (positive + negative/mutation 부분집합).
- **불변**: Phase 0~4 범위, S/M/L 티어, 증거·반증·정체성·CommitLore 규칙,
  생성 레포 CI가 이 공장에 원격 의존하지 않는 원칙.

### 2026-08-08 GitHub Free 하드닝 (증분)

`REPO_FACTORY_GITHUB_FREE_ENTERPRISE_HARDENING_FINAL_PROMPT.md` 반영. 전면
재작성 없이 GitHub 연동·병합·보안·검증 계층만 증분 수정:
- **신규**: `templates/governance/github-free-capabilities.{v1.json,schema.v1.json}`
  (기능 매트릭스 정본), `templates/kit/scripts/github-profile.py`(profile resolver),
  `templates/kit/.github/workflows/security-gate.yml`,
  `templates/kit/.github/dependabot.yml`, `scripts/run-canary.py --profile`,
  `governance/{github-profile.lock,actions-lock.v1}.json`(installer 생성),
  `tests/test_github_free_profiles.py`·`tests/test_hardening.py`.
- **확장**: policy 에 `security_commands`·`autonomy.auto_revert_out_of_band`;
  governance.py 에 actions-lock/permissions/pull_request_target/profile-lock 검사
  + `security-scan` 명령; merge-broker 에 profile 분기(public native auto-merge /
  private exact-sha + marker)·`GITHUB_TOKEN_MERGE_FORBIDDEN`·`audit`(OOB);
  phase-gate v4 assurance ladder.
- 검증 기준일 2026-08-08, GitHub Free 개인 계정.

### 2026-08-08 프로덕션 전수 리뷰 (적대적 자체 리뷰)

merge-authorization 경로를 적대적으로 재검토해 **테스트·canary 가 모두 초록인데도
프로덕션 `--online`/Actions 경로를 막던 결함 5건**을 발견·수정했다(canary 러너가
facts 를 주입하거나 broker 를 로컬 실행해 실제 경로가 미실행이었다):
1. `evaluate` protection 게이트가 profile 무인지 → private 은 protection 이 원래
   없어 영영 merge 불가 → protection 을 native profile 전용으로, private 은 skip.
2. `queue_available` 를 `allow_auto_merge` 로 유도 → merge queue(org 전용) 오인 →
   queue 경로를 org public 으로 한정.
3. `load_facts_online` 이 classic protection API 사용 → 공장은 ruleset 생성 →
   404 로 public 도 merge 불가 → `/rules/branches` effective view 로 전환.
4. `doctor` 가 classic protection 으로 ruleset-보호 repo 를 오보 → rules
   엔드포인트 + profile-aware(private→NOT_APPLICABLE).
회귀 테스트 추가(프로파일별 protection 게이트, ruleset-only online facts),
두 프로파일 canary 재검증 통과.

### 2026-08-08 GitHub App 완전 제거 (오너 결정)

단일 오너 모델에서 GitHub App 의 실익(운영자 토큰 유출 방어)이 작고 설치 마찰이
커서, **App 관련 코드·분기·워크플로·문서·테스트를 전부 제거**하고 **로컬 controller
(운영자 자격증명, target repo 밖) 단일 모델**로 확정했다.
- 삭제: `autopilot.yml`·`merge-broker.yml` 워크플로(App 토큰 필요), setup 스크립트,
  App manifest, App 설치 문서, `github_token_merge_forbidden`/`REPO_GOVERNANCE_APP_TOKEN`/
  app-token merge-gate 분기, phase-gate 의 `app_bound` 게이트.
- 변경: `security.runtime_identity` → `local_controller`; 자율 루프(reconcile/
  dispatch/merge/rollback)는 로컬 `autopilot.py`+`merge-broker.py` 가 운영자
  자격증명으로 실행; target repo workflow 는 read-only evidence 만.
- 핵심 불변식 불변: agent 는 GitHub write 자격증명 0 → merge-gate 를 만들 수 없다.

- **검증 수준(정직하게)**: 로컬 139/139 + **두 GitHub Free 프로파일 라이브
  canary v2 통과 (리뷰 수정 후 재검증)** (2026-08-08, `scripts/run-canary.py`):
  - **`FREE_PUBLIC_NATIVE_VERIFIED`** — public repo canary(로컬 controller 모델):
    active ruleset(bypass 0)·**merge-gate 없는 merge 를 GitHub 이 "rule violations"로
    거부**·direct/force push 거부·native auto-merge(사람 승인 0)·CodeQL/secret
    scanning/Dependabot·post-merge·revert/invalidation·replay 멱등·OOB audit clean·
    teardown. merge-gate 는 로컬 controller 가 생성(운영자 자격증명) — agent 는 write
    자격증명 0 이라 위조 불가.
  - **`FREE_PRIVATE_COMPENSATING_VERIFIED`** — private repo 18/18: native 부재
    실측 확인(주장 안 함)·로컬 broker exact-sha merge + commit marker·post-merge
    트리거·**OOB direct push 를 audit + post-merge 가 red 로 검출**·rollback/
    invalidation.
  - canary 가 실제 버그 1건을 잡아 고침: shallow checkout(fetch-depth 1)이 OOB
    커밋을 grafted root 로 만들어 오탐 GENESIS → private genesis 를 메시지 기준으로
    바꾸고 post-merge 를 fetch-depth 0 으로 수정, 회귀 테스트 추가.
  - private 은 `COMPENSATING_CONTROLS_ONLY` 로 native enforced 를 주장하지 않으며
    `9_9_CANDIDATE` 를 발급하지 않는다. `MULTI_REPO_DOGFOOD_VERIFIED` 이상은 실사용
    3 repo · 30+ ticket lifecycle 축적 전에는 주장하지 않는다.
  - **위 두 canary 는 App 제거 후 재실행으로 재검증한다(아래 최종 검증 참조).**

## 참고 파일

| 파일 | 언제 읽나 |
|---|---|
| `references/operational-governance.md` | **Phase 4 + 생성 레포의 일상 운영** — 거버넌스 모드, 4 authority, 자격증명 분리, role-agnostic merge, oracle 분리, state machine, assurance level |
| `references/research-protocol.md` | Phase 1(M/L) — 검색 축, 반증 탐색어, 재현 실험, **효용 가설 측정**(§D-2: 대조군 설계, 사후 부분집합 금지, 통계 교차검증) |
| `references/dossier-spec.md` | Phase 2(M/L) — 보고서 섹션별 필수 요소 |
| `references/identity-and-renaming.md` | **Phase 3-A**(모든 티어) — 이름 가용성 실측, 어휘 재유도(M/L), 대규모 용어 교체 실행 절차 |
| `references/dogfooding-loop.md` | **불변식 7**(M/L) — 도그푸딩을 CI의 빌드 게이트로. 범위 유도 · 침묵 금지 · 뮤테이션 증명 |
| `references/genesis-templates.md` | Phase 3~4(모든 티어) — ADR/PRD/티켓(metadata 포함) 템플릿 |
| `references/self-improvement-loop.md` | **불변식 14** — 스킬 자신의 자가개선 루프 + factory defect 승격 규칙 |
| `templates/governance/github-free-capabilities.v1.json` | **불변식 12** — GitHub Free 기능 매트릭스 정본 (public native / private compensating) |
| `scripts/run-canary.py` | live canary v2 — `--profile public|private`, 외부 쓰기 승인 후에만 |
| `templates/governance/*.json` | Phase 4 — policy/ticket/merge-intent/agent-adapter/audit-event 스키마 정본 |
| `scripts/install-governance.py` | Phase 4(모든 티어) — 운영 커널 설치. **`--dry-run` 먼저**, 충돌은 fail-closed |
| `scripts/create-issues.py` | Phase 4(모든 티어) — 티켓 marker 이슈 동기화. **`--dry-run` 먼저** |
| `scripts/verify-citations.py` | Phase 1(M/L) — 인용 API 검증. `CROSSREF_MAILTO` 설정 권장 |
| `tests/` | 이 스킬을 고칠 때 — `python3 -m unittest discover -s tests` 가 회귀 게이트다 |

스크립트 exit code 규약은 동일하다 — **0** 성공 / **1** 검증 실패(고칠 항목 있음) / **2** 사용법·스키마·API 오류(입력이 틀림). 자동화에서 이 셋을 구분해 분기하라. 스키마 위반은 첫 건에서 멈추지 않고 **전부 모아서** 보고한다.
