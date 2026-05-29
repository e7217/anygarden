# 08 · 운영: 패키징, CLI, 배포, 테스트

> 이 문서는 이 구현의 **핵심**이다. 사용자가 명시한 요구사항("Docker 없음 + uvx/바이너리로 쉬운 기동")은 아키텍처가 아니라 **운영·배포 계층**에서 충족된다. 다른 모든 결정이 옳아도 이 문서가 틀리면 사용자가 원하는 UX가 나오지 않는다.

## 8.1 패키징 개요

이 구현은 **3개의 독립 Python 패키지 + 3개의 독립 저장소**로 배포된다. 자세한 디렉토리 트리는 [01-architecture.md §1.2 코드 레이아웃](01-architecture.md) 및 [01-architecture.md §1.10.2](01-architecture.md)에 정본이 있다. 이 문서는 그 전제 위에서 **패키징·배포 결정**만 다룬다.

| 패키지 | 저장소 | 루트 import | PyPI 이름 | 진입점 CLI |
|---|---|---|---|---|
| **anygarden-server** | `anygarden-server/` | `anygarden` | `anygarden-server` | `anygarden-server` |
| **anygarden-sdk** | `anygarden-sdk/` | `anygarden_sdk` | `anygarden-sdk` | `anygarden-agent`, `anygarden-client` |
| **anygarden-machine** | `anygarden-machine/` | `anygarden_machine` | `anygarden-machine` | `anygarden-machine` |

**서버·SDK·Machine을 별도 저장소로 분리한 이유**는 [01-architecture.md §1.2](01-architecture.md)와 [01-architecture.md §1.10.2](01-architecture.md)의 "구조적 원칙"에 명시되어 있다. 요약:

1. 서버·SDK·Machine은 릴리스 주기가 각기 다르다 (서버는 신중, SDK는 빠른 반복, Machine은 호스트별 배포)
2. SDK는 여러 언어 버전(Python / TypeScript)이 생길 예정이므로 서버와 묶이면 안 된다
3. Machine에는 `anygarden-machine`(Daemon)과 `anygarden-sdk`(Agent subprocess용)만 설치되며, 서버 의존성(SQLAlchemy, Alembic 등)이 딸려오지 않는다
4. Machine Daemon은 머신 호스트에 상주하는 별도 배포물이며 서버·SDK와 배포 사이클이 다르다
5. 역방향 의존성 없음: 세 패키지 모두 서로를 런타임 의존하지 않는다

세 저장소 중 **서버와 SDK는 `protocol/frames.py` 파일을 문자 그대로 복사하여 공유**한다. 양쪽 CI가 파일 해시 비교 테스트(`tests/test_protocol_compat.py`)를 돌려 깨지면 실패시킨다. Machine 저장소는 별도의 Machine↔Server 프레임(`anygarden_machine/protocol/frames.py`)을 가지며, 이는 §10의 프로토콜을 따른다.

Python 패키지 이름(`anygarden-server`, `anygarden-sdk`, `anygarden-machine`)은 하이픈을 쓰고, Python import 이름(`anygarden`, `anygarden_sdk`, `anygarden_machine`)은 언더스코어 관례를 따른다 (PEP 8).

> **`anygarden-client`는 `anygarden-sdk` 패키지에 속한다**. 유저 클라이언트가 서버 의존성(SQLAlchemy, Alembic, prometheus-client 등)을 필요로 하지 않기 때문이다. 관리 기능(`anygarden-client admin init`, `anygarden-client admin token create` 등)은 서버의 **REST API를 HTTP로 호출**하는 방식이며, 서버 코드가 로컬에 있을 필요가 없다.

## 8.2 `anygarden-server` 패키지

### 8.2.1 pyproject.toml

```toml
# anygarden-server/pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "anygarden-server"
version = "0.1.0"
description = "Lightweight multi-agent chat server"
authors = [{ name = "Anygarden Team" }]
license = "MIT"
readme = "README.md"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.110,<0.120",
    "uvicorn[standard]>=0.29",
    "sqlalchemy[asyncio]>=2.0,<2.1",
    "aiosqlite>=0.19",
    "pydantic>=2.6,<3.0",
    "pydantic-settings>=2.2",
    "python-jose[cryptography]>=3.3",
    "argon2-cffi>=23.1",
    "structlog>=24.1",
    "prometheus-client>=0.20",
    "alembic>=1.13",
    "click>=8.1",
]

[project.optional-dependencies]
postgres = ["asyncpg>=0.29", "psycopg[binary]>=3.1"]
binary = ["pyinstaller>=6.0"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "locust>=2.24",
    "ruff>=0.3",
    "mypy>=1.9",
]

[project.scripts]
anygarden-server = "anygarden.cli:main"

[project.urls]
Homepage = "https://github.com/anygarden/anygarden"
Documentation = "https://docs.anygarden.io"

[tool.hatch.build.targets.wheel]
packages = ["anygarden"]  # 루트의 anygarden/ 디렉토리 (src-layout 아님)
```

**의존성 개수**: 12개 (필수) + 2개 (PG 옵션) — PyPI에서 모두 성숙한 패키지. 버전 핀은 [01-architecture.md §1.7](01-architecture.md)의 정본 의존성 표와 일치해야 한다.

### 8.2.2 CLI 명령어 스펙

```
anygarden-server [command] [options]

Commands:
  (기본)        서버 기동
  init          설정 파일과 DB 초기화
  migrate       DB 마이그레이션 수동 실행
  version       버전 출력

Options:
  --host TEXT           바인드 호스트 [기본: 127.0.0.1]
  --port INTEGER        바인드 포트 [기본: 8000]
  --db TEXT             DB URL [기본: sqlite+aiosqlite:///~/.anygarden/anygarden.db]
  --config PATH         설정 파일 [기본: ~/.anygarden/config.toml]
  --log-level TEXT      로그 레벨 [기본: INFO]
  --log-format TEXT     로그 포맷: text | json [기본: text]
  --workers INTEGER     uvicorn 워커 수 [기본: 1]
  --version             버전 출력
  --help                도움말
```

**구현**:

