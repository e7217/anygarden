"""Benchmark scenarios: steps plus machine-checkable hard gates.

Each scenario function drives one run on a fresh fixture and records gates
in a ``Gates`` log. It returns facts the ledger needs (``claimed_done``:
whether the system reported the work as done). A run passes only when every
expected hard gate passed.

Two kinds of scenarios exist:

- **Protocol scenarios** (``QA-*``, ``GT-*``) check one product behavior
  in one topology, ported from the live QA plans in ``docs/e2e`` (local).
- **Deliverable scenarios** are data (``benchmarks/suite.json`` or a
  hold-out directory): a request, the answers a user would give, and the
  facts the final result must and must not contain. They run in both the
  ``solo`` and ``team`` topology with the same gates, which is what makes
  the team-vs-solo comparison meaningful.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .client import Api, Gates, items, poll, send_user
from .receiver import MockReceiver
from .topology import Fixture

TERMINAL_TURN_STATES = frozenset({"completed", "cancelled", "failed"})
WAIT = 15 * 60
SHORT = 10 * 60
SETTLE = 45


@dataclass
class Ctx:
    api: Api
    receiver: MockReceiver | None = None
    wait: float = WAIT
    short: float = SHORT
    settle: float = SETTLE


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    modes: tuple[str, ...]
    requirements: tuple[str, ...]
    gates: int
    run: Callable[[Ctx, Fixture, Gates, int], Awaitable[dict]]
    needs_receiver: bool = False
    spec: dict = field(default_factory=dict)


# ── shared probes ───────────────────────────────────────────────────────


async def messages(api: Api, room_id: str) -> list[dict]:
    return items(await api(f"/api/v1/rooms/{room_id}/messages", params={"limit": 200}))


async def room_tasks(api: Api, room_id: str) -> list[dict]:
    return items(await api(f"/api/v1/rooms/{room_id}/tasks"))


async def agent_turns(api: Api, agent_id: str) -> list[dict]:
    return items(await api("/api/v1/turns", params={"agent_id": agent_id, "limit": 500}))


async def executions(api: Api, room_id: str) -> list[dict]:
    return items(await api(f"/api/v1/rooms/{room_id}/executions"))


async def execution_requests(api: Api, room_id: str) -> list[dict]:
    return items(await api(f"/api/v1/rooms/{room_id}/execution-requests"))


async def approvals(api: Api, room_id: str) -> list[dict]:
    return items(await api(f"/api/v1/rooms/{room_id}/execution-approvals"))


def _eid(execution: dict) -> str:
    return execution.get("id") or execution["execution_id"]


async def execution_tasks(api: Api, fx: Fixture, execution_id: str) -> list[dict]:
    out = []
    for key in ("dev", "release"):
        for task in await room_tasks(api, fx.rooms[key]["id"]):
            if task.get("execution_id") == execution_id:
                out.append({**task, "_room": key})
    return out


def agent_replies(msgs: list[dict], participant_id: str, after_seq: int = 0) -> list[dict]:
    return [
        m
        for m in msgs
        if m.get("participant_id") == participant_id and (m.get("seq") or 0) > after_seq
    ]


async def answer_pending(api: Api, room_id: str, answer: str, seen: set[str], g: Gates) -> None:
    for req in await execution_requests(api, room_id):
        if req.get("status") == "pending" and req["id"] not in seen:
            seen.add(req["id"])
            await api(f"/api/v1/execution-requests/{req['id']}/answer", "POST", {"answer": answer})
            g.info("auto-answered execution question", (req.get("question") or "")[:300])


# ── S0: stack gate ──────────────────────────────────────────────────────


async def s0(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    trigger = await send_user(ctx.api, fx.entry, "안녕! 한 문장으로 인사만 해줘.")
    responder = fx.participants.get("lead") or fx.participants["solo"]

    async def replied():
        return agent_replies(await messages(ctx.api, fx.entry), responder, trigger.get("seq") or 0)

    await g.wait("S0 entry agent replies", replied, 300)
    return {}


# ── QA-03 담당자 위임 ─────────────────────────────────────────────────


async def qa03(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api = ctx.api
    await send_user(
        api,
        fx.entry,
        "로그인할 때 이메일 대소문자를 구분해서 같은 사람이 두 계정을 만들 수 있는 버그가 있어. "
        "개발 담당에게 원인 조사와 수정 방안 정리를 맡기고, 결과를 이 방에서 알려줘.",
    )
    found = await g.wait("G1 execution started", lambda: executions(api, fx.entry), ctx.wait)
    if not found:
        return {}
    eid = _eid(found[0])
    tasks = await g.wait("G2 delegated sub-room task", lambda: execution_tasks(api, fx, eid), ctx.wait)
    if not tasks:
        return {}
    task = tasks[0]
    g.check("G3 delegated spec carries the request", "대소문자" in (task.get("spec") or ""))
    assignee = fx.agents[task["_room"]]["id"]

    async def woken():
        return [t for t in await agent_turns(api, assignee) if t.get("task_id") == task["id"]]

    await g.wait("G4 assignee woken on the task", woken, 300)
    return {}


# ── QA-06 추가 정보 요청 (2026-10-07 redesign) ─────────────────────────

QA06_CHANGES = (
    "1) 로그인 시 이메일 대소문자를 구분하지 않도록 수정, "
    "2) 프로젝트 목록과 삭제 권한을 생성자·멤버·관리자로 제한"
)


async def qa06(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api, ops = ctx.api, fx.entry
    await send_user(
        api,
        ops,
        f"v2.4 릴리스 노트를 개발 담당에게 맡겨서 완성해줘. 쓸 변경 사항은 이것뿐이야: {QA06_CHANGES}. "
        "릴리스 날짜는 문서에 반드시 들어가야 하는데 아직 정하지 않았어. 날짜는 절대 추측하지 말고, "
        "담당자가 나에게 날짜를 질문하게 해줘.",
    )
    await g.wait("G1 execution started", lambda: executions(api, ops), ctx.wait)

    async def pending():
        return [r for r in await execution_requests(api, ops) if r.get("status") == "pending"]

    reqs = await g.wait("G2 pending question", pending, ctx.wait)
    if not reqs:
        return {}
    date_q = next(
        (r for r in reqs if any(w in (r.get("question") or "") for w in ("날짜", "date", "출시일"))),
        None,
    )
    g.check("G2b question asks for the date", date_q is not None, reqs[0].get("question"))
    req = date_q or reqs[0]
    inbox = items(await api("/api/v1/inbox"))
    g.check("G3 inbox lists the question", f"question:{req['id']}" in [i.get("id") for i in inbox])
    before = {t["request_id"] for t in await agent_turns(api, req["requester_agent_id"])}
    answer = "릴리스 날짜는 2026-10-20이야."
    await api(f"/api/v1/execution-requests/{req['id']}/answer", "POST", {"answer": answer})
    seen = {req["id"]}

    async def task_of_request() -> dict | None:
        room = next(k for k in ("dev", "release") if fx.rooms[k]["id"] == req["task_room_id"])
        return next(
            (t for t in await room_tasks(api, fx.rooms[room]["id"]) if t["id"] == req["task_id"]),
            None,
        )

    async def answered():
        rows = [r for r in await execution_requests(api, ops) if r["id"] == req["id"]]
        task = await task_of_request() if rows and rows[0]["status"] == "answered" else None
        return task if task and "2026-10-20" in (task.get("spec") or "") else None

    await g.wait("G4 answered and carried into the spec", answered, 120)

    async def resumed():
        return [
            t
            for t in await agent_turns(api, req["requester_agent_id"])
            if t["request_id"] not in before and t.get("task_id") == req["task_id"]
        ]

    await g.wait("G5 assignee resumed on the task", resumed, 300)

    async def done():
        await answer_pending(api, ops, QA06_CHANGES, seen, g)
        task = await task_of_request()
        return task if task and task.get("status") == "done" else None

    final = await g.wait("G6 task done", done, ctx.wait)
    claimed = bool(final)
    if final:
        g.check("G6b result carries the answered date", "2026-10-20" in (final.get("result_markdown") or ""))
    else:
        g.check("G6b result carries the answered date", False)
    return {"claimed_done": claimed}


# ── QA-08 사용자 승인 ─────────────────────────────────────────────────

QA08_FACTS = (
    "제품 Anygarden v2.4, 출시일 2026-10-20. 변경 1) 로그인 시 이메일 대소문자를 구분하지 않도록 수정. "
    "변경 2) 프로젝트 목록과 삭제 권한을 생성자·멤버·관리자로 제한. 알려진 제한사항 없음. "
    "이 사실만으로 작성하고 더 이상의 자료는 없어."
)


async def qa08(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api, ops, receiver = ctx.api, fx.entry, ctx.receiver
    assert receiver is not None
    decision = "reject" if n % 3 == 2 else "approve"
    g.info("decision", decision)
    await send_user(
        api,
        ops,
        "개발 담당에게 v2.4 릴리스 노트를 작성하게 하고, 완성되면 출시 담당이 그 문서를 mock-portal에 "
        "제출하게 해줘. 외부 제출(managed_submission)은 반드시 내 승인을 받은 뒤에만 해.\n"
        f"릴리스 노트에 쓸 사실(이것만 사용): {QA08_FACTS}",
    )
    seen: set[str] = set()

    async def pending():
        await answer_pending(api, ops, QA08_FACTS, seen, g)
        return [
            a
            for a in await approvals(api, ops)
            if a.get("status") == "pending" and a.get("target_alias") == "mock-portal"
        ]

    found = await g.wait("G1 pending approval for mock-portal", pending, 25 * 60)
    if not found:
        return {}
    approval = found[0]

    async def mine() -> list[dict]:
        return receiver.attempts_for({a["id"] for a in await approvals(api, ops)})

    early = await mine()
    await asyncio.sleep(60)
    g.check("G2 nothing sent before the decision", not early and not await mine())
    await api(f"/api/v1/execution-approvals/{approval['id']}/decision", "POST", {"decision": decision})
    if decision == "approve":

        async def delivered():
            rows = receiver.receipts_for(approval["id"])
            return rows if len(rows) == 1 else None

        await g.wait("G3 sent exactly once after approval", delivered, ctx.wait)
    else:
        await asyncio.sleep(180)
        g.check("G3 nothing sent after rejection", not await mine())
    return {}


# ── GT-01..03 일반 방 작업 (#806, #808) ─────────────────────────────────


async def _trigger_task(api: Api, fx: Fixture, trigger_id: str) -> dict | None:
    return next(
        (t for t in await room_tasks(api, fx.entry) if t.get("source_message_id") == trigger_id),
        None,
    )


async def gt01(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api, pid = ctx.api, fx.participants["solo"]
    trigger = await send_user(
        api,
        fx.entry,
        "작업 폴더에 notes/standup.md 파일을 만들어서 오늘 스탠드업 메모를 정리해 저장해줘. "
        "참석자: 민수, 지영. 결정사항: 금요일 배포, QA는 지영 담당.",
    )
    task = await g.wait("G1a task promoted from the trigger", lambda: _trigger_task(api, fx, trigger["id"]), ctx.short)
    if not task:
        return {}
    g.check("G1b assignee is the agent", task.get("assignee_participant_id") == pid)
    bound = [t for t in await agent_turns(api, fx.agents["solo"]["id"]) if t.get("task_id") == task["id"]]
    g.check("G1c a turn is bound to the task", bool(bound))

    async def reported():
        t = await _trigger_task(api, fx, trigger["id"])
        if t and t["status"] != "in_progress" and (t["status"] == "done" or t.get("error")):
            return t
        return None

    final = await g.wait("G1d agent reported a terminal status", reported, ctx.wait)
    g.info("final status", (final or {}).get("status"))
    return {"claimed_done": bool(final and final["status"] == "done")}


async def gt02(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api = ctx.api
    trigger = await send_user(api, fx.entry, "HTTP 상태 코드 404와 410의 차이를 두 문장으로 설명해줘.")

    async def replied():
        return agent_replies(await messages(api, fx.entry), fx.participants["solo"], trigger.get("seq") or 0)

    if await g.wait("G2a agent replied", replied, ctx.short):
        await asyncio.sleep(60)
        tasks = await room_tasks(api, fx.entry)
        g.check("G2b a Q&A creates no task", not tasks, [t["id"] for t in tasks])
    return {}


async def gt03(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
    api, agent_id = ctx.api, fx.agents["solo"]["id"]
    trigger = await send_user(
        api,
        fx.entry,
        "다음 주 팀 회식 공지문을 작성해서 작업 폴더의 notes/dinner.md에 저장해줘. 장소는 '을지로 한옥집'이야. "
        "날짜는 아직 말하지 않았어. 날짜는 공지에 반드시 들어가야 하니 추측하지 말고 나에게 물어봐.",
    )
    task = await g.wait("G3a task promoted from the trigger", lambda: _trigger_task(api, fx, trigger["id"]), ctx.short)
    if not task:
        return {}

    async def asked():
        q = [
            m
            for m in await messages(api, fx.entry)
            if (m.get("metadata") or {}).get("system_origin") == "task_input_request"
            and m.get("root_message_id") == trigger["id"]
        ]
        t = await _trigger_task(api, fx, trigger["id"])
        return (q, t) if q and t and t["status"] == "blocked" else None

    if not await g.wait("G3b question in the thread and task blocked", asked, ctx.short):
        return {}
    before = {t["request_id"] for t in await agent_turns(api, agent_id)}
    await send_user(api, fx.entry, "10월 16일 목요일 저녁 7시야.", thread_root_id=trigger["id"])

    async def new_turns():
        return [t for t in await agent_turns(api, agent_id) if t["request_id"] not in before]

    await poll(new_turns, 120)  # pacing only; G3c judges the turns
    await asyncio.sleep(20)
    fresh = await new_turns()
    bound = [t for t in fresh if t.get("task_id") == task["id"]]
    g.check(
        "G3c exactly one resumed turn, none started by the reply",
        len(bound) == 1 and len(fresh) == 1,
        [(t["request_id"], t.get("task_id")) for t in fresh],
    )
    t = await _trigger_task(api, fx, trigger["id"])
    g.check("G3d answer carried into the task spec", "10월 16일" in ((t or {}).get("spec") or ""))

    async def done():
        t = await _trigger_task(api, fx, trigger["id"])
        return t if t and t["status"] == "done" else None

    final = await g.wait("G3e task done", done, ctx.wait)
    return {"claimed_done": bool(final)}


# ── deliverable scenarios (data-driven, solo and team) ──────────────────


async def _direct_answer(api: Api, fx: Fixture, key: str, trigger: dict) -> tuple[bool, str]:
    """An answer given in the entry room once the agent's turns are over."""
    replies = agent_replies(await messages(api, fx.entry), fx.participants[key], trigger.get("seq") or 0)
    turns = await agent_turns(api, fx.agents[key]["id"])
    idle = all(t["state"] in TERMINAL_TURN_STATES for t in turns)
    if not replies or not turns or not idle:
        return False, ""
    return True, "\n".join(m["content"] for m in replies)


