# Phase 3-A 상세 — 정체성 확정과 대규모 용어 교체

SKILL.md §Phase 3-A에서 참조. **이름·어휘를 굳힐 때**와 **이미 굳힌 것을 바꿔야 할 때** 둘 다 여기를 본다.

---

## 1. 개명 비용 곡선 — 왜 지금인가

| 시점 | 개명 비용 |
|---|---|
| 코드 0줄, 문서 0개 | **30분** |
| 문서 완성, 코드 0줄 | 반나절 (문서 전량 + 이슈 본문 + 저장소명 + 패키지 메타) |
| 티켓 절반 구현 | 며칠 (스펙·픽스처·인덱스·훅·CI·릴리스 아티팩트까지) |
| 배포 후 | 사실상 불가 (npm 이름은 회수 불가, 사용자가 이미 참조한다) |

Phase 4(레포 창세)에 진입하기 전에 끝낸다. 이름이 미정인 채로 쓴 문서는 전부 재작업 대상이다.

---

## 2. 이름 후보 → 가용성 실측 (추측 금지)

```bash
OWNER=<github-owner>
for p in cand1 cand2 cand3; do
  echo "npm/$p    -> $(curl -s -o /dev/null -w '%{http_code}' https://registry.npmjs.org/$p)"
  echo "gh/$OWNER/$p -> $(curl -s -o /dev/null -w '%{http_code}' https://github.com/$OWNER/$p)"
done   # 404 = 비어 있음, 200 = 선점
```

### 200이면 그게 무엇인지까지 봐라

404/200만으로는 판정할 수 없다. 선점의 **성격**이 결정을 바꾼다.

```bash
curl -s https://registry.npmjs.org/<name> | node -e "
let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const j=JSON.parse(s);
  console.log('latest  :', j['dist-tags'] && j['dist-tags'].latest);
  console.log('desc    :', j.description);
  console.log('created :', j.time && j.time.created);
  console.log('modified:', j.time && j.time.modified);
  console.log('repo    :', JSON.stringify(j.repository));})"
```

| 선점의 성격 | 판정 |
|---|---|
| 죽은 스쿼팅 — `0.0.1`, 설명 없음, 수년째 정지 | 이름 변형(하이픈·스코프)으로 우회 가능 |
| 활성 패키지, **다른** 도메인 | 우회 가능하나 검색 혼선은 감수 |
| 활성 패키지, **같은** 도메인 | **배제한다** |

> 실측 사례: 오너가 지정한 이름이 npm에서 활성(`1.5.0`, 4개월 내 갱신)이었고 설명이 "Git archaeology CLI — churn, bus factor, hotspots from your repo's git history"로 **우리와 같은 도메인**이었다. 패키지명을 하이픈으로 비껴가도 CLI 바이너리가 PATH에서 충돌하고 검색 결과에서 계속 섞인다. 배제하고 대안을 실측해 제시했다.

### npm이 비어 있어도 배제해야 하는 경우

npm 레지스트리는 생태계의 일부일 뿐이다. **기존 개발도구와의 충돌**을 따로 확인한다.
- 확인: `"<name>" cli` / `"<name>" tool` / `"<name>" language` 웹 검색. 1페이지에 다른 개발도구가 나오면 배제
- 실측 사례: 한 후보는 npm이 완전히 비어 있었지만 **OCaml 파서 생성기**와 이름이 같아 배제했다

### 오너에게 가져갈 형식

후보를 그냥 나열하지 마라. 각 후보에 대해 **npm/GitHub/PATH 3축의 실측 결과와 남는 충돌**을 붙여서 선택지로 만든다. 오너가 원한 이름이 막혔다면, 막혔다는 사실과 근거를 먼저 말하고 대안을 낸다 — 조용히 다른 이름으로 바꾸지 않는다.

---

## 3. 어휘 재유도 (독립 설계인 경우)

계승 어휘를 그대로 두면 이름만 바꾼 파생물이다. **어휘가 프로토콜의 본체**인 프로젝트라면 특히 그렇다.

재유도 규칙 — **죽은 필드 금지**: 모든 필드는 *그것을 읽고 행동을 바꾸는 소비자 라우트*를 최소 1개 가져야 한다. 라우트를 설계할 수 없으면 어휘에서 뺀다. "유용해 보여서" 남기는 것만 금지된다.

산출: 어휘표 4열 `{필드, 의미, 값 문법, 소비자 라우트}`. **라우트 열에 빈 칸이 있으면 그 행은 삭제 대상이다.**

enum 값은 **행동을 지시하는 단어**로 고른다. `permanent`는 승인 게이트가 즉시 이해하지만 `level-3`은 매번 해석 테이블이 필요하다.

---

## 4. ADR로 굳힌다

정체성 ADR 필수 항목:
- 채택 이름과 그 이유
- **배제한 후보 전부 + 배제 이유** (이유 없는 배제는 6개월 뒤 똑같이 재논의된다)
- 어휘표 (라우트 열 포함)
- "언제 교체가 싼가"의 근거 — 다음 사람이 이 결정을 언제 뒤집어도 되는지 알 수 있게

> 이름을 나중에 **또** 바꾸게 되면 원래 ADR을 기계 치환하지 마라. 새 ADR을 쓰고 원본 상단에 수퍼시드 배너만 단다. 어느 절이 대체됐고 어느 절이 유효한지 명시한다. 결정 이력을 치환하면 무엇이 왜 바뀌었는지가 사라진다.

---

## 5. 대규모 용어 교체 실행 절차 ⚠️

