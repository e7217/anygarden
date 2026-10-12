"""Fresh per-run fixtures: a general room (solo) or an operating room team.

Every scenario run seeds its own project through the public API, so runs
never share rooms, agents or history, and the collaboration metrics can be
scoped to exactly one run with ``project_id``.

Team topology (#802): the operating room has sub-rooms ``개발`` and
``출시``. The lead joins all three rooms and is the operating room's
representative, which makes it the operating lead; each sub-room has one
worker as its representative.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .client import Api

COMMON_RULES = (
    "이 환경은 분리된 벤치마크용 테스트 프로젝트다. 한국어로 응답한다. "
    "제공되지 않은 사실이나 작업 완료를 만들어 내지 않는다. 외부 제출, push, merge, "
    "실제 배포는 사용자 승인 없이 하지 않는다. 도구 오류는 숨기지 말고 알린다."
)
LEAD_RULES = (
    "너는 이 프로젝트의 운영실 총괄이다. 담당 서브룸의 권한·분업이 필요한 요청은 프로젝트 실행을 "
    "시작해 목표, 입력, 산출물, 완료 기준을 전달해 위임하고, 담당자의 실제 결과만 취합해 보고한다. "
    "혼자 끝낼 수 있는 요청은 직접 처리한다. 외부 제출은 managed_submission 권한을 선언하고 "
    "사용자 승인을 거친다."
)
WORKER_RULES = {
    "dev": "요청받은 조사·문서 작성을 실제로 수행하고 결과를 산출물로 남긴다. "
    "필요한 정보가 없으면 추측하지 말고 사용자에게 질문한다.",
    "release": "승인된 산출물만 지정된 대상에 제출한다. 승인 없이는 제출하지 않는다.",
}
SOLO_RULES = "너는 이 방의 유일한 담당자다. 요청을 직접 끝까지 수행한다."


@dataclass(frozen=True)
class EngineConfig:
    engine: str = "codex-cli"
    model: str | None = None
    permission_level: str = "standard"
    turn_timeout_sec: int = 300

    def agent_body(self, name: str, rooms: list[str], description: str, rules: str) -> dict:
        body: dict[str, Any] = {
            "engine": self.engine,
            "name": name,
            "rooms": rooms,
            "permission_level": self.permission_level,
            "turn_timeout_sec": self.turn_timeout_sec,
            "description": description[:200],
            "agents_md": f"{COMMON_RULES}\n{rules}",
        }
        if self.model:
            body["model"] = self.model
        return body


@dataclass
class Fixture:
    """Rooms and agents of one run. ``entry`` is where the user talks."""

    mode: str
    project: dict
    rooms: dict[str, dict]
    agents: dict[str, dict]
    participants: dict[str, str] = field(default_factory=dict)  # agent key -> entry-room pid

    @property
    def entry(self) -> str:
        return self.rooms["entry"]["id"]

    def agent_ids(self) -> list[str]:
        return [a["id"] for a in self.agents.values()]


async def _participant_ids(api: Api, fixture: Fixture) -> None:
    """Record each agent's participant id in the entry room."""
    detail = await api(f"/api/v1/rooms/{fixture.entry}")
    by_agent = {p.get("agent_id"): p["id"] for p in detail.get("participants", [])}
    for key, agent in fixture.agents.items():
        if agent["id"] in by_agent:
            fixture.participants[key] = by_agent[agent["id"]]


async def start_agents(api: Api, fixture: Fixture, timeout: float = 300) -> None:
    for agent_id in fixture.agent_ids():
        state = await api(f"/api/v1/agents/{agent_id}")
        if state.get("desired_state") != "running":
            await api(f"/api/v1/agents/{agent_id}/start", "POST")
    deadline = time.monotonic() + timeout
    states: list[str | None] = []
    while time.monotonic() < deadline:
        states = [
            (await api(f"/api/v1/agents/{agent_id}")).get("actual_state")
            for agent_id in fixture.agent_ids()
        ]
        if all(s == "running" for s in states):
            return
        await asyncio.sleep(5)
    raise RuntimeError(f"agents not running after {timeout}s: {states}")


async def seed_solo(api: Api, engine: EngineConfig, tag: str, start: bool = True) -> Fixture:
    project = await api("/api/v1/projects", "POST", {"name": f"bench {tag}"})
    room = await api("/api/v1/rooms", "POST", {"project_id": project["id"], "name": f"일반-{tag}"})
    agent = await api(
        "/api/v1/agents",
        "POST",
        engine.agent_body(f"solo-{tag}", [room["id"]], "일반 방 담당", SOLO_RULES),
    )
    fixture = Fixture("solo", project, {"entry": room}, {"solo": agent})
    await _participant_ids(api, fixture)
    if start:
        await start_agents(api, fixture)
    return fixture


async def seed_team(api: Api, engine: EngineConfig, tag: str, start: bool = True) -> Fixture:
    project = await api("/api/v1/projects", "POST", {"name": f"bench {tag}"})
    ops = await api("/api/v1/rooms", "POST", {"project_id": project["id"], "name": "운영실"})
    rooms = {"entry": ops}
    for key, name in (("dev", "개발"), ("release", "출시")):
        rooms[key] = await api(
            "/api/v1/rooms",
            "POST",
            {"project_id": project["id"], "name": name, "parent_room_id": ops["id"]},
        )
    sub_ids = f"서브룸: 개발={rooms['dev']['id']}, 출시={rooms['release']['id']}"
    agents = {
        "lead": await api(
            "/api/v1/agents",
            "POST",
            engine.agent_body(
                f"lead-{tag}",
                [r["id"] for r in rooms.values()],
                "운영실 총괄",
                f"{LEAD_RULES}\n{sub_ids}",
            ),
        )
    }
    await api(f"/api/v1/rooms/{ops['id']}/representative", "PUT", {"agent_id": agents["lead"]["id"]})
    for key in ("dev", "release"):
        agents[key] = await api(
            "/api/v1/agents",
            "POST",
            engine.agent_body(
                f"{key}-{tag}",
                [rooms[key]["id"]],
                WORKER_RULES[key],
                f"너는 {rooms[key]['name']} 담당이다. {WORKER_RULES[key]}",
            ),
        )
        await api(
            f"/api/v1/rooms/{rooms[key]['id']}/representative",
            "PUT",
            {"agent_id": agents[key]["id"]},
        )
    fixture = Fixture("team", project, rooms, agents)
    await _participant_ids(api, fixture)
    if start:
        await start_agents(api, fixture)
    return fixture


SEEDERS = {"solo": seed_solo, "team": seed_team}
