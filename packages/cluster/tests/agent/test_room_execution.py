"""Actual CLI registration → policy/supervisor → manager → fake child → lifecycle."""

import asyncio
import json
import os
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from anygarden.agent import secrets
from anygarden.agent.cli import _setup_engine
from anygarden.agent.client import ChatClient
from anygarden.agent.integrations._turn_timeout import resolve_turn_timeout
from anygarden.agent.runtime.execution.codex import CodexRuntime
from anygarden.agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden.agent.runtime.execution.endpoint import (
    DirectEndpoint,
    endpoint_environment,
)
from anygarden.agent.runtime.execution.launch import ExecutionLaunch
from anygarden.agent.runtime.execution.room import RoomCodexRuntime, RoomInvocation


@pytest.fixture
def endpoint_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"text":"local answer"}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    thread.join()
    server.server_close()


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess runtime")

SCRIPT = r"""
import sys,os,json,time,tomllib,urllib.request
from pathlib import Path
args=sys.argv[1:]; pi=os.environ['TEST_ENGINE']=='pi-cli'
if args==['--version']:
    print(os.environ.get('TEST_VERSION') or ('0.85.1' if pi else 'codex-cli 0.155.1'));sys.exit(0)
assert 'ANYGARDEN_TOKEN' not in os.environ
assert 'AG_ENGINE_ENDPOINT_KEY' not in os.environ
assert 'AG_ENGINE_ENDPOINT_CONFIG' not in os.environ
assert 'fake-only-token' not in str(args)
assert os.environ.get('ANYGARDEN_AGENT_TOKEN')=='self-mcp-fixture'
assert os.environ.get('STAGED_PROVIDER_KEY')=='staged-only-fixture'
prompt=sys.stdin.read()
with Path('calls.jsonl').open('a') as f:f.write(json.dumps({'argv':args,'prompt':prompt})+'\n')
# #805 — a definite failure, as an unsupported model reports it.
if Path('fail-once').exists():
    Path('fail-once').unlink()
    if not pi:
        print(json.dumps({'type':'thread.started','thread_id':'failed-session'}))
        print(json.dumps({'type':'turn.failed','error':{'message':'model is not supported'}}),flush=True)
    sys.exit(1)
def event(value):print(json.dumps(value),flush=True)
answer='local answer'
if os.environ.get('TEST_ENDPOINT'):
    if pi:
        provider=args[args.index('--provider')+1];model=args[args.index('--model')+1]
        entry=json.loads((Path(os.environ['PI_CODING_AGENT_DIR'])/'models.json').read_text())['providers'][provider]
        base=entry['baseUrl'];key=os.environ[entry['apiKey'][1:]]
        route='/responses' if entry['api']=='openai-responses' else '/chat/completions'
    else:
        config={}
        for i,v in enumerate(args):
            if v=='-c' and args[i+1].startswith(('model_provider=', 'model_providers.')):
                k,value=args[i+1].split('=',1);config[k]=tomllib.loads('value='+value)['value']
        base=config['model_providers.ag_direct.base_url'];route='/responses'
        model=args[args.index('-m')+1];key=os.environ[config['model_providers.ag_direct.env_key']]
    request=urllib.request.Request(base.rstrip('/')+route,json.dumps({'model':model}).encode(),{'Authorization':'Bearer '+key})
    with urllib.request.urlopen(request,timeout=2) as r:answer=json.load(r)['text']
if pi:
    event({'type':'session','id':'native-session'})
    if os.environ.get('TEST_PROGRESS'):
        event({'type':'message_start','message':{'role':'assistant'}})
    event({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':answer}],
        'usage':{'input':3,'output':1},'stopReason':'stop'}})
    event({'type':'turn_end'})
else:
    event({'type':'thread.started','thread_id':'native-session'})
    fail=os.environ.get('TEST_CODEX_FAIL')
    if fail:
        status=500 if fail=='after-effect' else 429
        msg=json.dumps({'type':'error','status':status,'error':{'type':'rate_limit_exceeded','message':'DO-NOT-PUBLISH'}})
        event({'type':'turn.started'})
        if fail=='after-effect':
            event({'type':'item.started','item':{'type':'command_execution','command':'DO-NOT-PUBLISH'}})
        event({'type':'error','message':msg})
        event({'type':'turn.failed','error':{'message':msg}})
        sys.exit(1)
    if os.environ.get('TEST_PROGRESS')=='trace':
        event({'type':'item.completed','item':{'type':'agent_message','text':'Checking the repo.'}})
    if os.environ.get('TEST_PROGRESS'):
        event({'type':'item.started','item':{'type':'command_execution','command':'DO-NOT-PUBLISH'}})
        event({'type':'item.completed','item':{'type':'command_execution','command':'DO-NOT-PUBLISH'}})
        event({'type':'item.started','item':{'type':'agent_message'}})
    event({'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':1}})
    event({'type':'item.started'})
Path('measured').touch()
mode=os.environ.get('TEST_OUTCOME','ok')
if mode in ('timeout','cancel'):time.sleep(30)
if mode=='failed':sys.exit(2)
if pi:event({'type':'agent_settled'})
else:Path(args[args.index('-o')+1]).write_text(answer)
"""