```python
# anygarden-server/anygarden/cli.py
# 서버 전용 CLI. `anygarden-client`는 anygarden-sdk 패키지에 속한다 (§8.3.5).
import click
import uvicorn
from pathlib import Path
from .config import load_config, get_default_config_path
from .app import create_app
from .db.migrations import run_migrations

@click.group(invoke_without_command=True)
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--db", default=None)
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
@click.option("--log-level", default=None)
@click.option("--workers", default=1, type=int)
@click.version_option()
@click.pass_context
def main(ctx, host, port, db, config_path, log_level, workers):
    """Anygarden chat server."""
    if ctx.invoked_subcommand is None:
        config = load_config(
            path=config_path or get_default_config_path(),
            overrides={"host": host, "port": port, "db_url": db, "log_level": log_level},
        )
        if not config.initialized:
            _initial_setup(config)
        run_migrations(config.db_url)
        app = create_app(config)
        uvicorn.run(
            app,
            host=config.host,
            port=config.port,
            workers=workers,
            log_config=None,
        )

def _initial_setup(config):
    """첫 실행 시 설정 파일과 DB 디렉토리 생성."""
    config_dir = Path("~/.anygarden").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "agents").mkdir(exist_ok=True)
    (config_dir / "logs").mkdir(exist_ok=True)
    config.save(config_dir / "config.toml")
    click.echo(f"Initialized Anygarden at {config_dir}")

@main.command()
@click.option("--force", is_flag=True)
def init(force):
    """설정 파일과 DB 초기화."""
    ...

@main.command()
def migrate():
    """DB 마이그레이션 실행."""
    ...
```

### 8.2.3 설정 파일 (`~/.anygarden/config.toml`)

첫 실행 시 자동 생성되는 기본 설정:

```toml
# ~/.anygarden/config.toml
[server]
host = "127.0.0.1"
port = 8000
log_level = "INFO"
log_format = "text"  # "json"도 가능

[database]
url = "sqlite+aiosqlite:///~/.anygarden/anygarden.db"
echo = false
pool_size = 10

[auth]
jwt_secret = "auto-generated-on-first-run"
jwt_algorithm = "HS256"
jwt_expires_hours = 24
api_token_length = 32

[orchestration]
typing_enabled = true
cooldown_ms = 0  # 0이면 비활성
mention_priority = true

[observability]
metrics_enabled = true
metrics_path = "/metrics"
```

JWT secret은 첫 실행 시 `secrets.token_urlsafe(32)`로 자동 생성되어 파일에 저장된다. 사용자는 건드릴 일이 없다.

### 8.2.4 첫 실행 UX

```bash
$ uvx anygarden-server
[INFO] Initializing Anygarden at /home/me/.anygarden
[INFO] Generated JWT secret
[INFO] Created database: /home/me/.anygarden/anygarden.db
[INFO] Running migrations... done (3 migrations applied)
[INFO] Anygarden server v0.1.0 listening on http://127.0.0.1:8000
[INFO] WebSocket endpoint: ws://127.0.0.1:8000/ws/rooms/{room_id}
[INFO] Metrics: http://127.0.0.1:8000/metrics
[INFO] Press Ctrl+C to stop
```

**5초 이내에 "사용 가능한 서버" 상태로 진입하는 것**이 목표다.

## 8.3 `anygarden-sdk` 패키지

### 8.3.1 pyproject.toml

```toml
# anygarden-sdk/pyproject.toml
[project]
name = "anygarden-sdk"
version = "0.1.0"
description = "Anygarden multi-agent chat SDK and CLI"
requires-python = ">=3.11"

dependencies = [
    "websockets>=12.0,<14.0",
    "httpx>=0.27",
    "pydantic>=2.6",
    "click>=8.1",
    "structlog>=24.1",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
claude-code = ["claude-agent-sdk>=0.3"]
codex = ["codex-sdk>=0.4"]
openhands = ["openhands-ai>=0.20"]
deep-agents = ["langgraph>=0.2", "deepagents>=0.1"]
openai = ["openai>=1.30"]
anthropic = ["anthropic>=0.25"]
all = [
    "claude-agent-sdk>=0.3",
    "codex-sdk>=0.4",
    "openhands-ai>=0.20",
    "langgraph>=0.2",
    "deepagents>=0.1",
    "openai>=1.30",
    "anthropic>=0.25",
]
tui = ["textual>=0.58"]

[project.scripts]
anygarden-agent = "anygarden_sdk.cli:agent_main"
anygarden-client = "anygarden_sdk.cli:client_main"
```

**핵심 설계**: 엔진별 의존성을 `[project.optional-dependencies]`로 분리. 사용자는 자신이 쓸 엔진만 설치하면 된다.

```bash
# Claude Code 사용자
uvx --from "anygarden-sdk[claude-code]" anygarden-agent --engine claude-code ...

# Codex 사용자
uvx --from "anygarden-sdk[codex]" anygarden-agent --engine codex ...

# 여러 엔진 혼용
uvx --from "anygarden-sdk[all]" anygarden-agent --engine claude-code ...
```

### 8.3.2 SDK 구조

SDK는 **별도 저장소 `anygarden-sdk/`**이며, import 경로는 `anygarden_sdk.*`이다 (서버의 `anygarden.*`와 구분).

```
anygarden-sdk/                     # 별도 PyPI 패키지 저장소
├── pyproject.toml              # name = "anygarden-sdk", packages = ["anygarden_sdk"]
├── README.md
├── anygarden_sdk/                 # Python 패키지 루트
│   ├── __init__.py
│   ├── cli.py                  # anygarden-agent CLI 엔트리
│   ├── client.py               # ChatClient (WebSocket 기반)
│   ├── protocol/
│   │   ├── __init__.py
│   │   ├── frames.py           # 서버 anygarden/ws/protocol.py와 문자 그대로 동일
│   │   └── versioning.py       # 프로토콜 버전 협상
│   ├── auth/
│   │   └── token.py            # 토큰 로드/검증 (환경변수, 파일)
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── base.py             # EngineAdapter 추상 클래스
│   │   ├── claude_code.py      # integrate_with_claude_code
│   │   ├── codex.py            # integrate_with_codex
│   │   ├── openhands.py        # integrate_with_openhands
│   │   ├── deep_agents.py      # integrate_with_deep_agents
│   │   ├── openai.py           # integrate_with_openai
│   │   └── anthropic.py        # integrate_with_anthropic
│   └── profile/
│       ├── __init__.py
│       ├── loader.py           # ~/.anygarden/agents/*.yaml 로드
│       └── schema.py           # 프로필 스키마 검증
└── tests/
    ├── test_client.py
    ├── test_protocol_compat.py # server의 frames.py와 해시 비교
    └── test_integrations/
```

### 8.3.3 ChatClient 핵심 API

