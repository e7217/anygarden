# Federation 운영 관찰 3건 정리 (task #59)

실머신 두 노드 E2E(#50, 2026-09-17)에서 기록된 운영 관찰 3건의 설계 근거와
운영 규칙을 확정한다. 모든 항목은 의도된 설계이며, 이 문서는 그 의도를
운영자·개발자가 코드 없이 이해할 수 있게 남긴 것이 목적이다.

## 1. 인증서 node_id vs 라이프사이클 id 관계

### 규칙

피어 인증서의 **node_id는 노드의 영구 identity**이고, 런타임에 생성되는
모든 라이프사이클 id(agent_id, execution_id, 프로세스 pid 등)는 **일회성
세션 자원**이다. 두 종류의 id는 서로 파생되거나 대체될 수 없다.

### 근거(코드)

- 인증서 발급: `federation/certificates.py` `create_credentials()` —
  node_id(UUID)를 CN과 SAN URI(`urn:anygarden:node:<uuid>`)에 기록.
  node_id가 UUID로 검증되므로 임의 문자열로 위조 불가.
- 인증서 해석: `inspect_certificate()`가 SAN URI에서 node_id를 복원하고
  SHA-256 핑거프린트를 함께 반환. 피어 pin은 이 핑거프린트 기준.
- PeerService 구성: `app.py` `_compose_federation_services()` — 노드의
  node_id는 **오직** `inspect_certificate()` 결과에서 나온다. 기동 시
  인증서를 읽지 못하면 fail-closed(라우트 마운트 유지 + 503, 부분
  자격증명은 경고 후 비활성). 즉 node_id는 파일이 아니라 인증서의 속성.
- 위임 실행 측: `federation/executor.py`의 `InvocationScope` —
  `execution_node_id`, `agent_id`, `execution_id`는 로컬 executor가
  실행마다 생성하는 값. 원격 수신 노드는 이 값을 신뢰하지 않고
  `invocation.scope.execution_node_id != self.node_id`를 검증한 뒤,
  sender의 **인증서 node_id**(mTLS 핸드셰이크에서 확정)로 grant를 조회한다.

### 운영 규칙

1. 인증서 재발급 = 새 node_id = 새 노드. 기존 grant/consent/invitations는
   이전 node_id에 묶이므로 재초대가 필요하다. 러너·머신 교체는 node_id에
   영향을 주지 않는다(인증서 파일만 이동·유지).
2. `agent_id`/`execution_id`는 노드 간 전송 시 `Principal`/scope로만
   전달되며 권한 판정의 키가 아니다. 권한 키는 항상
   `(sender node_id, authority_node_id, channel_id)` + grant_epoch.
3. 로그 상에서 node_id는 "누구"이고, execution/agent id는 "이 노드에서
   무엇이"에 해당한다. 장애 추적 시 mTLS 피어 식별은 인증서 로그로,
   실행 추적은 executor 로그로 분리해 읽는다.

### 회귀

- 기존: `test_federation_trust.py`가 SAN URI 파싱·핑거프린트·핀 검증을
  이미 수행. 추가 회귀 불요.

## 2. local_room 유니크 → 언바인드 불가 정책

### 규칙

로컬 Room은 **최대 하나의 공유 채널 스트림**에만 바인딩된다.
`ChannelStream.local_room_id`(`shared_channels/models.py`)는
`ForeignKey("rooms.id", ondelete="RESTRICT")` + `unique=True`:

- **unique**: 하나의 로컬 Room이 두 권위 노드(또는 두 채널)의 스트림을
  동시에 미러링하는 것을 DB 수준에서 차단. Room의 메시지 순서·seq가
  단일 권위 노드 기준으로만 성립하기 때문.
- **RESTRICT**: 스트림 행이 존재하는 동안 로컬 Room 행 삭제를 차단.
  "채널 공유 해제(unbind)"는 지원하지 않는다 — 공유는 초대·승인으로
  시작되고 철회는 권위 노드의 grant 철회로만 일어난다. 로컬 노드가
  Room을 스트림에서 떼어내면 미러 seq와 실제 Room 상태가 갈라져
  무결성이 깨진다.

### 운영 규칙

1. 공유를 끝내려면 Room을 삭제하는 것이 아니라 **권위 노드가 grant를
   철회**한다. 철회 후 원격 요청은 SCOPE/GRANT_DENIED로 거절되고,
   미러 Room 데이터는 로컬 기록으로 남는다.
2. Room이 이미 공유 중인데 다른 채널을 공유하려면: 먼저 기존 공유를
   권위 노드가 철회하고 스트림을 정리한 뒤에만 새 바인딩이 가능하다
   (운영 절차이며, unique 제약이 실수를 DB에서 차단).
3. 이 제약으로 인해 "같은 Room, 여러 권위 노드" 구성은 의도적으로
   불가능하다. 채널을 분리해 운영한다.

### 회귀(본 PR 추가)

- 동일 `local_room_id`로 둘째 스트림 생성 시 IntegrityError를 확인하는
  회귀 1건 추가 — 정책이 애플리케이션 실수가 아니라 DB 계약임을 고정.

## 3. grant 채널별 스코프·epoch

### 규칙

신뢰의 단위는 노드 전체가 아니라 **(peer_node_id, authority_node_id,
channel_id) 트리플렛**이다. `PeerGrant`(`federation/models.py`)의
복합 PK가 이를 반영한다.

- **채널별 스코프**: `capabilities`(허용 action 목록), `actors`
  (허용 principal 목록), `role`(participant/observer — observer는
  `channel.read`만 허용)은 채널 단위로만 부여된다. 노드 A가 채널 X의
  grant를 받았다고 채널 Y 접근이 가능하지 않다.
- **epoch**: `PeerGrant.epoch`는 grant의 세대 번호다. 철회 후 재승인
  시 새 epoch가 발급되고, 오래된 epoch의 요청·replay는 `_current_grant`
  에서 거절된다. 이것이 "철회가 재전송(replay)을 이긴다"를 보장한다.
- **policy_epoch 분리**: `PeerConsent.policy_epoch`는 로컬 정책 세대로
  authority의 `grant_epoch`과 **별개**이며 직렬화되지 않는다
  (models.py 주석). 권위 노드 정책과 로컬 동의 정책이 독립적으로
  진화한다.
- **만료**: `expires_at`으로 시간 상한도 이중화된다.

### 운영 규칙

1. 권한 변경(actors·capabilities)은 같은 채널의 grant를 갱신하되
   **epoch를 올린다**. 클라이언트는 응답의 grant_epoch를 따라간다.
2. 채널 추가 = 새 트리플렛 grant. 채널 철회 = 해당 트리플렛만 revoke.
   다른 채널의 접속은 영향받지 않는다.
3. observer grant는 `channel.read`만 통과한다 — role 상향 없이
   task.execute를 시도하면 SCOPE_DENIED.
4. 재초대(re-invite)는 철회된 트리플렛에 새 grant를 발급하는 절차이며,
   이전 epoch의 자격증명·메시지는 모두 무효다.

### 회귀

- 기존: 채널별 스코프·observer 제한·epoch replay 거절은
  `test_federation_trust.py`/`test_shared_channels.py`로 검증됨.
  실머신 E2E(#50/#54)에서 "권한 철회 후 원격 요청 거절·재초대 회복"도
  실증. 추가 회귀 불요.

## 검증

- 본 PR 회귀: local_room unique 제약 1건 추가(§2), 전체 cluster 회귀
  통과 확인 후 draft PR로 제출.
- 문서만의 변경이며 런타임 동작 변경은 없다(회귀 1건은 기존 계약 고정).