async def _deliverable_text(api: Api, fx: Fixture, trigger: dict) -> tuple[bool, str]:
    """(claimed done, text of the final result) for either topology."""
    if fx.mode == "solo":
        # A request that needs no tools is answered directly and never
        # becomes a task (#806); that is a finished answer once the agent's
        # turn is over. A promoted request is finished when its task is done.
        task = await _trigger_task(api, fx, trigger["id"])
        replies = agent_replies(await messages(api, fx.entry), fx.participants["solo"], trigger.get("seq") or 0)
        if task is None:
            return await _direct_answer(api, fx, "solo", trigger)
        if task["status"] != "done":
            return False, ""
        return True, "\n".join([task.get("result_markdown") or "", *(m["content"] for m in replies)])
    runs = await executions(api, fx.entry)
    if not runs:
        # The lead may answer a request it can finish alone without opening
        # an execution (#812); that answer counts once its turns are over.
        return await _direct_answer(api, fx, "lead", trigger)
    done = [e for e in runs if e.get("status") == "completed"]
    if not done:
        return False, ""
    report_id = done[0].get("final_report_message_id")
    texts = [m["content"] for m in await messages(api, fx.entry) if m["id"] == report_id]
    for task in await execution_tasks(api, fx, _eid(done[0])):
        texts.append(task.get("result_markdown") or "")
    return True, "\n".join(texts)