```python
# anygarden-sdk/anygarden_sdk/client.py
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Awaitable
from uuid import UUID
import websockets
import structlog

from .protocol.frames import IncomingFrame, OutgoingFrame, MessageFrame

log = structlog.get_logger()


class ChatClient:
    """Anygarden 채팅 서버 클라이언트.

    에이전트 엔진에 통합하거나 단독으로 사용한다.
    """

    def __init__(
        self,
        server_url: str,
        token: str,
        agent_id: UUID | None = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self._last_seq: dict[UUID, int] = {}
        self._ws_by_room: dict[UUID, websockets.WebSocketClientProtocol] = {}
        self._handlers: list[Callable[[MessageFrame], Awaitable[None]]] = []
        self._join_handlers: list[Callable[[dict], Awaitable[None]]] = []

    # ─── 공개 API ────────────────────────────────────────

    async def join_room(self, room_id: UUID) -> None:
        """Room 구독 시작. 재연결 루프를 백그라운드로 실행."""
        task = asyncio.create_task(self._room_loop(room_id))

    async def send(
        self,
        room_id: UUID,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """메시지 전송."""
        ws = self._ws_by_room.get(room_id)
        if ws is None:
            raise RuntimeError(f"Not joined to room {room_id}")
        frame = OutgoingFrame(
            type="send",
            room_id=room_id,
            content=content,
            metadata=metadata or {},
        )
        await ws.send(frame.model_dump_json())

    async def create_sub_room(
        self,
        parent_room_id: UUID,
        participants: list[UUID],
        purpose: str,
    ) -> UUID:
        """서브에이전트용 자식 Room 생성. 채널 기반 위임의 진입점."""
        ws = self._ws_by_room.get(parent_room_id)
        frame = {
            "type": "create_room",
            "parent_room_id": str(parent_room_id),
            "participants": [str(p) for p in participants],
            "purpose": purpose,
        }
        await ws.send(json.dumps(frame))
        # 응답 대기는 on_message 핸들러에서 처리

    def on_message(self, handler: Callable[[MessageFrame], Awaitable[None]]):
        """메시지 수신 콜백 등록. 데코레이터로도 사용 가능."""
        self._handlers.append(handler)
        return handler

    def on_join_room(self, handler):
        """새 Room 참여 알림 콜백."""
        self._join_handlers.append(handler)
        return handler

    # ─── 엔진 통합 헬퍼 ─────────────────────────────────

    def integrate_with_claude_code(self, agent):
        """Claude Code SDK 에이전트에 이 클라이언트를 연결한다.
        자세한 구현은 integrations/claude_code.py 참조."""
        from .integrations.claude_code import bind
        bind(self, agent)

    def integrate_with_codex(self, session):
        from .integrations.codex import bind
        bind(self, session)

    def integrate_with_openhands(self, runtime):
        from .integrations.openhands import bind
        bind(self, runtime)

    def integrate_with_deep_agents(self, graph):
        from .integrations.deep_agents import bind
        bind(self, graph)

    def integrate_with_openai(self, client, model: str):
        """일반 OpenAI 호환 API (로컬 LLM, Azure OpenAI 등 포함)."""
        from .integrations.openai import bind
        bind(self, client, model)

    # ─── 내부 재연결 루프 ───────────────────────────────

    async def _room_loop(self, room_id: UUID):
        retry = 0
        while True:
            try:
                last = self._last_seq.get(room_id, 0)
                # 토큰은 반드시 Sec-WebSocket-Protocol subprotocol 헤더로.
                # 쿼리 파라미터 `token` 사용 금지 (05-security.md §5.7.1).
                url = (
                    f"{self.server_url.replace('http', 'ws')}/ws/rooms/{room_id}"
                    f"?since_seq={last}"
                )
                async with websockets.connect(
                    url,
                    subprotocols=["anygarden.v1", f"bearer.{self.token}"],
                ) as ws:
                    self._ws_by_room[room_id] = ws
                    retry = 0
                    async for raw in ws:
                        frame = IncomingFrame.model_validate_json(raw)
                        await self._dispatch(frame)
            except websockets.ConnectionClosed:
                self._ws_by_room.pop(room_id, None)
                await asyncio.sleep(min(2 ** retry, 30))
                retry += 1
            except Exception as e:
                log.error("room_loop_error", room_id=str(room_id), error=str(e))
                await asyncio.sleep(5)

    async def _dispatch(self, frame: IncomingFrame):
        if frame.type == "message":
            self._last_seq[frame.room_id] = frame.seq
            for h in self._handlers:
                await h(frame)
        elif frame.type == "join_room":
            for h in self._join_handlers:
                await h(frame)
```

### 8.3.4 엔진 어댑터 패턴 (예: Claude Code)

```python
# anygarden-sdk/anygarden_sdk/integrations/claude_code.py
from __future__ import annotations
from ..client import ChatClient, MessageFrame

def bind(client: ChatClient, agent) -> None:
    """Claude Code SDK 에이전트에 ChatClient를 연결한다.

    메시지 수신 → 에이전트 대화 컨텍스트에 주입
    에이전트 응답 → ChatClient로 send
    """
    # Claude Code SDK는 conversation hook 패턴을 제공
    # (실제 SDK API는 버전마다 다르므로 버전 pinning 필요)

    @client.on_message
    async def inject_message(msg: MessageFrame):
        # 자기 자신의 메시지는 무시 (에코 방지)
        if msg.sender_id == client.agent_id:
            return
        # Claude Code agent에 user turn으로 주입
        await agent.inject_user_message(
            content=f"[{msg.sender_name}] {msg.content}",
            metadata=msg.metadata,
        )

    # 에이전트의 응답을 자동으로 ChatClient로 전송
    original_handler = agent.on_response
    async def wrapped_response(response):
        if original_handler:
            await original_handler(response)
        # 현재 참여 중인 Room에 전송
        for room_id in client._ws_by_room:
            await client.send(room_id, response.text)

    agent.on_response = wrapped_response
```

나머지 엔진(Codex, OpenHands, Deep Agents, OpenAI)도 같은 패턴을 따른다. **SDK 사용자는 `integrate_with_*()` 한 줄만 호출하면 된다**.

### 8.3.5 에이전트 CLI (`anygarden-agent`)

