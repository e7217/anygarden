"""Opt-in actual CLI probe. Run ONLY in a fresh network namespace with loopback.

unshare -n sh -c 'ip link set lo up; exec <python> offline_codex_probe.py <codex>'
Never uses existing credentials or contacts an external provider.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anygarden_agent.runtime.execution import (
    CodexRuntime,
    Invocation,
    LocalExecutionManager,
    SessionScope,
)


class FakeResponses(BaseHTTPRequestHandler):
    requests = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).requests += 1
        item = {
            "id": "message-offline",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": "offline fixture answer",
                    "annotations": [],
                }
            ],
        }
        response = {
            "id": "resp-offline",
            "object": "response",
            "model": "gpt-5.4",
            "status": "completed",
            "output": [item],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 3,
                "total_tokens": 13,
                "input_tokens_details": {"cached_tokens": 0},
            },
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []},
            },
            {
                "type": "response.content_part.added",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": "offline fixture answer",
            },
            {
                "type": "response.output_text.done",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "text": "offline fixture answer",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        body = "".join(
            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def main():
    # Do not run in the host network accidentally. A namespace with only lo has
    # no external interface even if someone later supplies a provider URL.
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise SystemExit(
            "refusing: requires isolated network namespace with only loopback"
        )
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeResponses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="codex-offline-") as directory:
            root = Path(directory)
            workspace, home = root / "workspace", root / "runtime-home"
            workspace.mkdir()
            home.mkdir()
            inv = Invocation(
                "native-1",
                SessionScope("n", "a", "n", "c", None, "w", 1, 1),
                "Return the fixture answer.",
                workspace,
                home,
                model="gpt-5.4",
                timeout_seconds=15,
                environment={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "CODEX_API_KEY": "offline-fixture-not-a-secret",
                },
            )
            handles = []

            class OfflineRuntime(CodexRuntime):
                def command(self, invocation, session, output):
                    handles.append(session)
                    command = super().command(invocation, session, output)
                    return command[:-1] + [
                        "-c",
                        'model_provider="fixture"',
                        "-c",
                        'model_providers.fixture.name="offline fixture"',
                        "-c",
                        f'model_providers.fixture.base_url="http://127.0.0.1:{server.server_port}/v1"',
                        "-c",
                        'model_providers.fixture.wire_api="responses"',
                        "-c",
                        'model_providers.fixture.env_key="CODEX_API_KEY"',
                        "-c",
                        "model_providers.fixture.supports_websockets=false",
                        "-",
                    ]

            m = LocalExecutionManager(
                root / "receipts",
                OfflineRuntime(Path(sys.argv[1]).resolve()),
                authorize=lambda _: True,
            )
            try:
                for invocation in (inv, replace(inv, execution_id="native-2")):
                    await m.start(invocation)
                    async with asyncio.timeout(25):
                        events = [e async for e in m.events(invocation.execution_id)]
                    receipt = await m.reconcile(invocation.execution_id)
                    print(
                        json.dumps(
                            {
                                "execution_id": receipt.execution_id,
                                "outcome": receipt.outcome,
                                "reason": receipt.reason,
                                "text": receipt.text,
                                "event_count": len(events),
                            }
                        ),
                        flush=True,
                    )
                    if receipt.outcome != "succeeded":
                        print("loopback requests:", FakeResponses.requests, flush=True)
                        for path in home.rglob("*.jsonl"):
                            for line in path.read_text().splitlines():
                                if '"error"' in line:
                                    print(line[:2000], flush=True)
                    assert receipt.outcome == "succeeded", receipt
                    assert receipt.text == "offline fixture answer"
                    if invocation.execution_id == "native-1":
                        await m.close()
                        m = LocalExecutionManager(
                            root / "receipts",
                            OfflineRuntime(Path(sys.argv[1]).resolve()),
                            authorize=lambda _: True,
                        )
                assert handles[0] is None and handles[1] is not None, handles
                assert FakeResponses.requests == 2, FakeResponses.requests
                print(
                    "actual CLI fresh + resume passed; loopback requests=2; external network unavailable"
                )
            finally:
                await m.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
