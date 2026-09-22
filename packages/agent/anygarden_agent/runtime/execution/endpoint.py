"""Direct endpoint materialization from authenticated, process-private stdin.

No environment discovery, arbitrary config keys, command-valued credentials,
or peer-supplied endpoint selection. DirectEndpoint contains no secret values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_KEY = "AG_ENGINE_ENDPOINT_CONFIG"
INPUT_KEY = "AG_ENGINE_ENDPOINT_KEY"
CHILD_KEY = "AG_DIRECT_API_KEY"
KEYLESS_PLACEHOLDER = "ag-keyless-local"
_PROVIDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


@dataclass(frozen=True)
class DirectEndpoint:
    provider: str
    model: str
    base_url: str
    api_protocol: str
    credential_ref: str | None = None
    credential_revision: int = 0

    def validate(self, engine: str) -> None:
        if engine not in {"codex-cli", "pi-cli"}:
            raise ValueError("Direct endpoints require Codex or Pi")
        if not isinstance(self.provider, str) or not _PROVIDER.fullmatch(self.provider):
            raise ValueError("Invalid endpoint provider")
        if (
            not isinstance(self.model, str)
            or not self.model
            or len(self.model) > 256
            or any(ord(c) < 32 for c in self.model)
        ):
            raise ValueError("An explicit endpoint model is required")
        if (
            not isinstance(self.base_url, str)
            or len(self.base_url) > 2048
            or any(ord(c) < 33 for c in self.base_url)
            or "\\" in self.base_url
        ):
            raise ValueError("Invalid endpoint URL")
        try:
            url = urlsplit(self.base_url)
            port = url.port
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError(
                "Endpoint URL must be HTTP(S), without credentials, query or fragment"
            ) from None
        if self.api_protocol not in {"responses", "chat-completions"} or (
            engine == "codex-cli" and self.api_protocol != "responses"
        ):
            raise ValueError(
                "Codex requires Responses; Pi supports Responses or Chat Completions"
            )
        if self.credential_ref is not None and (
            not isinstance(self.credential_ref, str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", self.credential_ref)
        ):
            raise ValueError("Invalid credential reference")
        if (
            type(self.credential_revision) is not int
            or self.credential_revision < 0
            or bool(self.credential_ref) != bool(self.credential_revision)
        ):
            raise ValueError("Invalid credential revision")


def parse_endpoint(config_json: str | None, *, engine: str) -> DirectEndpoint | None:
    if config_json is None:
        return None
    try:
        data = json.loads(config_json)
        if not isinstance(data, dict):
            raise TypeError
        endpoint = DirectEndpoint(**data)
        endpoint.validate(engine)
        return endpoint
    except (ValueError, TypeError):
        # Never reflect untrusted values (including credential-bearing URLs).
        raise ValueError("Invalid direct endpoint configuration") from None


def endpoint_environment(
    endpoint: DirectEndpoint | None, key: str | None
) -> dict[str, str]:
    if endpoint is None:
        if key is not None:
            raise ValueError("Endpoint credential without configuration")
        return {}
    if endpoint.credential_ref:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ValueError("Endpoint credential is unavailable or invalid")
        return {CHILD_KEY: key}
    if key is not None:
        raise ValueError("Unexpected endpoint credential")
    # Pi requires a nonempty auth value to select local models. This fixed,
    # nonsecret placeholder is never a substitute for a missing stored key.
    return {CHILD_KEY: KEYLESS_PLACEHOLDER}


def codex_endpoint_arguments(endpoint: DirectEndpoint | None) -> list[str]:
    if endpoint is None:
        return []
    endpoint.validate("codex-cli")
    # JSON strings are also TOML basic strings. Values cannot become TOML
    # keys or extra CLI arguments; only these fixed configuration keys exist.
    values: dict[str, object] = {
        "model_provider": "ag_direct",
        "model_providers.ag_direct.name": "AnyGarden direct endpoint",
        "model_providers.ag_direct.base_url": endpoint.base_url,
        "model_providers.ag_direct.wire_api": "responses",
        "model_providers.ag_direct.requires_openai_auth": False,
        "model_providers.ag_direct.request_max_retries": 0,
        "model_providers.ag_direct.stream_max_retries": 0,
    }
    if endpoint.credential_ref:
        values["model_providers.ag_direct.env_key"] = CHILD_KEY
    return [
        part
        for key, value in values.items()
        for part in ("-c", f"{key}={json.dumps(value)}")
    ]


def materialize_pi_endpoint(
    runtime_home: Path, endpoint: DirectEndpoint | None
) -> None:
    """Atomically replace only adapter-managed config in a per-agent directory."""
    import secrets

    if endpoint is not None:
        endpoint.validate("pi-cli")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(runtime_home, flags)
    try:
        for component in (".pi", "agent"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child

        def read_regular(name: str) -> bytes | None:
            try:
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                )
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Pi config must be a regular file")
                return stream.read(65537)

        def replace_regular(name: str, content: bytes) -> None:
            temporary = ".endpoint-" + secrets.token_hex(12)
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=descriptor,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                os.replace(
                    temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor
                )
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass

        marker_name = ".anygarden-endpoint.sha256"
        marker = read_regular(marker_name)
        current = read_regular("models.json")
        # Ownership must hold before every mutation, including activation and
        # rotation. A marker alone does not authorize replacing an edited file.
        if current is not None:
            if marker is None:
                if endpoint is None:
                    return
                raise ValueError("Existing Pi models.json is not managed by AnyGarden")
            if hashlib.sha256(current).hexdigest().encode() != marker:
                raise ValueError("Managed Pi endpoint config was modified")
        if endpoint is None:
            if marker is None:
                return
            if current is not None:
                os.unlink("models.json", dir_fd=descriptor)
            os.unlink(marker_name, dir_fd=descriptor)
            return
        # Pi gives auth.json credentials priority over models.json apiKey.
        # Refuse pre-existing credentials rather than silently selecting them.
        auth = read_regular("auth.json")
        if auth is not None:
            try:
                stored_auth = json.loads(auth)
                if not isinstance(stored_auth, dict) or endpoint.provider in stored_auth:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("Stored Pi authentication conflicts with the selected direct endpoint provider") from None
        data = {
            "providers": {
                endpoint.provider: {
                    "baseUrl": endpoint.base_url,
                    "api": "openai-responses"
                    if endpoint.api_protocol == "responses"
                    else "openai-completions",
                    "apiKey": f"${CHILD_KEY}",
                    "models": [{"id": endpoint.model, "name": endpoint.model}],
                }
            }
        }
        content = json.dumps(data).encode()
        replace_regular("models.json", content)
        replace_regular(marker_name, hashlib.sha256(content).hexdigest().encode())
    finally:
        os.close(descriptor)


def validate_endpoint_invocation(invocation) -> DirectEndpoint | None:
    """Check trusted descriptor consistency before any child is created."""
    endpoint = getattr(invocation, "endpoint", None)
    if endpoint is None:
        if CHILD_KEY in invocation.environment:
            raise ValueError("Endpoint credential without endpoint configuration")
        return None
    endpoint.validate(invocation.scope.engine)
    if invocation.model != endpoint.model or invocation.provider != endpoint.provider:
        raise ValueError("Endpoint provider/model differs from invocation selection")
    expected = endpoint_environment(endpoint, invocation.environment.get(CHILD_KEY) if endpoint.credential_ref else None)
    if invocation.environment.get(CHILD_KEY) != expected[CHILD_KEY]:
        raise ValueError("Direct endpoint credential is unavailable")
    return endpoint
