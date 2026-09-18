# R1 · Paperclip 소스 역분석 보고 (task #70, 2026-09-18)

대상: github.com/paperclipai/paperclip (오픈소스, TypeScript, main 브랜치 클론 실측).
방법: GitHub 공개 저장소 클론 후 소스·문서 직접 분석. 웹 브라우징 없음, 외부 쓰기 없음.
초점: ①작업 위임 ②에이전트 조직 ③자동 배정(무멘션) ④재시도/회복 — AnyGarden 대비.

## 1. 아키텍처 요약

- **컨트롤 플레인 단일 코어**: Node.js 단일 프로세스(API 서버 :3100) + 임베디드 PostgreSQL.
  `server/src/services/`에 494개 서비스 파일. 에이전트는 외부에서 실행되며 콜백으로 보고("control plane, not execution plane").
- **조직 = 회사(Company)**: 회사가 일급 객체. CEO→CTO→엔지니어 조직 트리(`reportsTo`), 예산, 승인 게이트 포함.
- **작업 단위 = Issue**: 안정적 식별자, parent/sub-issue, blocker 관계, 단일 assignee, 댓글, 산출물(work products), 리뷰/승인 핸드오프.
- **에이전트 실행 = 어댑터**: Claude Code/Codex/Gemini/OpenCode/Pi/Cursor 로컬 세션, 셸 명령, 웹훅(fire-and-forget), 외부 플러그인 어댑터. "하트비트를 받을 수 있으면 고용 가능."

## 2. 작업 위임 흐름

1. **할당**: Issue 생성/변경 시 `assigneeAgentId` 지정 → `issue-assignment-wakeup.ts`의
   `queueIssueAssignmentWakeup()`이 담당 에이전트에게 `heartbeat.wakeup(agentId, {source:"assignment"})` 발화.
   backlog 상태면 발화 안 함. 멱등키(idempotencyKey)·allowRunCoalescing 옵션으로 중복 웨이크 병합.
2. **실행**: heartbeat 러너가 어댑터로 에이전트 프로세스 기동. `start_new_session` 프로세스 트리+
   감시, bounded transient retry(30초×2), workspace busy retry(60초+지터), max-turns continuation 등
   **사유별 차등 재시도**(`scheduledRetryReason`: workspace_busy / ai_connection_busy / max_turns_continuation /
   transient_failure / execution_review_participant_recovery).
3. **결과**: issue 댓글+work products로 보고. review/approval 핸드오프가 일급 단계.
4. **위임(계층)**: 에이전트가 sub-issue를 만들어 다른 에이전트에 할당 = 위임. parent-child 체인으로
   회사 목표까지 역추적("why am I doing this?").

### 위임 안전장치 (특징적)

- **위임 사이클 금지**(`assertNoAgentDelegationCycle`, routes/issues.ts:7248): 에이전트가 자기가 만든
  아직 열린 ancestor 이슈를 자기에게 되돌아 오도록 위임하면 409 `delegation_cycle`.
  "A가 B에게 위임, B가 같은 일을 A에게 되위임" 교착 차단. 닫힌 ancestor은 예외(완료된 작업 재협업은 정상).
- **조직 체인 건강 검사**(`agent-eligibility.ts`): reportsTo 체인 순회 — terminated ancestor,
  missing manager, cycle, paused-ancestor escalation 경고. assignable/invokable 상태 집합 분리
  (paused는 invokable 불가지만 assignable 가능 등 미묘한 구분).
- **cross-company 차단**: 다른 회사 이슈/에이전트 간 위임 불가.

## 3. 무멘션(자동 배정) 대응 — 사실 "무멘션 자동 배정"은 없음

- **멘션은 명시적 링크 구조**: `extractAgentMentionIds()`(shared/project-mentions.ts)가 마크다운의
  agent mention 링크(`paperclip://agent/<id>` 형태 링크)를 파싱. 채널 댓글의 `@name`은 UI가 링크로
  변환한 것만 인식 — **LLM 추론으로 수신자를 고르지 않는다**.
- **무멘션 메시지**: 채팅 어댑터 문서 명시 — "A fresh unmentioned root message is ignored"(멘션 없는
  새 루프 메시지는 무시). 단, **기존 바인딩된 스레드/DM의 후속 메시지는 멘션 없이도 같은 이슈로
  라우팅**(`hasMeaningfulSlackMentionRequest` 등은 멘션 제거 후 실내용 존재 검사).
  즉 Paperclip의 무멘션 대응 = "컨텍스트 바인딩 지속"이지 "수신자 추론"이 아니다.
- **자동 배정 대체물**: ①CEO/매니저 하트비트가 우선순위 판단해 할당(조직 트리+목표 기반) ②
  `capabilities description`(단락)으로 에이전트 상호 발견 지원 ③watchdog이 방치 이슈를 감지해
  `blocked_by_unassigned_issue` 등으로 분류하고 "Assign blocker" 액션 제안(사람이/매니저가 배정).
  → **규칙·루프 기반이며, 의미적 매칭(LLM이 담당자 고르기)은 코어에 없음.**

