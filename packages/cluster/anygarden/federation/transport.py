"""Bounded pinned HTTP over actual mTLS. No DNS dialing, redirects or proxy env."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from anygarden.federation.certificates import (
    identity_from_tls,
    inspect_certificate,
    tls_context,
)
from anygarden.federation.endpoint import validate_endpoint
from anygarden.federation.errors import PeerError

MAX_BODY = 65536


async def post(service, endpoint, pem: str, path: str, payload: dict) -> dict:
    target = validate_endpoint(endpoint, allow_loopback=service.allow_loopback)
    expected = inspect_certificate(pem)
    context = tls_context(service.cert_path, service.key_path, [pem], server=False)
    writer = None
    try:
        async with asyncio.timeout(10):
            # Numeric IP comes only from local admin policy. A DNS change cannot redirect it.
            reader, writer = await asyncio.open_connection(
                target.ips[0],
                target.port,
                ssl=context,
                server_hostname=target.host,
                ssl_handshake_timeout=5,
            )
            actual = identity_from_tls(writer.get_extra_info("ssl_object"))
            if actual != expected:
                raise PeerError("PIN_MISMATCH", 401)
            raw = json.dumps(payload, separators=(",", ":")).encode()
            if len(raw) > MAX_BODY:
                raise PeerError("INVALID_SCHEMA", 400)
            conn = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
            host = f"[{target.host}]" if ":" in target.host else target.host
            writer.write(
                conn.send(
                    h11.Request(
                        method="POST",
                        target=path,
                        headers=[
                            ("Host", f"{host}:{target.port}"),
                            ("Content-Type", "application/json"),
                            ("Content-Length", str(len(raw))),
                            ("Connection", "close"),
                        ],
                    )
                )
            )
            writer.write(conn.send(h11.Data(data=raw)))
            writer.write(conn.send(h11.EndOfMessage()))
            await writer.drain()
            body = bytearray()
            status = 0
            while True:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    incoming = await reader.read(16384)
                    conn.receive_data(incoming)
                elif isinstance(event, h11.Response):
                    status = event.status_code
                    if 300 <= status < 400:
                        raise PeerError("REDIRECT_DENIED", 502)
                elif isinstance(event, h11.Data):
                    body.extend(event.data)
                    if len(body) > MAX_BODY:
                        raise PeerError("RESPONSE_TOO_LARGE", 502)
                elif isinstance(event, h11.EndOfMessage):
                    break
                elif isinstance(event, h11.ConnectionClosed):
                    raise PeerError("PEER_UNAVAILABLE", 503)
            if status != 200:
                raise PeerError("REMOTE_REJECTED", 502)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise PeerError("RECEIPT_DENIED", 502)
            return result
    except PeerError:
        raise
    except (OSError, TimeoutError, ValueError, h11.ProtocolError):
        raise PeerError("PEER_UNAVAILABLE", 503) from None
    finally:
        if writer:
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)


class PeerListener:
    """Opt-in separate listener. It mounts only federation control endpoints."""

    def __init__(self, service, host="127.0.0.1", port=0, *, channel_service=None):
        self.service, self.host, self.port = service, host, port
        self.channel_service = channel_service
        self.server = self.task = None
        self.context = None

    async def refresh_trust(self):
        if self.context:
            for pem in await self.service.trusted_certificates():
                # Old TLS trust anchors may remain until restart, but every HTTP
                # request still compares the current exact pin and state in SQL.
                self.context.load_verify_locations(cadata=pem)

    async def start(self):
        from anygarden.federation.router import create_peer_app

        service = self.service

        class PinnedProtocol(H11Protocol):
            def connection_made(self, transport):
                super().connection_made(transport)
                ssl_object = transport.get_extra_info("ssl_object")
                try:
                    if ssl_object is None:
                        raise PeerError("MTLS_REQUIRED", 401)
                    cert_identity = identity_from_tls(ssl_object)
                except PeerError:
                    transport.close()
                    return
                original = self.app
                closer = transport.close
                service._closers.setdefault(cert_identity.node_id, set()).add(closer)
                self._peer_closer = (cert_identity.node_id, closer)

                async def verified_app(scope, receive, send):
                    # A server-owned ASGI value, never populated from HTTP headers.
                    scope["anygarden.verified_peer"] = cert_identity
                    await original(scope, receive, send)

                self.app = verified_app

            def connection_lost(self, exc):
                if hasattr(self, "_peer_closer"):
                    node, closer = self._peer_closer
                    service._closers.get(node, set()).discard(closer)
                super().connection_lost(exc)

        config = uvicorn.Config(
            create_peer_app(service, self.channel_service),
            host=self.host,
            port=self.port,
            http=PinnedProtocol,
            ws="none",
            lifespan="off",
            access_log=False,
            log_level="critical",
            proxy_headers=False,
            timeout_keep_alive=5,
            limit_concurrency=64,
            h11_max_incomplete_event_size=16384,
        )
        config.load()
        self.context = tls_context(
            service.cert_path,
            service.key_path,
            await service.trusted_certificates(),
            server=True,
        )
        config.ssl = self.context
        self.server = uvicorn.Server(config)
        self.service.trust_changed = self.refresh_trust
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(500):
            if self.server.started:
                self.port = self.server.servers[0].sockets[0].getsockname()[1]
                return self
            if self.task.done():
                await self.task
                raise PeerError("LISTENER_START_FAILED", 503)
            await asyncio.sleep(0.01)
        await self.stop()
        raise PeerError("LISTENER_START_FAILED", 503)

    async def stop(self):
        if self.server:
            self.server.should_exit = True
        if self.task:
            await asyncio.wait_for(self.task, 10)
        self.service.trust_changed = None
