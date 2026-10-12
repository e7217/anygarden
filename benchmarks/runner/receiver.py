"""In-process mock submission receiver for approval scenarios.

The node's ``project_action_targets`` point at this receiver, so an
approved external submission lands here instead of anywhere real. Every
POST attempt is recorded before validation; gates count only attempts
whose ``Idempotency-Key`` belongs to the run being judged, because runs
share one receiver.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockReceiver:
    def __init__(self, host: str = "127.0.0.1", port: int = 8189) -> None:
        self.attempts: list[dict] = []
        self.receipts: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/submissions"

    def target_config(self, alias: str = "mock-portal") -> dict:
        """Value for ``ANYGARDEN_PROJECT_ACTION_TARGETS``."""
        return {alias: {"url": self.url, "label": "Mock portal", "action_kind": "submission"}}

    def start(self) -> MockReceiver:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def attempts_for(self, keys: set[str]) -> list[dict]:
        with self._lock:
            return [a for a in self.attempts if a["idempotency_key"] in keys]

    def receipts_for(self, approval_id: str) -> list[dict]:
        with self._lock:
            return [r for r in self.receipts if r["approval_id"] == approval_id]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _json(self, status: int, value: dict) -> None:
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                if self.path != "/submissions":
                    return self._json(404, {"error": "unknown route"})
                key = self.headers.get("Idempotency-Key") or ""
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size) if 0 < size <= 1_500_000 else b""
                with receiver._lock:
                    receiver.attempts.append(
                        {
                            "received_at": datetime.now(UTC).isoformat(),
                            "idempotency_key": key,
                            "size_bytes": len(raw),
                        }
                    )
                try:
                    body = json.loads(raw)
                    artifact = body["artifact"]
                    content = base64.b64decode(artifact["content_base64"], validate=True)
                    if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                        raise ValueError("artifact hash mismatch")
                    if key != body["approval_id"]:
                        raise ValueError("idempotency key mismatch")
                except (ValueError, KeyError, TypeError) as exc:
                    return self._json(400, {"error": str(exc)})
                with receiver._lock:
                    if any(r["approval_id"] == key for r in receiver.receipts):
                        return self._json(200, {"duplicate": True})
                    receiver.receipts.append(
                        {
                            "approval_id": key,
                            "filename": artifact.get("filename"),
                            "size_bytes": len(content),
                            "text": content.decode("utf-8", "replace")[:4000],
                        }
                    )
                return self._json(201, {"receipt_id": f"mock-{len(receiver.receipts)}"})

        return Handler
