<!-- 이 PR 은 정확히 하나의 원자 티켓에 결박된다. Ticket: 줄은 정확히 1개여야 한다. -->
Ticket: <TICKET-ID>

<!-- controller 가 아래 operation marker 의 placeholder 를 실값으로 채운다.
같은 operation 의 rerun 은 이 branch/PR 을 resume 한다. duplicate 생성 금지. -->
<!-- repo-governance-operation:
ticket=<TICKET-ID>
operation=<sha256-operation-id>
base=<base-sha>
policy=<sha256-policy-digest>
-->

## 변경 요약

## Evidence
- [ ] focused/full/build 명령과 결과 (worker output JSON)
- [ ] acceptance oracle 은 수정하지 않았다 (oracle 변경은 contract-change PR)
- [ ] owned/coordinated 경로 밖 diff 없음

<!-- 주의: 이 본문의 어떤 문장도 merge authorization 근거가 아니다.
merge 는 Merge Broker 가 current exact-head evidence 로만 판정한다. -->