@pytest.fixture
def setup_room(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    executable = tmp_path / "fake-cli"
    executable.write_text(f"#!{sys.executable}\n" + SCRIPT)
    executable.chmod(0o700)
    monkeypatch.setattr(
        "anygarden.agent.integrations.room_execution.shutil.which",
        lambda _: str(executable),
    )
    # These room-supervisor tests use a fake engine and no cluster server.
    # The real self MCP handshake/ownership is covered by test_pi_self_tools.
    monkeypatch.setattr(
        "anygarden.agent.runtime.execution.pi_self_tools.prepare_pi_self_tools",
        AsyncMock(return_value=tmp_path / "self-tools-fixture.json"),
    )
    # #688 — Pi uses the machine-managed executable handed over by the spawner.
    monkeypatch.setenv("ANYGARDEN_PI_EXECUTABLE", str(executable))
    monkeypatch.delenv("ANYGARDEN_PI_USE_PATH", raising=False)
    monkeypatch.setenv("ANYGARDEN_TOKEN", "transport-must-not-inherit")
    monkeypatch.setenv("ANYGARDEN_AGENT_TOKEN", "self-mcp-fixture")
    monkeypatch.setenv("ANYGARDEN_AGENT_GENERATION", "7")
    monkeypatch.setenv("ANYGARDEN_AGENT_PERMISSION_LEVEL", "trusted")
    monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "2")
    secrets.set_secrets({"STAGED_PROVIDER_KEY": "staged-only-fixture"})
    yield tmp_path
    secrets.clear()


async def client_for(engine, monkeypatch, endpoint=None, model="model"):
    monkeypatch.setenv("TEST_ENGINE", engine)
    launch = ExecutionLaunch(
        engine,
        "local",
        model,
        7,
        endpoint,
        endpoint_environment(endpoint, "fake-only-token" if endpoint else None),
    )
    client = ChatClient(
        "ws://127.0.0.1:1", token="fixture", agent_name="agent", execution_launch=launch
    )
    client._agent_id = "agent-id"
    client.sendLifecycle = AsyncMock()
    client.send = AsyncMock()
    client.sendTyping = AsyncMock()
    await _setup_engine(client, engine, "agent", model, None)
    assert client.execution_launch_ready
    return client


