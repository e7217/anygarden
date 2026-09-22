"""Passive schema retention for retired configuration tables.

These tables have no ORM mappings, routes, or readers in the running product.
Registering them in Base.metadata prevents a future Alembic autogenerate from
mistaking preserved historical configuration and ciphertext for dropped tables.
The existing migration chain creates them; removal adds no destructive migration.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Index,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

from anygarden.db.types import UtcDateTime


def register_archived_gateway_tables(metadata: MetaData) -> None:
    Table(
        "llm_gateway_models",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("model_name", String(128), nullable=False),
        Column("provider", String(32), nullable=False),
        Column("upstream_model", String(255), nullable=False),
        Column("api_key_ref", String(64), nullable=False),
        Column("extra_params", JSON, nullable=True),
        Column("enabled", Boolean, nullable=False),
        Column("created_at", UtcDateTime, nullable=False),
        Column("updated_at", UtcDateTime, nullable=False),
        UniqueConstraint("model_name", name="uq_llm_gateway_models_name"),
        Index("ix_llm_gateway_models_provider", "provider"),
        info={"archived": True},
    )
    Table(
        "llm_gateway_secrets",
        metadata,
        Column("env_var_name", String(64), primary_key=True),
        Column("encrypted_value", LargeBinary, nullable=False),
        Column("last_tested_at", UtcDateTime, nullable=True),
        Column("last_test_status", String(64), nullable=True),
        Column("created_at", UtcDateTime, nullable=False),
        Column("updated_at", UtcDateTime, nullable=False),
        info={"archived": True},
    )
