"""Strict control-plane models; channel command envelopes remain owned by #587."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Action = Literal[
    "channel.read", "message.send", "task.request", "task.execute", "task.cancel"
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Principal(ClosedModel):
    node_id: UUID
    kind: Literal["user", "agent"]
    principal_id: UUID


class Scope(ClosedModel):
    channel_id: UUID
    actors: list[Principal] = Field(min_length=1, max_length=100)
    capabilities: list[Action] = Field(min_length=1, max_length=5)
    role: Literal["observer", "member", "admin"] = "member"

    @model_validator(mode="after")
    def distinct(self):
        keys = [(p.node_id, p.kind, p.principal_id) for p in self.actors]
        if len(keys) != len(set(keys)) or len(set(self.capabilities)) != len(
            self.capabilities
        ):
            raise ValueError("duplicate scope entries")
        if self.role == "observer" and self.capabilities != ["channel.read"]:
            raise ValueError("observer grants may only read")
        return self


class Endpoint(ClosedModel):
    url: str = Field(max_length=2048)
    # Explicit local admin approval. Remote values never authorize dialing.
    approved_ips: list[str] = Field(min_length=1, max_length=16)
    allow_private: bool = False


class InviteCreate(ClosedModel):
    intended_node_id: UUID
    certificate_pem: str = Field(max_length=16384)
    endpoint: Endpoint
    scopes: list[Scope] = Field(min_length=1, max_length=32)
    expires_in_seconds: int = Field(default=600, ge=30, le=3600)
    grant_expires_in_seconds: int = Field(default=86400, ge=30, le=2592000)


class InviteBundle(ClosedModel):
    protocol_version: Literal[1] = 1
    invite_id: UUID
    issuer_node_id: UUID
    issuer_certificate_pem: str = Field(max_length=16384)
    intended_node_id: UUID
    intended_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    token: SecretStr = Field(min_length=32, max_length=128)
    scopes: list[Scope] = Field(min_length=1, max_length=32)
    expires_at: datetime
    grant_expires_at: datetime


class InviteAccept(ClosedModel):
    bundle: InviteBundle
    # Invited admin independently supplies endpoint/IP policy, not the issuer.
    issuer_endpoint: Endpoint


class Redeem(ClosedModel):
    protocol_version: Literal[1] = 1
    sender_node_id: UUID
    token: SecretStr = Field(min_length=32, max_length=128)


class Hello(ClosedModel):
    protocol_version: int
    sender_node_id: UUID


class RotatePin(ClosedModel):
    certificate_pem: str = Field(max_length=16384)
    expected_peer_epoch: int = Field(ge=1)


class RevokeControl(ClosedModel):
    protocol_version: Literal[1] = 1
    sender_node_id: UUID
    event_id: UUID
    peer_epoch: int = Field(ge=1)


class GrantReplace(ClosedModel):
    scope: Scope
    expected_grant_epoch: int = Field(ge=1)
    expires_in_seconds: int = Field(default=86400, ge=30, le=2592000)


class GrantRevokeControl(RevokeControl):
    channel_id: UUID
    grant_epoch: int = Field(ge=1)


class GrantReceipt(ClosedModel):
    channel_id: UUID
    grant_epoch: int = Field(ge=1, strict=True)


class RedemptionReceipt(ClosedModel):
    protocol_version: Literal[1]
    invite_id: UUID
    issuer_node_id: UUID
    intended_node_id: UUID
    grants: list[GrantReceipt] = Field(min_length=1, max_length=32)
    state: Literal["accepted"]