def message(request="req", thread=None):
    return {
        "room_id": "room",
        "content": "@agent hello",
        "participant_id": "human",
        "root_message_id": thread,
        "metadata": {
            "request_id": request,
            "mentions": [{"type": "legacy", "name": "agent"}],
        },
        "sender_kind": "human",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("engine,stages", [
    ("codex-cli", ["preparing", "using_tool", "preparing", "writing"]),
    ("pi-cli", ["preparing", "writing", "preparing"]),
])
async def test_room_typing_reports_observed_stages_only(setup_room, monkeypatch, engine, stages):
    monkeypatch.setenv("TEST_PROGRESS", "1")
    client = await client_for(engine, monkeypatch)
    try:
        await client._message_handlers[0](message())
        sent = [call.args for call in client.sendTyping.call_args_list]
        observed = [args[2] for args in sent if len(args) == 3]
        transitions = [stage for index, stage in enumerate(observed)
                       if index == 0 or stage != observed[index - 1]]
        assert transitions == stages
        assert sent[-1] == ("room", False)
        assert "DO-NOT-PUBLISH" not in str(sent)
        assert client._execution_adapter.progress_stage("room") is None
    finally:
        await client._execution_adapter.stop()


@pytest.mark.asyncio
@pytest.mark.req("ENG-10")
@pytest.mark.req("ENG-11")
async def test_room_typing_and_reply_carry_activity_narration_and_trace(setup_room, monkeypatch):
    # #880 — the typing heartbeat names the work and the latest narration;
    # the reply carries the turn's trace. Raw commands never leave.
    monkeypatch.setenv("TEST_PROGRESS", "trace")
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message())
        details = [call.kwargs for call in client.sendTyping.call_args_list if call.kwargs]
        assert {"activity": "command", "activity_seq": 1, "narration": "Checking the repo."} in details
        assert {"narration": "Checking the repo."} in details
        metadata = client.send.call_args.kwargs["metadata"]
        duration_ms = metadata["execution_trace"].pop("duration_ms")
        assert type(duration_ms) is int and duration_ms >= 0
        assert metadata["execution_trace"] == {
            "v": 1,
            "steps": [{"narration": "Checking the repo.", "activities": {"commands": 1}}],
            "totals": {"commands": 1},
            "truncated": 0,
        }
        assert client.send.call_args.args[1] == "local answer"
        assert "DO-NOT-PUBLISH" not in str(client.sendTyping.call_args_list)
        assert "DO-NOT-PUBLISH" not in str(metadata)
        assert client._execution_adapter.progress_detail("room") == {}
    finally:
        await client._execution_adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex-cli", "pi-cli"])
@pytest.mark.parametrize("outcome", ["ok", "failed", "timeout", "cancel"])
async def test_room_lifecycle_measured_usage(setup_room, monkeypatch, engine, outcome):
    root = setup_room
    monkeypatch.setenv("TEST_OUTCOME", outcome)
    if outcome == "timeout":
        monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "0.25")
    client = await client_for(engine, monkeypatch)
    try:
        task = asyncio.create_task(client._message_handlers[0](message()))
        if outcome == "cancel":
            async with asyncio.timeout(3):
                while not (root / "workspace" / "measured").exists():
                    await asyncio.sleep(0.01)
            # Wait for receipt progress recorded by the production collector.
            manager = client._execution_adapter._manager
            async with asyncio.timeout(3):
                while not manager._store.db.execute(
                    "SELECT 1 FROM events WHERE kind='progress'"
                ).fetchone():
                    await asyncio.sleep(0.01)
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            assert outcome == "cancel"
        events = [
            c.kwargs
            for c in client.sendLifecycle.call_args_list
            if c.kwargs.get("event") == "engine_call_finished"
        ]
        assert len(events) == 1
        assert events[0]["outcome"] == ("cancelled" if outcome == "cancel" else outcome)
        assert events[0]["input_tokens"] == 3 and events[0]["output_tokens"] == 1
        assert events[0]["model"] == "model"
        if outcome == "ok":
            assert any(c.args[1] == "local answer" for c in client.send.call_args_list)
        calls = (root / "workspace" / "calls.jsonl").read_text().splitlines()
        assert len(calls) == 1  # no fresh retry on ambiguous failure
        assert not client._execution_adapter._manager._tasks
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,protocol",
    [
        ("codex-cli", "responses"),
        ("pi-cli", "responses"),
        ("pi-cli", "chat-completions"),
    ],
)
async def test_room_direct_endpoint_http(
    setup_room, monkeypatch, endpoint_server, engine, protocol
):
    url, requests = endpoint_server
    monkeypatch.setenv("TEST_ENDPOINT", "1")
    endpoint = DirectEndpoint("local", "model", url, protocol, "ref", 1)
    client = await client_for(engine, monkeypatch, endpoint)
    try:
        await client._message_handlers[0](message())
        assert requests == [
            (
                "/v1/"
                + ("responses" if protocol == "responses" else "chat/completions"),
                "Bearer fake-only-token",
                {"model": "model"},
            )
        ]
        assert client.send.call_args.args[1] == "local answer"
    finally:
        await client.close()
    for path in setup_room.rglob("*"):
        if path.is_file() and path.name != "fake-cli":
            assert b"fake-only-token" not in path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.req("ENG-06")