개명·어휘 교체를 **실제로 수행할 때**의 절차. 이 함정으로 한 번의 개명이 세 커밋에 걸쳐 끝난 사례가 있다.

### 5.1 단어 경계 정규식을 쓰지 마라

- macOS `sed`는 `\b`를 **지원하지 않는다**(GNU sed와 다르다). 조용히 아무것도 안 바꾸거나 리터럴 `b`로 해석한다.
- `\bLore\b` 같은 경계 정규식은 `Lore_v2`, `LoreBench`, 인라인 코드 안의 토큰을 **놓친다**.
- **리터럴 치환만 쓴다.**

```bash
# 치환값을 셸이 perl 소스로 보간하면 $·@·/ 가 코드로 해석된다. 환경변수로 넘겨 차단한다.
FROM="OldName" TO="NewName" perl -0777 -pi -e 's/\Q$ENV{FROM}\E/$ENV{TO}/g' "$f"
```

### 5.2 치환 순서 = 긴 것/구체적인 것 먼저

`Atom[]` → `Trailer[]` 를 `atom` → `record` 보다 **먼저** 돌린다. 반대로 하면 `Atom[]`이 `Record[]`가 되어 의도한 타입명을 잃는다. 치환쌍 배열의 순서가 곧 우선순위다.

### 5.3 잔존 검사는 리터럴 grep 전수로

```bash
grep -rF --exclude-dir=.git --exclude-dir=node_modules -c "OldName" .   # 0이어야 한다
grep -ri --exclude-dir=.git --exclude-dir=node_modules "oldname" .      # 대소문자 변형까지
```

⚠️ **부분문자열 거짓 양성에 속지 마라.** `db_query` → `authdb_query`로 바꾼 뒤 `db_query`를 grep하면 **새 문자열 안에서** 1건이 잡힌다. 접두를 배제해서 확인한다:
```bash
grep -rnE "(^|[^a-z])db_query" .   # 진짜 잔존만
```

### 5.4 기계 치환에서 제외할 것 — 반드시 명시적으로

| 제외 대상 | 이유 / 처리 |
|---|---|
| **결정 이력 문서** (개명 ADR + 그것이 대체하는 원본 ADR) | 치환하면 무엇이 왜 바뀌었는지가 사라진다. 원본에는 수퍼시드 배너만 단다 |
| 인수인계 문서 | 곧 삭제 대상. 치환 대상에 넣으면 잔존 카운트가 오염된다 |
| `package-lock.json` | JSON 구조를 코드로 갱신한다 — `name`, `packages[""].name`, `packages[""].bin` **3곳** |
| 바이너리 파일 | `grep -Iq . "$f"` 로 텍스트만 걸러라 |

### 5.5 파일명·경로도 대상이다

본문 치환은 파일 **내용**만 바꾼다. 파일명에 옛 이름이 있으면 본문의 상호 참조가 깨진다.

```bash
find . -not -path './.git/*' -not -path './node_modules/*' -iname '*oldname*'
git mv <old> <new>
```

직후 **상대 링크 전수 검사**. 저장소 루트 기준으로 검사하면 전부 거짓 양성이 나므로, **포함 파일 기준**으로 경로를 해석해야 한다:
```bash
node -e "
const fs=require('fs'),path=require('path');
const walk=d=>fs.readdirSync(d,{withFileTypes:true}).flatMap(e=>{
  if(e.name==='.git'||e.name==='node_modules')return[];
  const p=path.join(d,e.name); return e.isDirectory()?walk(p):(p.endsWith('.md')?[p]:[]);});
let bad=0,total=0;
for(const f of walk('.')) for(const m of fs.readFileSync(f,'utf8').matchAll(/\]\(([^)\s]+)\)/g)){
  let t=m[1]; if(/^(https?:|mailto:|#)/.test(t))continue; t=t.split('#')[0]; if(!t)continue;
  total++; if(!fs.existsSync(path.resolve(path.dirname(f),t))){bad++;console.log('BROKEN',f,'->',m[1]);}}
console.log(total+'건 중 깨짐 '+bad+'건');"
```

### 5.6 셸 스크립트로 짤 때의 함정

- `set -euo pipefail` 하에서 `grep`은 **매치 0건일 때 exit 1**을 낸다. 카운트 용도라면 `{ grep ... || true; }`로 감싸라. 안 그러면 "0건"인 순간 스크립트가 죽는다.
- `[ "$n" != "0" ] && fail=1` 도 같은 함정이다. 조건이 거짓이면 전체가 exit 1이 된다. `if ... then ... fi`로 써라.
- `mapfile`은 bash 4+ 전용이다. macOS 기본 bash는 3.2다. `while IFS= read -r l; do arr+=("$l"); done < <(...)`를 써라.

### 5.7 마감 증거

치환 후 다음을 **실제로 실행한 출력**으로 남긴다:
- [ ] 잔존 리터럴 grep 0 (거짓 양성 배제 확인 포함)
- [ ] 타입체크·빌드 통과
- [ ] 테스트 전수 통과
- [ ] 상대 링크 0 깨짐
- [ ] 파일명 잔존 0

그리고 **외부 표면**까지 갱신했는지 별도로 확인한다 — 레포 안만 깨끗한 개명은 절반이다:
- [ ] GitHub 저장소명 (`gh repo rename`) + 로컬 remote URL
- [ ] 저장소 description·토픽
- [ ] **이슈·PR 본문 전량** (`gh issue list --json number,title,body` → 치환 → `gh issue edit`)
- [ ] 패키지 메타(`name`, `bin`, `repository.url`) + lock 파일
- [ ] 발행된 아티팩트·외부 문서
