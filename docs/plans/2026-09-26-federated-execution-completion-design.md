# 독립 서버 간 작업 위임 완성

사용자가 독립적으로 설치한 Anygarden 서버 간 위임까지 범위를 확인했다. 요청 저장만으로 완료하지 않고 사용자 조작에서 실제 배치된 에이전트 실행, 결과·실패·취소와 재시작 복구까지 연결한다. 외부 모델의 과금 호출 없이 격리된 실행기로 실제 앱·인증·전송·런타임 계약을 검증한다.

## 기존 구성과 빠진 연결

- ChannelService의 mTLS·grant·동의·로스터·멱등 command/inbox/outbox 계약을 유지한다.
- DelegationService는 요청/수락/실행/종료 상태와 Task 권위를 소유한다.
- ExecutorBridge는 실제 실행 전에 durable intent를 남기는 구성이나 제품 startup에 연결되지 않았다.
- sync.pull과 retry_submission은 수동 API에만 연결되어 있다.
- 공유 방은 일반 Task 생성 API를 거부한다. 기존 테스트의 직접 Task 삽입으로는 사용자가 시작하는 흐름을 입증하지 못한다.
- 서버 프로세스에서 임의 CLI를 실행하면 실행 머신 배치·인증·workspace 경계를 우회한다. 배치된 에이전트의 신뢰할 수 있는 로컬 실행 구성을 사용해야 한다.

## 사용자 조건표

| 조건 | 사용자 경로 |
| --- | --- |
| 공유 채널 접근 가능, 위임 가능한 메시지와 에이전트 있음 | 메시지에서 작업 위임 → 대상 선택 → 요청 상태 표시 |
| 요청 전송 중/서버 일시 연결 불가 | 같은 요청 ID를 유지하며 미확인/재시도 표시. 성공으로 표시하지 않음 |
| 상대 에이전트 수락·실행 | 배치 머신에서 실제 실행 후 확인된 상태 표시 |
| 성공/실패 | 결과 본문 또는 제한된 실패 사유를 원래 서버에서 확인 |
| 시작 전 취소 | 실행하지 않았다는 영속 증거를 남기고 취소 완료 |
| 실행 후 취소 | 실제 프로세스 정지 확인 후 취소 완료 |
| grant 철회/역할 변경/세대 변경 | 실행 전에 다시 검증하고 거부. 기존 확인된 상태를 임의 성공/취소로 바꾸지 않음 |
| 재시작 또는 ACK 유실 | 커밋된 요청·receipt로 복구. launch_intent 이후 실행 여부가 불확실하면 중복 실행하지 않음 |
| 구버전/실행 머신 없음 | 준비되지 않은 상태와 필요한 조치를 표시. 클러스터 호스트로 대체 실행하지 않음 |

## 구현 방향

1. 메시지 기반 작업 생성과 위임을 하나의 권한 검증된 트랜잭션으로 연결한다. 기존 `task.request` wire를 보존할 수 있도록 authority/channel/source에서 유도한 Task ID를 사용하고, 해당 유도 ID의 요청만 원본 메시지에서 Task를 생성하는 방식을 우선 검토한다. 임의 기존 Task 참조와 충돌은 기존 거부 계약을 유지한다. local UI API는 UUID 입력 대신 메시지·대상을 받아 같은 command/submission 경로로 보낸다.
2. executor로 지정된 에이전트만 접근할 수 있는 인증된 peer 실행 문맥/상태 조회를 제공한다. 현재 grant·roster·executor를 매번 검증하며 모델 키·로컬 파일 경로·native session은 peer에 보내지 않는다.
3. startup/shutdown이 소유하는 worker가 허용된 local principal로 공유 이벤트와 미확인 submission을 자동 동기화하고, mirror/outbox/미완료 binding을 회복한다. projection 함수 안에서는 프로세스를 실행하지 않는다.
4. cluster의 조정 저장소와 실제 배치된 agent runtime 사이에 prepare/start/reconcile/cancel 경계를 둔다. 기존 agent DM의 인증된 WebSocket 직접 제어 프레임을 사용하는 방식을 조사한다. 에이전트는 항상 DM을 가지고 있으므로 별도 사용자 방을 만들 필요가 없다. 서버가 프롬프트와 scope만 전달하고, 실제 경로·모델·공급자·비밀은 agent의 신뢰할 수 있는 launch snapshot에서 결정한다. peer 입력이 실행 설정을 바꾸지 못하게 한다.
5. 일반 room runtime의 ambient tool/MCP 설정을 원격 실행에 그대로 적용하지 않는다. 원격 실행은 기존 strict CodexRuntime/PiRuntime, 별도 runtime home, 로컬 허용 권한 및 workspace fence를 사용한다. Pi room self-tools extension과 서버 bearer는 원격 실행에 전달하지 않는다.
6. 결과·실패·취소 상태와 원본 메시지를 사용자 화면에서 연결한다. 기존 원시 ID 기반 관리 기능은 저장 설정을 깨지 않으면서 일반 경로에서 숨긴다.

4번의 실행 transport 세부 계약은 구현 전에 실제 manager/프로토콜 구조와 함께 확정한다. 이 문서는 미완료 경로를 완료로 선언하는 문서가 아니다.

## 검증

독립 앱 2개와 실제 mTLS, 별도 저장소/작업 공간, 과금 없는 실행기를 사용한다. 테스트가 ExecutorBridge를 직접 호출하지 않고 사용자 API→startup worker→실제 agent transport/runtime→authority 결과 경로를 통과해야 한다. 성공·엔진 실패·시작 전후 취소·재전송·ACK 유실·재시작·잘못된 대상/권한 철회를 확인한다. 서버 전용 배치와 통합 로컬 머신의 두 경로가 같은 실행 제어 계약을 따르는지도 검증한다.