async def test_room_resume_restart_and_thread_scope(setup_room, monkeypatch):
    client = await client_for("codex-cli", monkeypatch)
    await client._message_handlers[0](message("one"))
    await client.close()
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message("two"))
        await client._message_handlers[0](message("three", "thread"))
        calls = [
            json.loads(x)["argv"]
            for x in (setup_room / "workspace" / "calls.jsonl").read_text().splitlines()
        ]
        assert "resume" not in calls[0]
        assert calls[1][0:3] == ["exec", "resume", "native-session"]
        assert "resume" not in calls[2]
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex-cli", "pi-cli"])
async def test_fresh_session_after_failed_first_turn_gets_full_context(
    setup_room, monkeypatch, engine
):
    """#805 — a failed first turn leaves no session to resume.

    The next turn starts a fresh engine session, so it must carry the
    system prompt again; only a turn that resumes may skip it.
    """
    workspace = setup_room / "workspace"
    client = await client_for(engine, monkeypatch)
    client._execution_adapter._system_prompt = "SYSTEM-FIXTURE"
    try:
        (workspace / "fail-once").touch()
        for request in ("one", "two", "three"):
            await client._message_handlers[0](message(request))
        calls = [
            json.loads(line)
            for line in (workspace / "calls.jsonl").read_text().splitlines()
        ]
        assert len(calls) == 3
        resumed = [
            "native-session" in call["argv"] for call in calls
        ]
        assert resumed == [False, False, True]
        assert ["SYSTEM-FIXTURE" in call["prompt"] for call in calls] == [
            True,
            True,
            False,
        ]
    finally:
        await client.close()


def test_local_codex_preserves_configuration_without_widening_remote(tmp_path):
    scope = SessionScope("node", "agent", "node", "room", None, "workspace", 0, 0)
    env = {
        "HOME": "/configured/home",
        "CODEX_HOME": "/configured/codex",
        "ANYGARDEN_AGENT_TOKEN": "mcp",
    }
    invocation = RoomInvocation(
        "one",
        scope,
        "hello",
        tmp_path,
        tmp_path,
        environment=env,
        permission_level="trusted",
    )
    invocation.validate()
    runtime = RoomCodexRuntime(Path("/fake/codex"))
    cmd = runtime.command(invocation, None, tmp_path / "out")
    assert "--ignore-user-config" not in cmd and "--ignore-rules" not in cmd
    assert "sandbox_mode=danger-full-access" in cmd
    assert runtime.environment(invocation) == env
    remote = Invocation(
        "one", scope, "hello", tmp_path, tmp_path, permission_level="trusted"
    )
    with pytest.raises(ValueError):
        remote.validate()
    remote = replace(remote, permission_level="standard")
    isolated = CodexRuntime(Path("/fake/codex"))
    assert "--ignore-user-config" in isolated.command(remote, None, tmp_path / "out")
    assert isolated.environment(remote)["CODEX_HOME"] == str(tmp_path)


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("standard", True), ("restricted", False), ("trusted", False)],
)
def test_room_codex_outbox_writable_only_under_workspace_write(tmp_path, tier, expected):
    # #858 — outbox is a symlink target outside the codex workspace.
    scope = SessionScope("node", "agent", "node", "room", None, "workspace", 0, 0)
    invocation = RoomInvocation(
        "one", scope, "hello", tmp_path, tmp_path, permission_level=tier
    )
    runtime = RoomCodexRuntime(Path("/fake/codex"))
    runtime.outbox_dir = tmp_path / "memory" / "outbox"
    cmd = runtime.command(invocation, None, tmp_path / "out")
    root = f'sandbox_workspace_write.writable_roots=["{runtime.outbox_dir}"]'
    assert (root in cmd) is expected
    assert cmd[-3:] == ["-o", str(tmp_path / "out"), "-"]