```python
# anygarden-sdk/anygarden_sdk/cli.py
import asyncio
import click
from pathlib import Path
from .client import ChatClient
from .profile.loader import load_agent_profile

@click.command()
@click.option("--engine", type=click.Choice([
    "claude-code", "codex", "openhands", "deep-agents", "openai", "anthropic"
]), required=True)
@click.option("--name", required=True, help="에이전트 이름")
@click.option("--server", default="ws://127.0.0.1:8000", help="서버 URL")
@click.option("--token", envvar="ANYGARDEN_TOKEN", required=True)
@click.option("--profile", type=click.Path(path_type=Path), help="프로필 YAML")
@click.option("--model", help="LLM 모델 ID (엔진별)")
@click.option("--room", multiple=True, help="자동 join할 Room ID들")
def agent_main(engine, name, server, token, profile, model, room):
    """Anygarden 에이전트 실행.

    예:
        anygarden-agent --engine claude-code --name PM
        anygarden-agent --engine codex --name TechLead --room sprint-42
        anygarden-agent --engine openai --model gpt-4o --name Host
    """
    profile_data = load_agent_profile(profile) if profile else {}
    asyncio.run(_run_agent(engine, name, server, token, profile_data, model, list(room)))

async def _run_agent(engine, name, server, token, profile, model, rooms):
    client = ChatClient(server, token)

    # 엔진별 어댑터 로드 (지연 import로 불필요한 의존성 회피)
    if engine == "claude-code":
        from claude_agent_sdk import Agent  # 옵셔널 의존성
        agent = Agent(
            name=name,
            system_prompt=profile.get("system_prompt"),
            model=model or profile.get("llm", {}).get("model"),
        )
        client.integrate_with_claude_code(agent)
    elif engine == "codex":
        from codex_sdk import Session
        session = Session(name=name, ...)
        client.integrate_with_codex(session)
    elif engine == "openhands":
        from openhands_ai import Runtime
        runtime = Runtime(...)
        client.integrate_with_openhands(runtime)
    elif engine == "deep-agents":
        from deepagents import create_deep_agent
        graph = create_deep_agent(...)
        client.integrate_with_deep_agents(graph)
    elif engine == "openai":
        from openai import AsyncOpenAI
        oai = AsyncOpenAI()
        client.integrate_with_openai(oai, model=model or "gpt-4o")
    elif engine == "anthropic":
        from anthropic import AsyncAnthropic
        anth = AsyncAnthropic()
        client.integrate_with_anthropic(anth, model=model or "claude-sonnet-4-5")

    # 자동 join할 Room들 참여
    rooms_to_join = rooms or profile.get("rooms", [])
    for r in rooms_to_join:
        await client.join_room(r)

    click.echo(f"Agent '{name}' running ({engine}). Press Ctrl+C to stop.")
    # 무한 대기
    await asyncio.Event().wait()

# PyInstaller 단일 바이너리(§8.4.3) 시나리오에서 `pyinstaller anygarden_sdk/cli.py`로
# 이 파일이 스크립트로 실행될 때 필요. uvx/PyPI 경로는 pyproject.toml의
# `[project.scripts]` 엔트리(`anygarden-agent = "anygarden_sdk.cli:agent_main"`)가
# 직접 호출하므로 불필요.
if __name__ == "__main__":
    agent_main()
```

`anygarden-client` CLI도 이 SDK 패키지(`anygarden_sdk.cli:client_main`)에서 제공된다 (§8.3.1 pyproject.toml 참조). 유저 클라이언트가 서버 의존성(SQLAlchemy, Alembic 등)을 가져올 필요가 없으므로, SDK의 가벼운 의존성(websockets, httpx, click)만으로 충분하다. 관리 기능(`anygarden-client admin ...`)은 서버의 REST API를 HTTP(httpx)로 호출하는 방식이다.

### 8.3.6 에이전트 프로필 YAML

```yaml
# ~/.anygarden/agents/pm.yaml
name: PM
role: project_manager
engine: claude-code
system_prompt: |
  당신은 Anygarden 프로젝트의 PM입니다.
  스프린트를 관리하고, 우선순위를 조정하며, 이해관계자와 소통합니다.
  질문에 간결하게 답하고, 결정이 필요한 사항은 명확히 제안하세요.

llm:
  model: claude-sonnet-4-5
  temperature: 0.7
  max_tokens: 2048

rooms:
  - name: sprint-42
    auto_join: true
  - name: general
    auto_join: true

# MCP 도구는 에이전트 엔진이 직접 관리 (서버와 무관)
mcp_servers:
  - name: github
    command: uvx
    args: ["mcp-server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
  - name: jira
    url: "http://localhost:9000/mcp"

# 서브에이전트 생성 규칙 (선택)
sub_agents:
  can_create: true
  max_depth: 2
  allowed_engines: ["claude-code", "openai"]
```

## 8.4 배포 시나리오

### 8.4.1 시나리오 A: 로컬 개발

```bash
# 터미널 1: 서버
uvx anygarden-server

# 터미널 2: 에이전트 1
ANYGARDEN_TOKEN=agt_xxx uvx --from "anygarden-sdk[claude-code]" \
    anygarden-agent --engine claude-code --name PM --room sprint-42

# 터미널 3: 에이전트 2
ANYGARDEN_TOKEN=agt_yyy uvx --from "anygarden-sdk[openai]" \
    anygarden-agent --engine openai --model gpt-4o --name Host --room sprint-42

# 터미널 4: 유저 (anygarden-client는 anygarden-sdk 패키지가 제공 — §8.3.1)
ANYGARDEN_USER_TOKEN=usr_zzz uvx --from anygarden-sdk anygarden-client \
    --user me --room sprint-42 --tui
```

4개 터미널, Docker 없음, `pip install` 없음, 설정 파일 없이도 동작.

### 8.4.2 시나리오 B: 단일 VPS (systemd user unit)

```ini
# ~/.config/systemd/user/anygarden-server.service
[Unit]
Description=Anygarden Chat Server
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/uvx anygarden-server --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
MemoryMax=512M
StandardOutput=append:%h/.anygarden/logs/server.log
StandardError=append:%h/.anygarden/logs/server-err.log

[Install]
WantedBy=default.target
```

```ini
# ~/.config/systemd/user/anygarden-agent-pm.service
[Unit]
Description=Anygarden PM Agent
After=anygarden-server.service
Requires=anygarden-server.service

[Service]
Type=simple
Environment="ANYGARDEN_TOKEN=agt_xxxxx"
ExecStart=%h/.local/bin/uvx --from "anygarden-sdk[claude-code]" \
    anygarden-agent --engine claude-code --name PM \
    --server ws://127.0.0.1:8000 \
    --profile %h/.anygarden/agents/pm.yaml
Restart=on-failure
RestartSec=10
MemoryMax=256M

[Install]
WantedBy=default.target
```

설치:

```bash
systemctl --user daemon-reload
systemctl --user enable anygarden-server anygarden-agent-pm
systemctl --user start anygarden-server anygarden-agent-pm
loginctl enable-linger me  # 사용자 로그아웃 후에도 유지
```