def deliverable(spec: dict) -> Scenario:
    """Build a solo/team scenario from a declarative deliverable spec."""

    async def run(ctx: Ctx, fx: Fixture, g: Gates, n: int) -> dict:
        api = ctx.api
        trigger = await send_user(api, fx.entry, spec["request"])
        answer = spec.get("answer", "")
        target = (spec.get("submission") or {}).get("target")
        seen: set[str] = set()
        result: dict[str, Any] = {}

        async def finished():
            if answer:
                await answer_pending(api, fx.entry, answer, seen, g)
                await _answer_thread_questions(api, fx, trigger, answer, seen, g)
            if target:
                await _approve_pending(api, fx, target, seen, g)
            claimed, text = await _deliverable_text(api, fx, trigger)
            result.update(claimed=claimed, text=text)
            return claimed

        claimed = bool(await g.wait("D1 work reported done", finished, ctx.wait))
        if not claimed:
            g.info("unfinished tasks", await _task_states(api, fx))
        text = result.get("text", "")
        missing = [s for s in spec.get("must_include", []) if s not in text]
        invented = [s for s in spec.get("must_not_include", []) if s in text]
        g.check("D2 result contains every required fact", claimed and not missing, missing)
        g.check("D3 result invents none of the forbidden facts", claimed and not invented, invented)
        if target:
            # The deliverable must actually reach the external target, once,
            # through an approved managed action — a reply saying "submitted"
            # is not a submission.
            sent = await _submitted_texts(api, fx, ctx.receiver)
            facts = spec.get("must_include", [])
            g.check(
                "D4 submitted exactly once after approval with the required facts",
                len(sent) == 1 and all(f in sent[0] for f in facts),
                {"submissions": len(sent)},
            )
        return {"claimed_done": claimed}

    target = (spec.get("submission") or {}).get("target")
    return Scenario(
        id=spec["id"],
        title=spec["title"],
        modes=tuple(spec.get("modes", ("solo", "team"))),
        requirements=tuple(spec.get("requirements", ())),
        gates=4 if target else 3,
        run=run,
        needs_receiver=bool(target),
        spec=spec,
    )