## 4. 무응답·장애 자가 회복

- **Task watchdog**(`task-watchdogs.ts`): issue별 watchdog가 subtree를 주기 재평가. live run 상태
  (queued/running/scheduled_retry)과 대조해 "멈춘 하위트리"를 탐지 → 복구 이슈(recovery issue) 생성,
  escalated 상태로 리더에게 보고. 첫 run 전 15초 유예로 false positive 방지.
- **Recovery actions**(`issue-recovery-actions.ts`): 복구 액션 객체(kind/owner/fingerprint/evidence/
  budget)가 일급. recovery budget 소진 시 escalated. 원 담당자 복귀(returnOwnerAgentId) 지원.
- **bounded retry 스케줄**: transient failure 30초×2, workspace busy 60초+지터, max_turns 연속
  continuation 등 실패 사유별 정책. non-retryable preflight 코드 별도 집합(설정 오류는 재시도 없음).
- **run reconciliation**: 서버 재시작 후 고아 run 조사(reap orphaned runs), legacy 실행 정산
  (`legacy-execution-recovery.ts`), remote execution 종료 수신 확인.

## 5. AnyGarden 대비표

| 축 | Paperclip | AnyGarden 현황 | 평가 |
|---|---|---|---|
| 작업 위임 단위 | Issue 트리(단일 assignee, blocker 그래프) | Task/SharedMessage 기반 위임(담당 노드 1개) | **부분** — blocker 그래프·계층 부모 추적 없음 |
| 위임 안전장치 | delegation cycle 409, 조직 체인 건강, cross-company 차단 | delegation cycle 개념 없음(단일 담당 노드라 사이클 구조 자체가 희소) | **미구현**(필요성 낮음, 다중 담당 노드 확장 시 도입) |
| 자동 배정(무멘션) | 없음 — 하트비트+룰 기반, watchdog 제안만 | 없음(멘션 필수 아님: 채널 담당 노드가 채널 메시지 전반 처리) | **상이** — AnyGarden은 채널 단위 담당 노드 모델이라 수신자 추론 문제가 구조적으로 작음 |
| 무멘션 대응 | 바인딩된 스레드 후속은 멘션 불필요, 새 루트는 무시 | 공유 채널 자체가 담당 노드 바인딩 → 채널 내 메시지는 모두 처리 대상 | **우위/동등** — 채널 바인딩 모델이 더 단순·명시적 |
| 재시도/회복 | 사유별 차등 재시도(5+종), watchdog subtree 재평가, recovery issue 일급, reconciliation | 실행 lease·in-flight recovery, 위임 pickup timeout sweeper(PR618/620), 단절 복구 실머신 검증 | **동등~우위** — recovery issue 개념(사람 인지용)은 AnyGarden에 없음 |
| 실행 어댑터 | 4종 어댑터 체계+플러그인 | Runtime 프로토콜(codex-cli+pi-cli 방금 추가) | **동등** — 패턴 동일(우리 PR621이 동일 구조) |
| 조직 모델 | 회사·조직 트리·예산·승인 게이트 | 노드·채널·grant 스코프(머신 협업 초점) | **상이** — 목표가 다름(회사 운영 vs 머신 간 협업 인프라) |

## 6. 기획 시사점 (Track D 인풋)

1. **무멨션 수신자 추론을 굳이 만들지 않아도 되는 이유**: Paperclip조차 LLM 수신자 추론을 안 하고
   바인딩+루프로 풀었다. AnyGarden의 채널 담당 노드 모델은 이보다 더 강한 바인딩. 다만
   **"채널에 담당 노드 없음" 상태의 폴백**(마지막 활동 노드·명시적 재지정·거부 사유 응답)은 고려 가치.
2. **recovery issue/액션 객체**: 단절·픽업 타임아웃을 감지만 하지 말고 "복구 액션"으로 승격해
   이력·예산(budget)·에스컬레이션을 남기는 패턴 — PR618/620 스위퍼 위에 얹을 수 있음.
3. **사유별 차등 재시도**: 우리 in-flight recovery에 재시도 사유 분류(workspace_busy 스타일)를
   명시화하면 무의미한 전체 재시도를 줄일 수 있음.
4. **위임 사이클 방어**: 다중 담당 노드/채널 확장 시 409 delegation_cycle 개념 선제 도입 검토.

## 방법론 한계

- 클론 시점(main, 2026-09-18) 기준 단일 스냅샷. heartbeat.ts만 29,485행으로 전수 분석 불가 —
  위임·회복·무멘션 축의 대표 경로만 코드 추적.
- "2026 최신 기법"은 학습 컷오프 한계로 본 저장소 최신 코드(가장 신선한 1차 소스)로 대체.
- 라이선스: 저장소 LICENSE 확인 결과 분석·참조 자유(오픈소스). 코드 복사가 아니라 메커니즘 참조만.