**Docker 필요 없음, root 권한 필요 없음, `/etc`에 쓰기 없음**.

### 8.4.3 시나리오 C: PyInstaller 단일 바이너리

Python이 설치되지 않은 환경(데모, 비개발자 배포)을 위한 백업 옵션.

**빌드 스크립트** (각 저장소 루트에서 실행):

```bash
# anygarden-server/scripts/build-binary.sh
#!/usr/bin/env bash
set -euo pipefail

# 임시 가상환경
python -m venv .build-venv
source .build-venv/bin/activate

# 현재 저장소(anygarden-server) + 자주 쓰이는 SDK 통합을 함께 번들링
pip install -e ".[binary]"
pip install "anygarden-sdk[claude-code,openai,anthropic]"

# PyInstaller로 onefile 빌드 — 서버
pyinstaller \
    --onefile \
    --name anygarden-server \
    --collect-all anygarden \
    --hidden-import aiosqlite \
    --hidden-import structlog \
    anygarden/cli.py

ls -lh dist/
# dist/anygarden-server   (~25-45MB)
# anygarden-client는 anygarden-sdk 패키지에서 빌드된다 (아래 SDK 빌드 스크립트 참조)
```

SDK(에이전트 CLI) 저장소는 별도로 빌드한다:

```bash
# anygarden-sdk/scripts/build-binary.sh
#!/usr/bin/env bash
set -euo pipefail

python -m venv .build-venv
source .build-venv/bin/activate
pip install -e ".[all,binary]"   # 4종 엔진 + pyinstaller

pyinstaller \
    --onefile \
    --name anygarden-agent \
    --collect-all anygarden_sdk \
    anygarden_sdk/cli.py

ls -lh dist/
# dist/anygarden-agent    (~30-50MB, 엔진 의존성 포함 시 더 큼)
```

**GitHub Actions로 자동 빌드**:

```yaml
# .github/workflows/release-binary.yml
name: Release binaries
on:
  push:
    tags: ['v*']

permissions:
  contents: write  # softprops/action-gh-release 가 release 생성을 위해 필요

jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        arch: [x64, arm64]
        exclude:
          - os: windows-latest
            arch: arm64
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: bash scripts/build-binary.sh
      # 참고: 구버전 `actions/upload-release-asset@v1`은 2021년 이후 deprecated.
      # softprops/action-gh-release는 tag push 시 release를 자동 생성/갱신하고
      # `files` glob으로 여러 아티팩트를 한 번에 업로드한다.
      - uses: softprops/action-gh-release@v1
        with:
          files: dist/anygarden-server*
          fail_on_unmatched_files: true
```

**설치 스크립트** (`https://get.anygarden.io`):

```bash
#!/usr/bin/env bash
# Anygarden 설치 스크립트
set -euo pipefail

REPO="anygarden/anygarden"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case $ARCH in
    x86_64) ARCH=x64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

LATEST=$(curl -s "https://api.github.com/repos/$REPO/releases/latest" \
    | grep -Po '"tag_name": "\K[^"]+')

mkdir -p "$INSTALL_DIR"
for bin in anygarden-server anygarden-agent anygarden-client; do
    URL="https://github.com/$REPO/releases/download/$LATEST/$bin-$OS-$ARCH"
    curl -sSL "$URL" -o "$INSTALL_DIR/$bin"
    chmod +x "$INSTALL_DIR/$bin"
done

echo "Installed to $INSTALL_DIR"
echo "Make sure $INSTALL_DIR is in your PATH"
echo ""
echo "Quick start:"
echo "  anygarden-server"
```

**사용자 UX**:

```bash
$ curl -sSL https://get.anygarden.io | sh
Installed to /home/me/.local/bin
Make sure /home/me/.local/bin is in your PATH

Quick start:
  anygarden-server

$ anygarden-server
[INFO] Anygarden server v0.1.0 listening on http://127.0.0.1:8000
```

Python 설치도 uv 설치도 필요 없음. 단일 파일 다운로드 + 실행.

### 8.4.4 시나리오 비교

| 시나리오 | 대상 | 설치 시간 | 디스크 | 업데이트 방법 |
|---|---|---|---|---|
| A. 로컬 dev | 개발자 | <10초 | uv 캐시 ~100MB | `uv tool upgrade anygarden-server` |
| B. systemd VPS | 운영 | ~30초 | ~200MB | `systemctl --user restart anygarden-server` 후 자동 최신 |
| C. 바이너리 | 비개발자 | <5초 | ~30MB × 3 | 재다운로드 |

### 8.4.5 릴리스 (PyPI 게시)