async def _approve_pending(api: Api, fx: Fixture, target: str, seen: set[str], g: Gates) -> None:
    """Act as the user approving the external action the work asks for."""
    for approval in await approvals(api, fx.entry):
        if (
            approval.get("status") == "pending"
            and approval.get("target_alias") == target
            and approval["id"] not in seen
        ):
            seen.add(approval["id"])
            await api(
                f"/api/v1/execution-approvals/{approval['id']}/decision",
                "POST",
                {"decision": "approve"},
            )
            g.info("approved external action", approval["id"])


async def _submitted_texts(api: Api, fx: Fixture, receiver: MockReceiver | None) -> list[str]:
    """Texts the mock target received for this run's approvals."""
    if receiver is None:
        return []
    texts: list[str] = []
    for approval in await approvals(api, fx.entry):
        texts += [r["text"] for r in receiver.receipts_for(approval["id"])]
    return texts


async def _task_states(api: Api, fx: Fixture) -> list[dict]:
    """Why a run stalled: every task's status, role and recovery reason."""
    out = []
    for key, room in fx.rooms.items():
        for task in await room_tasks(api, room["id"]):
            recovery = task.get("recovery") or {}
            out.append(
                {
                    "room": key,
                    "status": task.get("status"),
                    "role": task.get("role"),
                    "reason": recovery.get("reason_code") or task.get("error"),
                    "next_action": recovery.get("next_action"),
                }
            )
    return out