def test_pi_timeout_precedence(monkeypatch):
    monkeypatch.delenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", raising=False)
    monkeypatch.setenv("ANYGARDEN_AGENT_PI_TURN_TIMEOUT_SEC", "99")
    assert resolve_turn_timeout("pi") == 99
    monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "123")
    assert resolve_turn_timeout("pi") == 123


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_model,provider,compatible",
    [("model", "local", True), ("other", "local", False), ("model", "other", False)],
)
async def test_legacy_import_requires_matching_native_selection(
    setup_room, monkeypatch, legacy_model, provider, compatible
):
    from anygarden.agent.integrations.engine_session_store import save_sessions

    handle = "abcdef01-1234-1234-1234-123456789abc"
    codex_home = setup_room / "codex-home"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    native = sessions / f"rollout-{handle}.jsonl"
    native.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": handle,
                    "model_provider": provider,
                    "cwd": str(setup_room / "workspace"),
                },
            }
        )
        + "\n"
        + json.dumps({"type": "turn_context", "payload": {"model": legacy_model}})
        + "\n"
    )
    original = native.read_bytes()
    save_sessions(setup_room, {"room": handle})
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message())
        calls = [
            json.loads(x)["argv"]
            for x in (setup_room / "workspace" / "calls.jsonl").read_text().splitlines()
        ]
        assert "resume" not in calls[0]  # Unknown-origin legacy history is never imported.
        assert native.read_bytes() == original
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_initial_endpoint_never_imports_legacy_after_disable(
    setup_room, monkeypatch, endpoint_server
):
    from anygarden.agent.integrations.engine_session_store import save_sessions

    handle = "abcdef01-1234-1234-1234-123456789abc"
    codex_home = setup_room / "codex-home"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    (sessions / f"rollout-{handle}.jsonl").write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": handle,
                    "model_provider": "local",
                    "cwd": str(setup_room / "workspace"),
                },
            }
        )
        + "\n"
        + json.dumps({"type": "turn_context", "payload": {"model": "model"}})
        + "\n"
    )
    save_sessions(setup_room, {"room": handle})
    url, _ = endpoint_server
    monkeypatch.setenv("TEST_ENDPOINT", "1")
    endpoint = DirectEndpoint("local", "model", url, "responses", "ref", 1)
    client = await client_for("codex-cli", monkeypatch, endpoint)
    await client._message_handlers[0](message("direct"))
    await client.close()
    monkeypatch.delenv("TEST_ENDPOINT")
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message("disabled"))
        calls = [
            json.loads(x)["argv"]
            for x in (setup_room / "workspace" / "calls.jsonl").read_text().splitlines()
        ]
        assert len(calls) == 2
        assert all("resume" not in c for c in calls)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,expected", [("codex-cli", "gpt-6.1-sol"), ("pi-cli", None)]
)
async def test_omitted_model_keeps_only_codex_default(
    setup_room, monkeypatch, engine, expected
):
    client = await client_for(engine, monkeypatch, model=None)
    try:
        await client._message_handlers[0](message())
        assert client._execution_adapter._launch.model == expected
        call = json.loads((setup_room / "workspace" / "calls.jsonl").read_text())
        if expected:
            assert call["argv"][call["argv"].index("-m") + 1] == expected
        else:
            assert "--model" not in call["argv"]
        finished = [
            c.kwargs
            for c in client.sendLifecycle.call_args_list
            if c.kwargs.get("event") == "engine_call_finished"
        ]
        assert finished[0]["model"] == expected
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex-cli", "pi-cli"])
async def test_manager_cancel_finishes_turn_without_cancelling_room_handler(
    setup_room, monkeypatch, engine
):
    monkeypatch.setenv("TEST_OUTCOME", "cancel")
    client = await client_for(engine, monkeypatch)
    try:
        task = asyncio.create_task(client._message_handlers[0](message("cancel-one")))
        manager = client._execution_adapter._manager
        async with asyncio.timeout(3):
            while not manager._store.db.execute(
                "SELECT 1 FROM events WHERE kind='progress'"
            ).fetchone():
                await asyncio.sleep(0.01)
        execution_id = next(iter(manager._tasks))
        await manager.cancel(execution_id)
        await task  # domain cancellation must not escape to the WS receiver
        events = [c.kwargs for c in client.sendLifecycle.call_args_list]
        assert events[-1]["event"] == "handler_finished"
        assert events[-1]["outcome"] == "cancelled"
        assert events[-2]["input_tokens"] == 3
        assert events[-2]["output_tokens"] == 1
        client._execution_adapter._environment["TEST_OUTCOME"] = "ok"
        await client._message_handlers[0](message("next-turn"))
        assert client.send.call_args.args[1] == "local answer"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,version,expected",
    [
        (
            "pi-cli",
            "0.87.1",
            "UNSUPPORTED_RUNTIME: pi-cli 0.87.1 is not supported; this build requires 0.85.1",
        ),
    ],
)
async def test_version_mismatch_error_names_observed_and_expected(
    setup_room, monkeypatch, engine, version, expected
):
    monkeypatch.setenv("TEST_VERSION", version)
    client = await client_for(engine, monkeypatch)
    try:
        await client._message_handlers[0](message())
        finished = [
            c.kwargs
            for c in client.sendLifecycle.call_args_list
            if c.kwargs.get("event") == "engine_call_finished"
        ]
        assert finished[0]["outcome"] == "failed"
        assert finished[0]["error"] == expected
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_codex_unlisted_version_executes_room_turn(setup_room, monkeypatch):
    monkeypatch.setenv("TEST_VERSION", "codex-cli 0.160.0")
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message())
        assert client.send.call_args.args[1] == "local answer"
        assert client._execution_adapter._runtime_version == "0.160.0"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.req("ENG-07")