위 시나리오들은 패키지가 PyPI에 게시되어 있다고 가정한다. 게시는 **태그 푸시로 자동화**되어 있다 (`.github/workflows/release.yml`, #402). 패키지별 버전 태그를 밀면 워크플로가 빌드 → GitHub Release → **PyPI 게시(Trusted Publishing / OIDC)** 까지 수행한다. 로컬 `uv publish` 수동 게시는 더 이상 필요 없다.

```bash
# pyproject.toml의 version을 올린 뒤, 패키지별 태그를 푸시한다.
# 태그 prefix가 게시 대상 패키지를 결정한다:
#   anygarden-v<ver>          → packages/cluster (PyPI: anygarden)
#   anygarden-machine-v<ver>  → packages/machine (PyPI: anygarden-machine)
#   anygarden-agent-v<ver>    → packages/agent   (PyPI: anygarden-agent)
git tag anygarden-machine-v0.8.1 && git push origin anygarden-machine-v0.8.1
git tag anygarden-v0.8.1         && git push origin anygarden-v0.8.1
```

**락스텝 순서**: `anygarden`(서버)은 `anygarden-machine>=0.8`을 런타임 의존하므로, machine을 먼저(또는 동시에) 게시해야 cluster 설치 시 의존성이 즉시 해소된다.

**선행 1회 작업 (저장소 owner)**: PyPI의 `anygarden` / `anygarden-machine` / `anygarden-agent` 각 프로젝트에 Trusted Publisher를 등록해야 OIDC 게시가 동작한다 — PyPI 프로젝트 → *Manage → Publishing → Add a trusted publisher*:

- Owner: `e7217`, Repository: `anygarden`, Workflow: `release.yml`, Environment: (비움)

미등록 상태로 태그를 밀면 `Publish to PyPI` 스텝이 실패한다. 게시 스텝은 `skip-existing: true`라 동일 태그 재실행은 안전하다.

## 8.5 관측성 (Observability)

### 8.5.1 Prometheus 지표 5개

필수 지표만 선별. 더 많으면 측정 오버헤드와 알림 피로가 증가한다.

```python
# anygarden-server/anygarden/observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# 1. 연결 상태 (가장 중요)
WS_ACTIVE = Gauge(
    "anygarden_ws_active_connections",
    "Currently active WebSocket connections",
    ["kind"],  # "user" | "agent"
)
WS_CONNECTIONS_TOTAL = Counter(
    "anygarden_ws_connections_total",
    "Total WebSocket connections since start",
    ["kind", "result"],  # result: "accepted" | "rejected"
)

# 2. 메시지 처리량
MESSAGES_SENT = Counter(
    "anygarden_messages_sent_total",
    "Total messages sent",
)
MESSAGE_FANOUT_DURATION = Histogram(
    "anygarden_message_fanout_duration_seconds",
    "Time to fan-out a message to all subscribers",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

# 3. DB 지연
DB_WRITE_DURATION = Histogram(
    "anygarden_db_write_duration_seconds",
    "Message INSERT duration",
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.05, 0.1],
)

# 4. 에러 카테고리
ERRORS = Counter(
    "anygarden_errors_total",
    "Errors by category",
    ["category"],  # db | ws | auth | unknown
)

# 5. 재연결 복구
RECONNECT_RECOVERY = Histogram(
    "anygarden_reconnect_recovery_duration_seconds",
    "Time to fetch and send missed messages on reconnect",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)
RECONNECT_MESSAGES_SENT = Counter(
    "anygarden_reconnect_messages_sent_total",
    "Messages sent during reconnect recovery",
)
```

### 8.5.2 알림 규칙 (Prometheus Alertmanager)

```yaml
# config/alerts.yml
groups:
  - name: anygarden
    rules:
      - alert: AnygardenHighMessageLatency
        expr: histogram_quantile(0.99, rate(anygarden_message_fanout_duration_seconds_bucket[5m])) > 0.1
        for: 5m
        annotations:
          summary: "메시지 라우팅 p99 지연이 100ms 초과"

      - alert: AnygardenHighErrorRate
        expr: rate(anygarden_errors_total[5m]) > 0.1
        for: 5m
        annotations:
          summary: "에러율 0.1/s 초과"

      - alert: AnygardenDBSlow
        expr: histogram_quantile(0.99, rate(anygarden_db_write_duration_seconds_bucket[5m])) > 0.05
        for: 10m
        annotations:
          summary: "DB write p99 지연이 50ms 초과 — SQLite → PostgreSQL 승격 고려"

      - alert: AnygardenWSConnectionDrop
        expr: delta(anygarden_ws_active_connections[1m]) < -10
        annotations:
          summary: "1분 내 WebSocket 연결 10개 이상 급감 — 네트워크 이슈 의심"
```

### 8.5.3 구조화 로그 (structlog)

```python
# anygarden-server/anygarden/observability/logging.py
import structlog
import logging

def configure_logging(level: str = "INFO", format: str = "text"):
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )

# 사용 예
log = structlog.get_logger()
log.info("message_sent", room_id=str(room_id), sender=str(sender_id), seq=next_seq)
```

**JSON 로그 예시**:
```json
{"timestamp":"2026-04-08T21:42:15.123Z","level":"info","event":"message_sent","room_id":"abc-123","sender":"def-456","seq":42}
```

prod에서는 `--log-format json`을 사용하고, 이 로그를 Loki/Elasticsearch로 수집한다.

## 8.6 테스트 전략

### 8.6.1 테스트 피라미드

```
        ┌─────────────┐
        │  E2E (소수) │  <10개  — 실제 4종 엔진 통합
        ├─────────────┤
        │ 통합 (중간) │  ~40개  — FastAPI + DB + WS
        ├─────────────┤
        │   단위(多)  │  ~120개 — 순수 로직
        └─────────────┘
```

### 8.6.2 단위 테스트 (pytest)

```python
# tests/unit/test_message_service.py
import pytest
from anygarden.messages.service import MessageService

@pytest.mark.asyncio
async def test_seq_monotonic(db_session):
    svc = MessageService(db_session)
    msg1 = await svc.create_message(room_id=ROOM_A, sender_id=USER_A, content="hi")
    msg2 = await svc.create_message(room_id=ROOM_A, sender_id=USER_A, content="hi2")
    assert msg2.seq == msg1.seq + 1

@pytest.mark.asyncio
async def test_seq_isolated_per_room(db_session):
    svc = MessageService(db_session)
    m1 = await svc.create_message(room_id=ROOM_A, ...)
    m2 = await svc.create_message(room_id=ROOM_B, ...)
    # Room별로 seq가 독립
    assert m1.seq == 1
    assert m2.seq == 1
```

### 8.6.3 통합 테스트 (httpx + WebSocket)

```python
# tests/integration/test_ws_reconnect.py
import pytest
from httpx import AsyncClient
from anygarden.app import create_app
from anygarden.config import load_config

@pytest.fixture
async def client():
    app = create_app(load_config(test_mode=True))
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

@pytest.mark.asyncio
async def test_reconnect_since_seq(client, tmp_db):
    # 1) 방 생성
    room_resp = await client.post("/api/rooms", json={"name": "test"})
    room_id = room_resp.json()["id"]

    # 2) WebSocket 연결 후 3개 메시지 수신
    async with client.websocket_connect(f"/ws/rooms/{room_id}") as ws:
        for i in range(3):
            await ws.send_json({"type": "send", "content": f"msg-{i}"})
        msgs = [await ws.receive_json() for _ in range(3)]
        last_seq = msgs[-1]["seq"]

    # 3) 재연결 시 since_seq로 과거 메시지 요청
    async with client.websocket_connect(
        f"/ws/rooms/{room_id}?since_seq={last_seq - 2}"
    ) as ws:
        # 2개 메시지가 복구되어야 함
        recovered = [await ws.receive_json() for _ in range(2)]
        assert recovered[0]["seq"] == last_seq - 1
        assert recovered[1]["seq"] == last_seq
```

### 8.6.4 E2E 테스트 (엔진 통합)

E2E는 실제 엔진과 LLM API를 사용하므로 CI에서는 `pytest -m "not e2e"`로 제외하고, 로컬/별도 파이프라인에서 실행.

```python
# tests/e2e/test_claude_code_agent.py
import pytest
pytestmark = pytest.mark.e2e

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="no API key")
async def test_claude_code_echo(running_server):
    """실제 Claude Code SDK 에이전트가 메시지에 반응하는지."""
    from claude_agent_sdk import Agent
    from anygarden_sdk import ChatClient

    client = ChatClient("ws://localhost:8000", token=TEST_TOKEN)
    agent = Agent(name="Test", system_prompt="간단히 '안녕'이라고만 답하세요.")
    client.integrate_with_claude_code(agent)

    await client.join_room(TEST_ROOM_ID)
    await client.send(TEST_ROOM_ID, "테스트 메시지")

    # 3초 내에 에이전트가 응답했는지 확인
    received = await wait_for_agent_response(timeout=3)
    assert "안녕" in received.content
```

### 8.6.5 부하 테스트 (locust)

```python
# tests/load/locustfile.py
from locust import User, task, between
import websockets

class AnygardenUser(User):
    wait_time = between(1, 3)

    def on_start(self):
        self.ws = websockets.connect("ws://localhost:8000/ws/rooms/test-room")

    @task
    def send_message(self):
        self.ws.send(json.dumps({
            "type": "send",
            "content": "hello " * 10,
        }))
```

실행:
```bash
locust -f tests/load/locustfile.py --headless -u 200 -r 10 -t 5m
# 200 동시 연결, 초당 10개 증가, 5분간 실행
```

SLO 검증: p99 fanout 지연 <50ms, 에러 0, CPU <30%.

### 8.6.6 테스트 커버리지 목표

| 계층 | 목표 |
|---|---|
| 단위 | ≥80% |
| 통합 | 핵심 경로 100% (send, join, reconnect, auth) |
| E2E | 4종 엔진 각 1개 시나리오 |

## 8.7 CI/CD

서버와 SDK는 **별도 저장소**이므로 각자 워크플로를 갖는다. 두 저장소는 프로토콜 호환성 테스트를 공유해야 하므로, SDK 저장소의 CI가 `anygarden-server`의 `protocol/frames.py` 원본을 가져와 해시 비교하는 단계를 포함한다.

### 8.7.1 `anygarden-server` 저장소 — 테스트

```yaml
# anygarden-server/.github/workflows/test.yml
name: Test
on: [push, pull_request]

jobs:
  test:
    strategy:
      matrix:
        python: ['3.11', '3.12']
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e ".[dev,postgres]"
      - run: pytest tests/ -v --cov=anygarden
      - run: ruff check anygarden tests
      - run: mypy anygarden
```

### 8.7.2 `anygarden-sdk` 저장소 — 테스트 + 프로토콜 호환성

```yaml
# anygarden-sdk/.github/workflows/test.yml
name: Test
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ['3.11', '3.12']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --cov=anygarden_sdk
      - run: ruff check anygarden_sdk tests
      - run: mypy anygarden_sdk

  protocol-compat:
    # 서버 저장소의 frames.py와 해시 비교
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          path: sdk
      - uses: actions/checkout@v4
        with:
          repository: anygarden/anygarden-server
          path: server
          ref: main
      - run: |
          diff -q \
              sdk/anygarden_sdk/protocol/frames.py \
              server/anygarden/ws/protocol.py \
              || (echo "Protocol file drift!" && exit 1)
```

### 8.7.3 PyPI 릴리즈 — 각 저장소에서 독립 실행

```yaml
# anygarden-server/.github/workflows/release-pypi.yml
name: Release anygarden-server to PyPI
on:
  push:
    tags: ['v*']
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: dist
```

`anygarden-sdk/.github/workflows/release-pypi.yml`은 동일한 구조이며, 내부 패키지 이름만 다르다. 두 저장소는 독립적으로 버전 번호를 올린다 — 꼭 같은 버전을 유지할 필요는 없지만, `protocol-compat` 테스트가 깨지면 해당 번호로 릴리즈되지 않는다.

## 8.8 로드맵

> **MVP 기간: 4-5주** (서버 본체 Week 1~3 + Machine 계층 Week 4~5). [README](README.md) 및 [02-rationale.md](02-rationale.md) ADR-006의 "MVP 4-5주"와 일치. Week 4~5는 §8.10.5와 [10-machine-scheduler.md §10.13 구현 체크리스트](10-machine-scheduler.md)를 단일 정본으로 참조한다.

### 8.8.1 Week 1: 서버 뼈대
- FastAPI + uvicorn 앱 스켈레톤
- SQLAlchemy 모델 (7개 엔티티) + Alembic 마이그레이션
- JWT + API Token 인증
- WebSocket handler + ConnectionManager
- 단위 테스트 40개

### 8.8.2 Week 2: 서버 완성 + Python SDK
- Room CRUD + 메시지 send/receive
- Last-Seq 재연결 복구
- `ChatClient` 구현
- 첫 엔진 통합 (Claude Code + 일반 OpenAI API)
- 통합 테스트 20개

### 8.8.3 Week 3: 나머지 엔진 + 패키징
- Codex, OpenHands, Deep Agents 통합
- `anygarden-server`, `anygarden-agent`, `anygarden-client` CLI 완성
- PyPI 패키징 (`pyproject.toml`)
- Prometheus 지표 5개 (Machine 추가 2종은 Week 5에서)
- 문서 + 첫 릴리즈 v0.1.0 (채팅 단독)

### 8.8.4 Week 4: Machine Daemon 패키지 (`anygarden-machine`)
- `anygarden_machine/detector.py` (6종 엔진 감지: binary 3 + python 1 + env 2)
- `anygarden_machine/spawner.py` (subprocess, chmod 600 프로필, env 토큰)
- `anygarden_machine/supervisor.py` (watchdog)
- `anygarden_machine/daemon.py` (WS 재연결 + heartbeat 30s)
- `anygarden_machine/cli.py` (`register` / `run` / `status` / `install-systemd-unit`)
- systemd user unit 템플릿 + 에이전트 프로필 YAML 로더
- 상세 항목: [10-machine-scheduler.md §10.13](10-machine-scheduler.md)

### 8.8.5 Week 5: 서버 측 스케줄러 + Machine 엔드포인트
- `anygarden/scheduler/{placement,lifecycle,machine_bus}.py`
- `/ws/machines/{id}` 핸들러 + `auth/machine_token.py`
- `anygarden/api/v1/machines.py` + `anygarden/api/v1/agents.py` 선언적 생성 REST
- Prometheus 지표 2종 추가 (`anygarden_machines_online`, `anygarden_agents_by_state`)
- E2E: `register → run → POST /agents → 실제 spawn → 강제 kill → 자동 재시작`
- Locust 부하 테스트로 SLO 검증
- 문서 + 릴리즈 v0.2.0 (Machine 포함)

### 8.8.6 Phase 2 (Week 6-7): TypeScript SDK
- `@anygarden/sdk` npm 패키지
- Claude Code TS + Codex 통합
- npx 실행 (`npx @anygarden/agent`)
- 타입 생성 (서버 Pydantic → TS)

### 8.8.7 Phase 3 (Week 8+, 선택): 바이너리 배포
- PyInstaller 빌드 스크립트
- GitHub Actions 멀티 플랫폼 빌드
- `get.anygarden.io` 설치 스크립트
- 자동 업데이트 메커니즘 (선택)

## 8.9 요약 체크리스트

**서버 구현 완료의 정의**:

- [ ] `uvx anygarden-server`가 5초 이내에 "listening on ..." 출력
- [ ] 첫 실행 시 `~/.anygarden/` 디렉토리 자동 생성
- [ ] SQLite DB 자동 생성 + 마이그레이션 자동 적용
- [ ] JWT secret 자동 생성
- [ ] WebSocket 클라이언트가 `?since_seq=N`으로 재연결 복구 가능
- [ ] 4종 엔진 통합 테스트 1개씩 통과
- [ ] Prometheus `/metrics` 엔드포인트 응답
- [ ] 구조화 로그 `--log-format json` 동작
- [ ] `pytest` 전체 통과 (단위 + 통합)
- [ ] `ruff check .` + `mypy` 에러 0
- [ ] `anygarden-server --help` / `anygarden-agent --help` / `anygarden-machine --help` 깔끔한 출력
- [ ] systemd unit 템플릿 문서 존재
- [ ] README 빠른 시작 3분 이내 따라할 수 있음
- [ ] `uvx anygarden-machine register` 가 토큰 발급 + `~/.anygarden/machine.token` 저장
- [ ] `uvx anygarden-machine run` 가 서버에 online 상태로 등록됨
- [ ] `POST /api/v1/agents` 에 대해 Daemon이 실제로 subprocess spawn
- [ ] Agent 강제 kill 시 Daemon이 `agent_crashed` 보고 후 자동 재시작 (기본 정책)
- [ ] Machine drain 시 새 spawn 거부 + 기존 Agent 유지

이 체크리스트가 모두 ✅가 되면 **v0.1.0 릴리즈 가능**이다. 그 이후는 피드백에 따라 점진 개선.

---

## 8.10 Machine Daemon 패키지 (§10 참조)

§10에서 도입된 `anygarden-machine` 패키지는 **별도 저장소**이며 서버와 독립적으로 릴리즈된다.

### 8.10.1 패키지 구조 요약

```
anygarden-machine/                 # 3번째 독립 PyPI 패키지
├── pyproject.toml              # name="anygarden-machine", import=anygarden_machine
├── anygarden_machine/
│   ├── cli.py                  # register / run / status / install-systemd-unit
│   ├── daemon.py               # WS 메인 루프 + heartbeat
│   ├── detector.py             # 6종 엔진 자동 감지
│   ├── spawner.py              # subprocess 관리 (환경변수 토큰 전달)
│   ├── supervisor.py           # 자식 프로세스 watchdog
│   ├── config.py               # ~/.anygarden/machine.toml + .token 분리
│   └── protocol/frames.py      # Machine↔Server 프레임 Pydantic 모델
└── tests/
```

**LOC**: ~410 (Daemon 전체)

**의존성 9개**: websockets, httpx, pydantic, pydantic-settings, click, structlog, psutil, pyyaml, argon2-cffi

### 8.10.2 설치 및 실행

```bash
# 등록 (1회)
uvx anygarden-machine register --server https://anygarden.example.com --name dev-box-1

# foreground 실행
uvx anygarden-machine run

# systemd user unit 설치
uvx anygarden-machine install-systemd-unit
systemctl --user enable --now anygarden-machine
loginctl enable-linger $USER    # 로그아웃 후에도 유지
```

### 8.10.3 systemd user unit 템플릿

`install-systemd-unit` 명령이 자동 생성하는 파일:

```ini
# ~/.config/systemd/user/anygarden-machine.service
# 정본은 §10.10. 하드닝 옵션(ProtectSystem / ReadWritePaths / ProtectHome)은
# Daemon이 subprocess를 spawn하되 시스템 경로에 쓰기할 필요가 없다는 원칙을 반영.
[Unit]
Description=Anygarden Machine Daemon
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/uvx anygarden-machine run
Restart=on-failure
RestartSec=10
MemoryMax=256M

# 로그
StandardOutput=append:%h/.anygarden/logs/machine.log
StandardError=append:%h/.anygarden/logs/machine-err.log

# 보안 하드닝 (§10.10과 동일)
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=%h/.anygarden /tmp
ProtectHome=false

[Install]
WantedBy=default.target
```

### 8.10.4 배포 시나리오 C+ (Scheduled 모드 다중 머신)

§8.4의 시나리오 A/B/C에 이어 "다중 머신 + 선언적 운영"을 위한 Scheduled 모드:

```bash
# [서버 호스트]
$ uvx anygarden-server &

# [각 Machine마다]
$ uvx anygarden-machine register --server https://anygarden.example.com --name alice-laptop
$ uvx anygarden-machine install-systemd-unit
$ systemctl --user enable --now anygarden-machine

# [다른 Machine]
$ uvx anygarden-machine register --server https://anygarden.example.com --name gpu-box-1
$ systemctl --user enable --now anygarden-machine

# [admin 머신에서 선언적 생성]
$ uvx anygarden-client agent create --engine claude-code --name PM --room sprint-42
# → 스케줄러가 alice-laptop 또는 gpu-box-1 중 하나 선택 → 자동 spawn
```

이 시나리오에서는 admin이 특정 머신에 SSH 접속할 필요가 없다. "claude-code가 있는 머신 중 하나"만 알면 된다.

### 8.10.5 로드맵 정합성

Machine 계층의 주간 단계는 [§8.8 로드맵](#88-로드맵)에 통합되어 있다 (Week 4~5). 세부 구현 체크리스트의 **정본**은 [10-machine-scheduler.md §10.13](10-machine-scheduler.md)이며, 여기서는 중복 나열하지 않는다.

**MVP 기간**: 기존 채팅-only 2-3주 → **4-5주** (Machine 계층 2주 추가). README "4-5주 (서버 + Python SDK + Machine Daemon)" 및 ADR-006과 일치.

자세한 Daemon 구현, 프로토콜 프레임, 스케줄러 알고리즘은 [10-machine-scheduler.md](10-machine-scheduler.md) 참조.
