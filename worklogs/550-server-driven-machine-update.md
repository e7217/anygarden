# feat: 서버 주도 machine 자동 업데이트 (자기소유 venv + `anygarden-machine update` + WS 트리거) (#550)

- Commit: `2ac443b` (machine-side) + `1ffff26` (cluster/frontend) — squash-merged as one
- Author: Changyong Um
- Date: 2026-07-23
- PR: #550 (issue)

## Situation

anygarden-machine은 UI 없는 원격 systemd 데몬이라 업데이트가 각 호스트에서 수동(`pip install -U … && systemctl --user restart …`)이었다. #546의 Admin System 패널도 명령을 "표시만" 했다. 서버는 이미 머신별 WS 제어 채널(`machine_bus.send`)과 버전 감지(#546)를 갖췄으므로 원클릭 원격 업데이트가 가능했다. codex/claude는 네이티브 바이너리 자가교체로 쉽게 self-update하지만 우리는 Python 패키지라 불가 — 실물 조사(NousResearch/hermes-agent가 자기 전용 venv + `hermes update`, earendil-works/pi가 `pi update --self` 패키지매니저 위임)로 "툴이 설치 레이아웃을 소유하면 업데이트가 결정적"이라는 Hermes 패턴을 채택했다.

## Task

- 자기소유 설치 모델(B): 부트스트랩이 `~/.anygarden/machine/`에 전용 venv + 매니페스트 + shim + systemd 유닛 생성 → pip/uv/pipx 감지 제거
- `anygarden-machine update`: 매니페스트대로 최신을 전용 venv에 재설치, 수동/서버가 같은 코드 경로
- 서버 주도 트리거: admin/owner UI 버튼 → `self_update` 프레임 → 데몬 update → 결과 보고 → UI
- 안전 수준: 설치+재시작(systemd)+결과보고, 자동 롤백은 범위 밖
- 보안: 고정 패키지(anygarden-machine)·PyPI만, target은 PEP440 검증

## Action

machine-side (`2ac443b`):
- `install_manifest.py` — `~/.anygarden/machine/install.json`(설치법 기록, 부재/malformed→None)
- `updater.py` — `build_update_command`(고정 패키지·PEP440·no-shell) + `run_update`(예외 대신 UpdateResult)
- `cli.py` — `update`(--version/--restart), `bootstrap`(매니페스트+shim+유닛), `install_systemd_unit` 본문 헬퍼 추출
- `scripts/install.sh` — 전용 venv(python -m venv, pip 보장) → PyPI 설치 → bootstrap
- `protocol/frames.py` — `SelfUpdateFrame`(ServerFrame+parse) / `SelfUpdateResultFrame`(MachineFrame)
- `daemon.py` — `_handle_self_update`(updating 보고 → to_thread(run_update) → 성공 시 exit 플래그로 systemd 재시작, 에이전트 #451 재adopt / 실패 보고)

cluster/frontend (`1ffff26`):
- `api/v1/machines.py` — `POST /{id}/update`(owner/admin, machine_bus.send, update_status 기록, 오프라인 409, PEP440 400), MachineOut에 update 필드
- `db/models.py` + migration `055` — Machine.update_status/update_error/update_started_at
- `ws/machine_handler.py` — `self_update_result` 수신, 재등록 시 daemon_version 변경으로 success 확정
- `frontend` — `useMachines.updateMachineDaemon`, AdminMachines [Update] 버튼 + Version 옆 updating/updated/failed

## Decisions

계획(`.tmp/plan-550-server-driven-machine-update.md`)에서 4개 대안 비교:
- **A. 설치방식 감지·위임(Pi식)** — pip/uv/pipx/uv-tool 감지. 기각: uv venv엔 pip 부재 등 휴리스틱 엣지케이스. (실물 확인: 개발 머신이 uv·pip 없음)
- **B. 자기소유 venv + 매니페스트(Hermes식) ★채택** — 부트스트랩이 layout 통제·기록 → 결정적, 감지 불필요, 수동·서버 단일 경로. 비용은 install.sh 신설.
- C. git clone + git pull(Hermes 원형) — 기각: 우리는 PyPI 배포라 소스 이원화.
- D. standalone 바이너리 — 기각: 데몬이 agent(별도 Python)·엔진에 Python 환경 필요, 번들 비대.

결정적 근거: "설치 layout을 소유하면 업데이트는 그 venv에 pip -U 한 줄"이라는 Hermes 실증. 수동·서버가 동일 primitive 공유.

구현 중 조정: 트리거 엔드포인트를 계획의 "admin 전용"에서 **owner 기반**(`_get_owned_machine`, owner+admin)으로 변경 — 모든 `/api/v1/machines/{id}` 엔드포인트가 owner 기반이고, 자기 머신 업데이트는 권한 상승이 아니므로(owner가 이미 머신 통제) 라우터 일관성이 낫다.

보안: Pi의 공급망 방어(--ignore-scripts/락 핀) 정신을 차용 — 데몬은 고정 패키지·PyPI만 설치, target PEP440 검증(서버·데몬 이중).

가정(위반 시 재검토): 부트스트랩이 `python -m venv`(pip 보장)로 생성 → pip -U 항상 가능. 대상 머신은 부트스트랩 설치 전제(기존 수동 설치는 best-effort, 재설치 권장). 재등록 success 판정은 버전 변경 신호(동일 버전 no-op 업데이트는 updating 유지 → 후속).

## Result

- machine 435 passed(+25), cluster 1292 passed(+9 및 migration head 054→055 갱신), frontend 450 passed + `npm run build`(tsc). ruff 추가 코드 클린(잔존 F541는 register의 기존 이슈, 무관).
- 검증 범위: primitive(build_update_command/run_update mock), 프레임 parse, 데몬 핸들러(성공 exit/실패 보고), 엔드포인트(권한·오프라인·PEP440), 결과 수신·success 확정, migration up/down.
- 미결(수동/배포 검증 필요): install.sh 부트스트랩 → 실제 pip install → systemd 재시작 → 새 daemon_version 보고까지의 실기동 end-to-end는 배포 환경에서 확인 필요(자동 테스트는 subprocess/exit를 mock). 자동 롤백·일괄 업데이트·기존 설치 완전 지원은 후속.