async def test_pi_without_managed_install_or_opt_in_refuses_to_start(
    setup_room, monkeypatch
):
    """#688 — never silently fall back to whatever ``pi`` is on PATH."""
    monkeypatch.delenv("ANYGARDEN_PI_EXECUTABLE")
    with pytest.raises(ValueError, match="managed Pi install is missing"):
        await client_for("pi-cli", monkeypatch)


@pytest.mark.asyncio
@pytest.mark.req("ENG-07")
async def test_pi_path_opt_in_uses_path(setup_room, monkeypatch):
    monkeypatch.delenv("ANYGARDEN_PI_EXECUTABLE")
    monkeypatch.setenv("ANYGARDEN_PI_USE_PATH", "1")
    client = await client_for("pi-cli", monkeypatch)
    try:
        assert client._execution_adapter._codex_path == str(setup_room / "fake-cli")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pi_explicit_executable_must_be_executable(setup_room, monkeypatch):
    monkeypatch.setenv("ANYGARDEN_PI_EXECUTABLE", str(setup_room / "missing-pi"))
    with pytest.raises(ValueError, match="ANYGARDEN_PI_EXECUTABLE"):
        await client_for("pi-cli", monkeypatch)


@pytest.mark.asyncio
@pytest.mark.req("EXE-10")
@pytest.mark.parametrize(
    "failure,reason,transient",
    [
        ("before-effect", "MODEL_TRANSIENT_FAILURE", True),
        ("after-effect", "MODEL_EXECUTION_FAILED", False),
    ],
)
async def test_codex_transient_failure_reaches_frames_as_native_transient(
    setup_room, monkeypatch, failure, reason, transient
):
    """A 429 before any tool item rides the terminal frames as transient so the
    cluster's bounded auto-retry applies; a 5xx after a tool item does not."""
    monkeypatch.setenv("TEST_CODEX_FAIL", failure)
    client = await client_for("codex-cli", monkeypatch)
    try:
        await client._message_handlers[0](message())
        frames = {
            c.kwargs["event"]: c.kwargs
            for c in client.sendLifecycle.call_args_list
            if c.kwargs.get("event") in {"engine_call_finished", "handler_finished"}
        }
        for event in ("engine_call_finished", "handler_finished"):
            frame = frames[event]
            assert frame["outcome"] == "failed"
            assert frame["native_outcome"] == "failed"
            assert frame["native_process_state"] == "stopped"
            assert frame["native_reason_code"] == reason
            assert frame["native_transient"] is transient
            assert frame["local_execution_id"]
        assert "DO-NOT-PUBLISH" not in str(client.sendLifecycle.call_args_list)
        assert "DO-NOT-PUBLISH" not in str(client.send.call_args_list)
        calls = (setup_room / "workspace" / "calls.jsonl").read_text().splitlines()
        assert len(calls) == 1  # the agent never retries in-process
    finally:
        await client.close()
