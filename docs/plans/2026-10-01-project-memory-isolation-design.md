# 프로젝트 기억 격리 설계

작성일: 2026-10-01. 기준 HEAD: `c8bca3d19bf2acb16afd7916cae0eb2066ae946a`와 현재 작업 트리.
범위: [QA-11 프로젝트 간 격리](../e2e/2026-09-30-project-room-autonomy-qa.md#qa-11-프로젝트-간-격리)의 남은 장기 기억·모델 세션 경로.
이 문서는 다음 구현을 위한 설계와 재현 근거다. 메모리 구현이나 QA-11 통과를 선언하지 않는다.

## 확인된 실패

입력은 `memory/shared/<room_id>/`로 분리했고 산출물은 출처 프로젝트로 제한했지만,
한 에이전트가 여러 프로젝트에 참여하면 `Agent.memory_md`가 모든 방의 프롬프트에 주입된다.
현재 흐름은 `Agent.memory_md` → 방별 welcome의 전역 `memory_md` → 단일
`ChatClient._memory_md` → `compose_memory_suffix(client, room_id)`다. `room_id`는 공유
파일과 ephemeral 설정을 선택하지만 장기 기억을 선택하지 않는다.

실제 기존 함수를 호출한 임시 pytest는 아래 테스트의 마지막 assertion에서 실패했다.
`uv run pytest <temporary-test-file> -q -p no:cacheprovider` 결과는 **1 failed in 0.25s**,
exit code 1이었다. 임시 파일은 `/tmp`에 만들고 실행 후 삭제했다. 합성 표시만 사용했다.

```python
from types import SimpleNamespace
from anygarden_agent.integrations.base import compose_memory_suffix

def test_room_b_does_not_receive_room_a_long_term_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = SimpleNamespace(
        _memory_md="CAREER_PRIVATE_ONLY_MARKER", _room_ephemeral={},
    )
    career = "11111111-1111-1111-1111-111111111111"
    garden = "22222222-2222-2222-2222-222222222222"
    assert "CAREER_PRIVATE_ONLY_MARKER" in compose_memory_suffix(client, career)
    assert "CAREER_PRIVATE_ONLY_MARKER" not in compose_memory_suffix(client, garden)
```

실패 프롬프트에는 `<memory>CAREER_PRIVATE_ONLY_MARKER</memory>`와
`memory/notes.md`에 기록하라는 지침이 포함됐다. 실제 모델의 추정 행동에 의존하지 않는
프롬프트 구성 단계의 재현이며, 전체 동시 프로젝트 E2E 실행은 아니다.

## 선택과 보장 범위

최소 구현은 **에이전트·방별 기억**이다. 기존 에이전트의 이름, 역할, `agents_md`, skills,
도구, provider/model, 인증과 권한 설정을 유지하면서 `(agent_id, room_id)`를 저장·동기화·
캐시·파일·세션 경계로 삼는다. 같은 프로젝트의 다른 방도 자동으로 기억을 합치지 않는다.
공유가 필요하면 위임의 입력 참조나 사용자 지정 가져오기로 출처를 명시한다. DM은 그 DM
방의 별도 기억을 가진다. 이 선택은 프로젝트 간 격리를 충족하는 더 좁은 기본 경계다.

| 대안 | 장점 | 비용·판정 |
| --- | --- | --- |
| 에이전트·방별 기억 | 기존 방·세션 키 재사용, DM 경계 명확, 작은 일관된 변경 | 같은 프로젝트 내 공유는 명시적으로 전달. 이번 구현 선택 |
| 에이전트·프로젝트별 기억 | 프로젝트 내 여러 방의 기억 연속성 | 프로젝트 없는 방·이동·DM 정책과 자동 공유 권한 추가 필요. 후속 요구가 있을 때 검토 |
| 프로젝트별 프로세스·작업공간·OS 격리 | 파일 읽기까지 강한 경계 가능 | 기존 작업공간·도구·권한과 호환 정책 필요. 이번 범위 밖 |

보장하는 것은 다른 프로젝트의 제품 관리 기억·입력·세션이 자동 주입되거나 잘못된 방에
저장·동기화·재사용되지 않는다는 것이다. 현재 실행기는 설정된 workspace, HOME, skills와
도구를 유지한다. 허용된 도구가 다른 디렉터리를 직접 읽거나 사용자 공통 지침에 정보를
적어 둔 경우까지 차단하는 OS 보안 격리는 아니다. 폴더 이름만 바꾸고 읽기 차단을
보장한다고 표현하지 않는다. 다른 방의 데이터를 읽지 말라는 생성 지침도 함께 갱신한다.

## 저장·전달 계약

1. `AgentRoomMemory`를 추가한다. 키는 `(agent_id, room_id)`, 값은 `memory_md`,
   `revision`, `session_epoch`, `updated_at`이다. 빈 기억은 정상 상태다. 기존
   `Agent.memory_md`는 출처가 없는 legacy 기록으로 보존한다.
2. welcome은 인증된 에이전트의 **해당 연결 방** 기억과 revision/session epoch만
   전달한다. scoped payload는 방 ID를 명시한다. 클라이언트는
   `_room_memory[room_id]`에 저장한다. `compose_memory_suffix`는 정확히 현재 방의
   값만 선택하며, 미수신·미지정 방은 빈 기억으로 처리한다. 전역 `_memory_md`나 이전
   방 값으로 fallback하지 않는다. 재연결의 빈 snapshot은 해당 방의 오래된 캐시를 지운다.
3. `SyncDesiredStateFrame`/`SpawnManifest`에는 현재 배정된 방의 scoped snapshot map을
   추가한다. 파일은 `memory/rooms/<canonical-room-uuid>/notes.md`에 materialize한다.
   전역 `memory/notes.md`를 런타임 기억으로 읽거나 덮어쓰지 않는다. manifest store가
   이 map을 보존하고 cold spawn·머신 이동도 같은 layout으로 복원한다.
4. daemon watcher의 hash와 revision 상태는 `(agent_id, room_id, generation)`으로
   구분한다. `AgentMemoryUpdateFrame`에 `room_id`, 현재 `generation`, `base_revision`을
   넣는다. 인증 머신이 현재 배치 머신이고, generation이 최신이고, 활성 방 참여가
   유효할 때만 해당 row를 갱신한다. source room 없는 legacy update는 명시적 오류로
   거절하며 기존 archive나 임의 방에 기록하지 않는다.
5. 서버는 revision CAS로 관리자 편집과 오래된 daemon snapshot의 덮어쓰기 경쟁을
   막는다. 성공·충돌 응답에 방 ID와 authoritative revision/snapshot을 전달한다.
   daemon은 응답 후 기준 revision/hash를 바꾼다. 실패한 전송을 먼저 완료로 캐시하지
   않는다. 현재 클라이언트에는 scoped change를 전달하여 다음 turn이 최신 기억을 쓴다.
6. 생성 `AGENTS.md`와 prompt의 기억 정책은 현재 방의 정확한 notes 경로를 안내한다.
   ephemeral 방은 기존 영속 기록 금지 지침을 유지하고, 서버도 해당 방의 새 agent
   영속 업데이트를 거절한다. 현재 방의 기존 기억 조회와 관리자 편집 계약은 유지한다.
   ephemeral은 과거 native history 삭제나 호스트 파일 읽기 차단을 뜻하지 않는다.

새 디렉터리·파일에는 기존 안전 write/chmod 규칙을 적용한다. UUID가 아닌 scope,
`..`, 절대 경로, 역슬래시, NUL, scope 디렉터리·notes 파일 symlink를 거절한다. 다른 방
파일과 legacy 파일을 임의 삭제하지 않는다. 배정 제거 후 남은 파일은 보존할 수 있지만
snapshot map, prompt와 watcher의 활성 scope에서 제외한다.

## 기존 기억 편집과 legacy 정책

기존 `AgentUpdate.memory_md/memory_md_set`와 `AgentOut.memory_md`, 단일 Agent 조회는
관리자가 기억을 읽고 수정하는 API 계약이다. 현재 `frontend/src` 전체 검색에서는 이 필드의
전용 편집 UI를 찾지 못했다. `ManagedWorkspaceEditor`는 별도의 workspace 파일 편집기다.
따라서 API 계약을 보존하면서 현재 방의 기억을 조회·편집하는 명시적 경로를 함께 제공해야 한다.

| 위치·조건 | 기본값과 표시 | 저장·동작 |
| --- | --- | --- |
| 방 안에서 현재 담당자 기억 조회 | 현재 방 제목과 scoped 기억 표시 | 기존 관리자 권한 범위에서 해당 `(agent, room)`만 편집·clear |
| Agent 설정에서 방 문맥 없음 | 기억의 방별 목록을 표시하고 조회할 방을 선택 | 선택 전에는 편집 입력을 숨김. 기본 전역 기억 입력 생성 금지 |
| 신규 Agent 생성 | 기억 입력 없음, 배정 방의 빈 기억으로 시작 | 기존 identity·도구 설정 흐름 유지 |
| legacy 전역 기록 존재 | 접을 수 있는 ‘이전 전역 기록’ 항목과 자동 주입 중단 설명 | 조회·편집·export 유지. 선택한 대상 방으로 명시적 가져오기 가능 |
| legacy 전역 기록 없음 | archive 관리 항목 숨김 | 빈 예외 설정을 일반 흐름에 노출하지 않음 |
| ephemeral 방 | 임시 대화 표시와 agent 영속 기록 금지 설명 | 현재 방의 기존 기억과 관리자 편집은 유지, daemon 영속 갱신 차단 |

새 scoped read/update API는 방 접근·관리자 권한과 expected revision을 검사한다. scoped
관리자 편집은 파일 snapshot과 다음 prompt에 반영하고 해당 방의 `session_epoch`를 올려
이전 모델 history에 남은 수정 전 내용을 계속 사용하지 않게 한다. 일반 agent append는
revision만 올려 매 기록마다 모델 세션을 초기화하지 않는다.

기존 `memory_md` 필드는 legacy archive라는 응답 metadata·API 문서·UI 표시를 붙여
계속 읽고 수정할 수 있게 한다. 저장에 성공했는데 런타임 효과가 사라진 이유를 숨기지
않는다. runtime 적용은 사용자가 대상 방을 선택해 가져올 때 수행한다. 출처 없는 기존
기억을 모든 방이나 현재 우연히 유일한 방으로 자동 복사하지 않는다. 임의 삭제·묵시적
공유·묵시적 설정 폐기를 하지 않는다.

## 모델 세션 전환과 재시작

현재 production CLI는 Codex/Pi 모두 `RoomExecutionAdapter`와 `LocalExecutionManager`를
사용한다. `SessionScope`는 이미 agent/authority/room/thread/workspace/policy/engine을
해시하므로 새 방 사이의 기본 handle 분리는 존재한다. 그러나 그 방별 history 자체에
전역 기억이 이미 주입됐을 수 있어 저장 경계 변경만으로 오염이 사라지지 않는다.

최초 적용 시 caller policy epoch에 고정 `room-memory-v1` 버전과 해당 방 `session_epoch`를
포함한다. 기존 generation/provider/model/endpoint fence는 유지한다. 이 변경으로 이전
manager session을 재사용하지 않고 새 history를 시작한다. legacy
`.anygarden-engine-sessions.json` handle의 import도 중단한다. 기존 model/provider/cwd
검사는 기억 오염 여부를 증명하지 못한다. 파일·native history는 삭제하지 않고 이전
기록으로 보존한다. 이후 같은 방·thread·epoch의 정상 history는 restart 후 재개한다.

일반 export된 Codex/Pi adapter 경로도 오래된 unversioned store를 자동 restore하지
않는지 확인한다. production 경로만 수정하고 테스트·직접 등록 경로에서 전역 기억이나
legacy handle을 재도입하지 않는다. root `MEMORY.md`와 생성된 workspace의
`memory/notes.md` link도 점검한다. 제품 생성물은 scoped 안내로 바꾸되 사용자가 직접
작성한 공통 지침·파일을 식별 없이 삭제하거나 내용을 다시 쓰지 않는다.

## 구현 위치와 검증

아래 line은 작성 시점이며 변경 후 함수명과 계약을 기준으로 추적한다.

| 경로 | 현재 근거·변경점 |
| --- | --- |
| `packages/cluster/anygarden/db/models.py:439`, `db/migrations/versions/` | 전역 `Agent.memory_md`; scoped table/migration과 legacy 보존 |
| `packages/cluster/anygarden/api/v1/agents.py:223,759,950` | 기존 read/update/clear API; scoped 편집과 archive 명시 |
| `packages/cluster/anygarden/ws/handler.py:1021,1056,1120`, `ws/protocol.py:338` | 모든 welcome의 전역 snapshot; 연결 방 scoped lookup/payload/change |
| `packages/agent/anygarden_agent/client.py:226,977`, `integrations/base.py:387` | 단일 cache·전역 prompt 주입; 방별 cache와 정확한 선택 |
| `packages/agent/anygarden_agent/memory/compose.py:20` | 전역 notes 정책; 현재 방 notes 경로와 ephemeral 정책 |
| `packages/cluster/anygarden/scheduler/lifecycle.py:1771`, `machine/protocol/frames.py:51,392` | 전역 sync/update; scoped maps, room/generation/revision, ack |
| `packages/cluster/anygarden/machine/spawner.py:331,658,772,817` | 전역 생성 지침·notes snapshot·root MEMORY·workspace link; scoped bootstrap |
| `packages/cluster/anygarden/machine/daemon.py:790,1078`, `machine/manifest_store.py` | cold spawn/watcher/영속 manifest; 방별 파일·hash·revision |
| `packages/cluster/anygarden/ws/machine_handler.py:204` | agent_id만으로 전역 DB update; authenticated machine·generation·membership·CAS |
| `packages/agent/anygarden_agent/integrations/room_execution.py:155,187,210,238` | per-room/thread scope, epoch 및 legacy import; 기억 version fence |
| `packages/agent/anygarden_agent/runtime/execution/{contracts,launch,manager}.py` | 기존 session key/config fence 유지, contaminated legacy import 차단 |
| `packages/agent/anygarden_agent/integrations/engine_session_store.py`, `runtime/execution/room.py` | unversioned resume map과 사용자 설정·도구 보존 경계 |

구현 순서는 DB/API 계약 → welcome/client/prompt → materialize/manifest/watcher/authenticated
update → epoch/legacy session 전환 → 현재 방 편집/archive UI와 통합 회귀다.

필수 회귀는 위 pytest를 scoped 두 marker로 통과시키고, 동일 Agent의 CAREER/GARDEN
방에서 읽기·수정·clear·교차 쓰기 거절을 검증하는 것이다. 추가로 둘의 동일 notes 본문도
별도 watcher key로 전송되는지, 재연결 빈 snapshot과 cold restart/머신 이동이 다른 방
fallback 없이 복원되는지, forged machine·stale generation·비참여 방·경로/symlink·CAS
충돌이 저장 전에 거절되는지, 관리자가 편집한 기억을 현재 방에서 다시 조회할 수 있는지
검증한다. legacy archive 보존과 명시적 가져오기 및 ephemeral 경로도 확인한다.

Codex/Pi 양쪽에서 upgrade 전 handle은 재개하지 않고 정상 scoped handle은 방·thread별
restart 후 재개하는지 검증한다. 기존 테스트 위치는
`packages/agent/tests/test_integrations/test_base_adapter.py`, `test_memory_compose.py`,
`test_room_execution.py`의 `test_room_resume_restart_and_thread_scope`/legacy import 사례,
`test_execution/test_local_execution.py`의 `test_session_scope_isolation`,
`packages/cluster/tests/machine/test_daemon.py`의 memory flush 사례,
`test_materialize.py:844`와 `tests/test_ws_handler.py`, `tests/test_agents_api.py`다.
이를 통과한 뒤 실제 두 프로젝트의 모델 turn과 산출물에서 고유 표시를 확인해야
QA-11의 전체 증거가 완성된다.
