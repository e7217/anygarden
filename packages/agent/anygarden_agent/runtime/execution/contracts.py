"""Local invocation contract. Native sessions and receipts never go to peers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .endpoint import DirectEndpoint, validate_endpoint_invocation

Outcome = Literal["succeeded", "failed", "cancelled", "unknown"]
ProcessState = Literal["not_started", "running", "finished", "stopped", "unknown"]
FAILURE_CODES = frozenset(
    {"ENGINE_ERROR", "TIMEOUT_STOPPED", "UNSUPPORTED_RUNTIME", "POLICY_DENIED"}
)


SUPPORTED_ENGINES = frozenset({"codex-cli", "pi-cli"})


def unsupported_version_detail(
    engine: str, observed: str | None, supported: tuple[str, ...]
) -> str:
    """Operator-facing reason for a failed CLI version gate (#687).

    The receipt keeps the closed ``UNSUPPORTED_RUNTIME`` code; this text is
    only attached locally. Versions are not secrets, so naming both the
    observed and the expected version is safe and makes the fix obvious.
    """
    required = (
        supported[0]
        if len(supported) == 1
        else "one of " + ", ".join(supported)
    )
    if observed is None:
        return f"could not read the installed {engine} version; this build requires {required}"
    return f"{engine} {observed} is not supported; this build requires {required}"

# Provider names may carry hyphens/dots (``openai-codex``, ``my-local``)
# but never whitespace, ``=`` or other shell/tooling metacharacters —
# the closed charset keeps injection out while custom names work.
_PROVIDER_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _valid_provider(name: str) -> bool:
    # First char alphanumeric (dev02/API alignment): a leading ``-`` or
    # ``.`` would read as an option in ``--provider=value`` forms.
    return (
        1 <= len(name) <= 64
        and set(name) <= _PROVIDER_CHARS
        and name[0].isalnum()
    )


@dataclass(frozen=True)
class SessionScope:
    execution_node_id: str
    agent_id: str
    authority_node_id: str
    channel_id: str
    thread_root_id: str | None
    workspace_binding_id: str
    workspace_epoch: int
    policy_epoch: int
    engine: str = "codex-cli"
    engine_version: str = "0.154.0"

    @property
    def key(self) -> str:
        return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class Invocation:
    execution_id: str
    scope: SessionScope
    prompt: str
    workspace: Path
    runtime_home: Path
    instructions: str = ""
    model: str | None = None
    # Explicit provider selection for multi-provider runtimes (pi --provider).
    # Must be set for engines that would otherwise fall back to an ambient
    # default provider — never let a runtime pick one implicitly.
    provider: str | None = None
    reasoning_effort: str | None = None
    permission_level: str = "restricted"
    timeout_seconds: float = 600
    # Caller is the trusted local policy/materialization layer, never a wire payload.
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    external_workspace: bool = False
    endpoint: DirectEndpoint | None = None

    def validate(self) -> None:
        validate_endpoint_invocation(self)
        if not self.execution_id or not self.prompt:
            raise ValueError("execution_id and prompt are required")
        # Engine membership is central. Each adapter handles CLI compatibility
        # according to its own execution contract.
        if self.scope.engine not in SUPPORTED_ENGINES:
            raise ValueError("unsupported runtime")
        if self.scope.engine == "pi-cli" and not self.provider:
            # pi without an explicit provider would use the machine's ambient
            # default (the exact path of the 2026-09-17 incident).
            raise ValueError("pi-cli requires an explicit provider")
        if self.provider is not None and not _valid_provider(self.provider):
            raise ValueError("provider must be a plain provider name")
        if self.permission_level not in {"restricted", "standard"}:
            raise ValueError("unsupported permission level")
        if self.external_workspace:
            raise ValueError("external workspace enforcement is not supported")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout must be finite and positive")
        for path in (self.workspace, self.runtime_home):
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(
                    "workspace and runtime_home must be existing absolute directories"
                )
        if not all(
            (
                self.scope.execution_node_id,
                self.scope.agent_id,
                self.scope.authority_node_id,
                self.scope.channel_id,
                self.scope.workspace_binding_id,
            )
        ):
            raise ValueError("incomplete session scope")
        if self.scope.workspace_epoch < 0 or self.scope.policy_epoch < 0:
            raise ValueError("invalid session epoch")
        if not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in self.environment.items()
        ):
            raise ValueError("environment must map strings to strings")

    @property
    def fingerprint(self) -> str:
        data = asdict(self)
        data["workspace"] = str(self.workspace.resolve())
        data["runtime_home"] = str(self.runtime_home.resolve())
        # Store only the digest, not credentials, prompts, paths or instructions.
        return hashlib.sha256(canonical(data).encode()).hexdigest()


@dataclass(frozen=True)
class Capabilities:
    engine: str = "codex-cli"
    engine_version: str = "0.154.0"
    progress: bool = True
    cancel: bool = True
    resume: bool = True
    workspace_enforcement: bool = False
    external_workspace_writes: bool = False
    automatic_retry: bool = False


@dataclass(frozen=True)
class RuntimeResult:
    outcome: Outcome
    process_state: ProcessState
    reason: str
    text: str | None = None
    session_handle: str | None = None
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class Receipt:
    execution_id: str
    state: str
    process_state: ProcessState
    outcome: Outcome | None = None
    reason: str | None = None
    text: str | None = None
    usage: dict[str, int] | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ExecutionEvent:
    execution_id: str
    sequence: int
    kind: str
    payload: dict


class Runtime(Protocol):
    def capabilities(self) -> Capabilities: ...
    async def run(
        self,
        invocation: Invocation,
        session_handle: str | None,
        emit: Callable[[str, dict], None],
        launched: Callable[[int], None],
        authorized: Callable[[], bool],
    ) -> RuntimeResult: ...


class ExecutionManager(Protocol):
    def capabilities(self) -> Capabilities: ...
    async def start(self, invocation: Invocation) -> Receipt: ...
    def events(
        self, execution_id: str, *, after: int = 0
    ) -> AsyncIterator[ExecutionEvent]: ...
    async def cancel(self, execution_id: str) -> Receipt: ...
    async def reconcile(self, execution_id: str) -> Receipt: ...


def canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
