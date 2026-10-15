# docs(federation): document first-run credentials, cards and TCP passthrough (#839)

- Commit: `df6a029` (df6a029d18cb6ecd9691f327f09e7e0578fd35e8)
- Author: Changyong Um
- Date: 2026-10-15T11:17:45+09:00
- PR: #839

## Situation

이전 운영 문서(`anygarden-node@.service`, `local-node.md`, `federation-two-node.md`)는 peer 자격증명을 사전에 `federation init-credentials`로 직접 생성해야 한다고 기술하고 있었다(PR609 fail-closed). #839에서 `anygarden start --peer-port` 첫 기동 시 기본 경로에 자격증명이 없으면 자동으로 생성하는 동작이 구현되었으나, 문서에는 아직 반영되지 않았다. 이에 따라 서버 운영자는 실제 동작과 다른 설치 절차를 따르고 있었다.

## Task

- `deploy/systemd/anygarden-node@.service`의 Prerequisites 주석을 신규 동작(첫 기동 시 자동 생성, 기존 쌍 보존, fail-closed 조건)에 맞게 수정한다.
- `docs/runbook/local-node.md`의 `--peer-port` 동작 설명을 최신화한다.
- `docs/runbook/federation-two-node.md`의 설치 절차를 첫 기동으로 자격증명이 생성되는 방식으로 재작성하고, TCP passthrough 프록시 섹션 및 실패 플레이북 항목을 추가한다.
- `docs/federated-delegation.md`의 연결 흐름 요약을 연결 카드·초대 코드 UI 흐름으로 갱신한다.
- `docs/requirements.md`의 FED-06 수용 기준을 확장하고 FED-10~FED-12 신규 요구사항을 추가한다.
- `packages/cluster/CHANGELOG.md`에 Fixed 및 Added 항목을 기재한다.

## Action

- `deploy/systemd/anygarden-node@.service` (line 6–16): Prerequisites 주석 2행을 5행으로 교체. "명시적으로 생성해야 함(PR609 fail-closed)" → "첫 기동 시 생성, 기존 쌍 보존, 부분/불가 쌍은 거부; `ANYGARDEN_PEER_PUBLIC_URL` 로 peer가 다이얼할 주소를 설정".
- `docs/runbook/local-node.md` (line 141–160): `--peer-port` 동작 불릿 목록 재작성. 자동 생성 트리거 조건, 기존 쌍 보존 정책, fail-closed 조건(partial, unusable, `ANYGARDEN_PEER_CREDENTIALS_DIR` 설정 시 쌍 없음), `--peer-public-url` 설명 추가.
- `docs/runbook/federation-two-node.md` (line 54–120): Step 2를 "명시적 init-credentials" → "첫 기동으로 자격증명 생성"으로 교체; "Invite → shared channel" 섹션을 연결 카드·초대 코드 UI 절차(3단계)로 재작성; TCP passthrough 섹션(nginx stream, HAProxy) 신규 추가; 실패 플레이북에 `ENDPOINT_DENIED`, `CODE_INVALID`/`IDENTITY_MISMATCH` 항목 추가.
- `docs/federated-delegation.md` (line 7): 연결 흐름 1번 항목을 카드 붙여넣기·초대 코드·지문 대조 방식으로 업데이트.
- `docs/requirements.md`: FED-06 기준 확장(partial 쌍, 명시적 디렉터리에 쌍 없는 경우 포함); FED-10~FED-12 신규 행 추가.
- `packages/cluster/CHANGELOG.md`: Fixed에 `INVALID_SCHEMA`/`ENDPOINT_DENIED` 버그픽스 2건; Added에 자격증명 자동 생성, 연결 카드·초대 코드, TCP passthrough 문서화 항목 기재.

## Decisions

- **수동 생성 단계를 제거하고 첫 기동으로 통합**: 설치자가 `federation init-credentials`를 별도 호출해야 하는 절차는 실수하기 쉬웠다. 첫 기동 시 자동 생성으로 단순화하되, 기존 쌍은 절대 덮어쓰지 않아 회전 제어권을 운영자에게 유지한다.
- **`ANYGARDEN_PEER_CREDENTIALS_DIR` 에 쌍 없을 때 fail-closed 유지**: 볼륨 미마운트 시 새 자격증명이 생성되면 peer가 알지 못하는 새 노드 ID로 동작해 조용히 연결이 깨진다. 디렉터리가 명시적으로 설정되어 있는데 쌍이 없으면 기동을 거부한다.
- **TCP passthrough 문서화**: peer 리스너는 클라이언트 인증서를 TLS 내부에서 검증(mTLS)하므로 일반 HTTPS 역방향 프록시(nginx http/ssl, HTTPS LB)는 클라이언트 인증서를 소실시킨다. nginx `stream` 모듈과 HAProxy `mode tcp` 예시를 명시하여 운영자가 잘못된 프록시를 설정하지 않도록 한다.
- **연결 카드·초대 코드 흐름**: 이전 API 직접 호출 방식(`POST /api/v1/node/invites`)을 UI 흐름(카드 복사 → 붙여넣기 → 검토 → 코드 수락)으로 교체. API 경로는 참고용으로만 병기.

## Result

- 설치 가이드가 실제 구현 동작과 일치한다.
- 운영자는 `--peer-port` 첫 기동만으로 자격증명을 얻고 노드 ID·지문을 즉시 확인할 수 있다.
- TCP 역방향 프록시 설정 시 nginx stream / HAProxy tcp 예시를 따라 올바른 passthrough를 구성할 수 있다.
- FED-06 수용 기준이 구현 동작(자동 생성 + fail-closed 예외 조건)을 정확히 반영하고, FED-10~FED-12가 신규 동작을 커버한다.
- CHANGELOG에 Fixed 2건(UI 버그) + Added 3건(자격증명 자동 생성, 연결 카드·초대 코드, TCP passthrough 문서) 기재 완료.