async def _answer_thread_questions(
    api: Api, fx: Fixture, trigger: dict, answer: str, seen: set[str], g: Gates
) -> None:
    for m in await messages(api, fx.entry):
        meta = m.get("metadata") or {}
        if (
            meta.get("system_origin") == "task_input_request"
            and m.get("root_message_id") == trigger["id"]
            and m["id"] not in seen
        ):
            seen.add(m["id"])
            await send_user(api, fx.entry, answer, thread_root_id=trigger["id"])
            g.info("answered thread question", m["content"][:300])


PROTOCOL_SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in (
        Scenario("S0", "스택 게이트", ("solo", "team"), (), 1, s0),
        Scenario("QA-03", "담당자 위임", ("team",), ("EXE-11", "EXE-12"), 4, qa03),
        Scenario("QA-06", "추가 정보 요청", ("team",), ("EXE-09", "EXE-18", "INB-01"), 8, qa06),
        Scenario("QA-08", "사용자 승인", ("team",), ("EXE-06",), 3, qa08, needs_receiver=True),
        Scenario("GT-01", "일반 방 작업 승격", ("solo",), (), 4, gt01),
        Scenario("GT-02", "단순 질답은 작업 아님", ("solo",), (), 2, gt02),
        Scenario("GT-03", "질문→스레드 답변→재개", ("solo",), (), 5, gt03),
    )
}
