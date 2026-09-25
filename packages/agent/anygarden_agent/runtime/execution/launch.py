"""Trusted local launch configuration, populated only from private agent stdin.

Room adapters bind this snapshot to an Invocation before dispatch. Remote
commands must not instantiate or replace the client launch configuration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

from anygarden_agent import secrets

from .contracts import Invocation, _valid_provider
from .endpoint import (
    CHILD_KEY,
    CONFIG_KEY,
    INPUT_KEY,
    DirectEndpoint,
    endpoint_environment,
    parse_endpoint,
)
from .pi_auth import CONFIG_KEY as PI_CONFIG_KEY
from .pi_auth import INPUT_KEY as PI_INPUT_KEY
from .pi_auth import NativePiAuth, parse_native_auth


@dataclass(frozen=True)
class ExecutionLaunch:
    engine: str
    provider: str | None
    model: str | None
    generation: int
    endpoint: DirectEndpoint | None
    _child_environment: dict[str, str] = field(default_factory=dict, repr=False)
    native_auth: NativePiAuth | None = None

    def bind(self, invocation: Invocation) -> Invocation:
        if invocation.scope.engine != self.engine:
            raise ValueError("Launch engine differs from invocation engine")
        environment = dict(invocation.environment)
        for key in (CHILD_KEY, CONFIG_KEY, INPUT_KEY, PI_CONFIG_KEY, PI_INPUT_KEY):
            environment.pop(key, None)
        environment.update(self._child_environment)
        # Bind both the caller's policy epoch and the server launch generation;
        # descriptor/revision also fences the commit-to-generation-bump interval.
        epoch = int.from_bytes(
            hashlib.sha256(
                json.dumps(
                    [
                        invocation.scope.policy_epoch,
                        self.generation,
                        self.provider,
                        self.model,
                        asdict(self.endpoint) if self.endpoint else None,
                        asdict(self.native_auth) if self.native_auth else None,
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).digest()[:8],
            "big",
        )
        bound = replace(
            invocation,
            provider=self.provider,
            model=self.model,
            endpoint=self.endpoint,
            native_auth=self.native_auth,
            environment=environment,
            scope=replace(invocation.scope, policy_epoch=epoch),
        )
        bound.validate()
        return bound


def load_execution_launch(
    *,
    engine: str,
    provider: str | None,
    model: str | None,
    generation: int,
    endpoint_configured: bool = False,
    pi_auth_configured: bool = False,
) -> ExecutionLaunch:
    if type(generation) is not int or generation < 0:
        raise ValueError("Invalid launch generation")
    if provider is not None and not _valid_provider(provider):
        raise ValueError("Invalid launch provider")
    if engine == "pi-cli" and provider is None:
        raise ValueError("Pi requires an explicit launch provider")
    config = secrets.get(CONFIG_KEY)
    if endpoint_configured and not config:
        raise ValueError("Direct endpoint configuration is missing from private stdin")
    endpoint = parse_endpoint(config, engine=engine)
    child_environment = endpoint_environment(endpoint, secrets.get(INPUT_KEY))
    pi_config = secrets.get(PI_CONFIG_KEY)
    pi_key = secrets.get(PI_INPUT_KEY)
    if pi_auth_configured and not (pi_config and pi_key):
        raise ValueError("Pi native credential is missing from private stdin")
    native_auth = parse_native_auth(
        pi_config, pi_key, provider=provider, engine=engine, endpoint=endpoint
    )
    if engine == "pi-cli" and endpoint is None and native_auth is None:
        raise ValueError("Pi native provider credential is missing from private stdin")
    if native_auth is not None:
        child_environment[PI_INPUT_KEY] = pi_key
    if endpoint is not None and (
        endpoint.provider != provider or endpoint.model != model
    ):
        raise ValueError("Direct endpoint selection differs from launch provider/model")
    return ExecutionLaunch(
        engine, provider, model, generation, endpoint, child_environment, native_auth
    )
