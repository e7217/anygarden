"""Service layer for MCP server templates and instances (#124).

Owns the DB side of the feature: template CRUD with builtin-vs-custom
mutation rules, attach/detach with credential validation + encryption,
and the ``render_for_agent`` spawn-time resolution that the lifecycle
frame builder calls.

The encryption is injected (``secrets: MCPSecrets``) rather than
created inline so tests can substitute a deterministic Fernet key
without reaching into settings, and production wiring (``app.py``)
shares a single key across all requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    Agent,
    MCPServerInstance,
    MCPServerTemplate,
)
from anygarden.mcp_templates import builtin as builtin_mod
from anygarden.mcp_templates.encryption import MCPSecrets
from anygarden.mcp_templates.merge import (
    RenderedInstance,
    merge_for_engine,
    render_instance,
    settings_path_for_engine,
)


# ── Exceptions (service → API translates to HTTP codes) ───────────


class TemplateNotFound(LookupError):
    """No template row matches the given id."""


class TemplateNameConflict(ValueError):
    """A template with the requested name already exists."""


class TemplateImmutable(ValueError):
    """Attempt to mutate a builtin template row.

    Builtins are owned by :mod:`anygarden.mcp_templates.builtin` — the
    admin UI blocks edits but we also enforce at the service layer so
    a caller that bypasses the UI still can't corrupt a seeded row.
    """


class TemplateInUse(RuntimeError):
    """Attempt to delete a template that still has live instances.

    Fails by design rather than cascading so admins explicitly detach
    every agent before retiring a custom template — makes the
    credential-expiry implication visible.
    """


class InvalidTemplateConfig(ValueError):
    """``config_per_engine`` contains engines not listed in
    ``supported_engines``, or required fields are missing."""


class EngineIncompatible(RuntimeError):
    """Attach refused because the agent's engine isn't in the template's
    ``supported_engines``."""


class MissingRequiredEnv(ValueError):
    """Attach refused because ``env_values`` doesn't include every name
    the template's ``required_env_vars`` demands."""


# ── Data objects returned to callers ──────────────────────────────


@dataclass
class AttachResult:
    """Return shape of :meth:`MCPTemplateService.attach`.

    ``created`` tells the API layer whether to bump the agent's
    generation — a pure no-op upsert (same credentials re-submitted)
    should not force a respawn.
    """

    instance: MCPServerInstance
    created: bool
    credentials_changed: bool


# ── Service ───────────────────────────────────────────────────────


class MCPTemplateService:
    """Orchestrates template CRUD + per-agent attach/detach + render."""

    def __init__(
        self,
        session_factory,
        *,
        secrets: MCPSecrets,
    ) -> None:
        self._session_factory = session_factory
        self._secrets = secrets

    # ── Template CRUD ─────────────────────────────────────────────

    async def list_templates(
        self,
        db: AsyncSession,
        *,
        source: Optional[str] = None,
    ) -> list[MCPServerTemplate]:
        """Return templates ordered by (source, name) for stable UI."""
        stmt = select(MCPServerTemplate).order_by(
            MCPServerTemplate.source, MCPServerTemplate.name
        )
        if source is not None:
            stmt = stmt.where(MCPServerTemplate.source == source)
        return list((await db.execute(stmt)).scalars().all())

    async def get_template(
        self, db: AsyncSession, template_id: str,
    ) -> MCPServerTemplate:
        row = (
            await db.execute(
                select(MCPServerTemplate).where(MCPServerTemplate.id == template_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise TemplateNotFound(template_id)
        return row

    async def create_custom(
        self,
        db: AsyncSession,
        *,
        name: str,
        display_name: str,
        description: Optional[str],
        icon: Optional[str],
        config_per_engine: dict[str, dict],
        required_env_vars: list[str],
        supported_engines: list[str],
        created_by: Optional[str],
    ) -> MCPServerTemplate:
        """Insert a new custom (admin-authored) template row.

        Validates that every engine key in ``config_per_engine`` is
        also present in ``supported_engines`` (and vice versa — the
        split would confuse the attach-time filter).
        """
        self._validate_config(config_per_engine, supported_engines)

        existing = (
            await db.execute(
                select(MCPServerTemplate).where(MCPServerTemplate.name == name)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise TemplateNameConflict(name)

        row = MCPServerTemplate(
            name=name,
            display_name=display_name,
            description=description,
            icon=icon,
            config_per_engine=config_per_engine,
            required_env_vars=list(required_env_vars),
            supported_engines=list(supported_engines),
            source="custom",
            created_by=created_by,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def update_custom(
        self,
        db: AsyncSession,
        template_id: str,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        icon: Optional[str] = None,
        config_per_engine: Optional[dict[str, dict]] = None,
        required_env_vars: Optional[list[str]] = None,
        supported_engines: Optional[list[str]] = None,
    ) -> MCPServerTemplate:
        """Update a custom template. Builtins are immutable."""
        row = await self.get_template(db, template_id)
        if row.source == "builtin":
            raise TemplateImmutable(template_id)

        if display_name is not None:
            row.display_name = display_name
        if description is not None:
            row.description = description
        if icon is not None:
            row.icon = icon
        if required_env_vars is not None:
            row.required_env_vars = list(required_env_vars)
        # Engine validation has to happen after any of the three
        # engine-shaped fields are touched, using the merged snapshot
        # of the new values (so you can update supported_engines and
        # config_per_engine in the same call without racing yourself).
        if config_per_engine is not None or supported_engines is not None:
            next_cfg = (
                config_per_engine
                if config_per_engine is not None
                else row.config_per_engine
            )
            next_supp = (
                supported_engines
                if supported_engines is not None
                else row.supported_engines
            )
            self._validate_config(next_cfg, next_supp)
            row.config_per_engine = next_cfg
            row.supported_engines = list(next_supp)

        await db.commit()
        await db.refresh(row)
        return row

    async def delete_custom(
        self, db: AsyncSession, template_id: str,
    ) -> None:
        """Delete a custom template. Builtins are immutable; templates
        with live instances raise :class:`TemplateInUse`."""
        row = await self.get_template(db, template_id)
        if row.source == "builtin":
            raise TemplateImmutable(template_id)

        # Check usage first — the FK ondelete is CASCADE so not
        # checking would silently wipe every agent's attachment.
        usage = (
            await db.execute(
                select(MCPServerInstance).where(
                    MCPServerInstance.template_id == template_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if usage is not None:
            raise TemplateInUse(template_id)

        await db.delete(row)
        await db.commit()

    # ── Validation helpers ────────────────────────────────────────

    @staticmethod
    def _validate_config(
        config_per_engine: dict[str, dict],
        supported_engines: list[str],
    ) -> None:
        if not isinstance(config_per_engine, dict) or not config_per_engine:
            raise InvalidTemplateConfig(
                "config_per_engine must be a non-empty dict of "
                "{engine: config}"
            )
        if not isinstance(supported_engines, list) or not supported_engines:
            raise InvalidTemplateConfig(
                "supported_engines must be a non-empty list"
            )
        extra_engines = set(config_per_engine) - set(supported_engines)
        if extra_engines:
            raise InvalidTemplateConfig(
                f"config_per_engine has engines not in supported_engines: "
                f"{sorted(extra_engines)}"
            )
        missing = set(supported_engines) - set(config_per_engine)
        if missing:
            raise InvalidTemplateConfig(
                f"supported_engines lists engines with no config block: "
                f"{sorted(missing)}"
            )
        for engine, block in config_per_engine.items():
            if not isinstance(block, dict):
                raise InvalidTemplateConfig(
                    f"config_per_engine[{engine!r}] must be a dict, got "
                    f"{type(block).__name__}"
                )

    # ── Attach / detach ──────────────────────────────────────────

    async def attach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        template_id: str,
        env_values: dict[str, str],
    ) -> AttachResult:
        """Create or refresh an MCP instance row for the agent.

        Validates engine compatibility + required env presence before
        encrypting. Re-attaching the same (agent, template) overwrites
        credentials in place; ``credentials_changed`` tells the API
        layer whether to bump the agent's generation.
        """
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise LookupError(f"Agent {agent_id} not found")
        template = await self.get_template(db, template_id)

        if agent.engine not in (template.supported_engines or []):
            raise EngineIncompatible(
                f"Template {template.name} does not support engine "
                f"{agent.engine!r}"
            )

        missing = [
            var for var in (template.required_env_vars or [])
            if var not in env_values or env_values[var] == ""
        ]
        if missing:
            raise MissingRequiredEnv(
                f"Missing required env values: {missing}"
            )

        # Only persist the declared variables — drop anything extra
        # the caller included, so an admin who accidentally paste
        # unrelated secrets doesn't write them to the DB.
        relevant = {
            var: env_values[var]
            for var in (template.required_env_vars or [])
            if var in env_values
        }
        ciphertext = (
            self._secrets.encrypt_dict(relevant) if relevant else b""
        )

        existing = (
            await db.execute(
                select(MCPServerInstance).where(
                    MCPServerInstance.template_id == template_id,
                    MCPServerInstance.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            instance = MCPServerInstance(
                template_id=template_id,
                agent_id=agent_id,
                env_values_encrypted=ciphertext or None,
                enabled=True,
            )
            db.add(instance)
            await db.commit()
            await db.refresh(instance)
            return AttachResult(instance=instance, created=True, credentials_changed=True)

        # Compare decrypted values rather than ciphertext — Fernet
        # embeds a timestamp + IV so the same plaintext encrypts to
        # different bytes each call, making raw byte comparison
        # useless for change detection.
        previous = self._secrets.decrypt_dict(existing.env_values_encrypted)
        changed = previous != relevant
        if changed:
            existing.env_values_encrypted = ciphertext or None
        await db.commit()
        await db.refresh(existing)
        return AttachResult(
            instance=existing, created=False, credentials_changed=changed,
        )

    async def detach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        instance_id: str,
    ) -> bool:
        """Remove an instance. Returns True if a row was actually deleted.

        Matches the skill-library idempotency pattern: a detach of
        something that was already gone must NOT bump the agent's
        generation (would force a wasted respawn).
        """
        instance = (
            await db.execute(
                select(MCPServerInstance).where(
                    MCPServerInstance.id == instance_id,
                    MCPServerInstance.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()
        if instance is None:
            return False
        await db.delete(instance)
        await db.commit()
        return True

    async def set_enabled(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        instance_id: str,
        enabled: bool,
    ) -> bool:
        """Toggle ``enabled`` on an instance. Returns True on change.

        Same bump-gating contract as attach/detach.
        """
        instance = (
            await db.execute(
                select(MCPServerInstance).where(
                    MCPServerInstance.id == instance_id,
                    MCPServerInstance.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()
        if instance is None:
            raise LookupError(f"Instance {instance_id} not found")
        if instance.enabled == enabled:
            return False
        instance.enabled = enabled
        await db.commit()
        return True

    async def list_instances_for_agent(
        self, db: AsyncSession, agent_id: str,
    ) -> list[tuple[MCPServerInstance, MCPServerTemplate]]:
        """Return (instance, template) tuples for every instance
        attached to ``agent_id`` regardless of enabled state."""
        rows = (
            await db.execute(
                select(MCPServerInstance, MCPServerTemplate)
                .join(
                    MCPServerTemplate,
                    MCPServerInstance.template_id == MCPServerTemplate.id,
                )
                .where(MCPServerInstance.agent_id == agent_id)
                .order_by(MCPServerTemplate.name)
            )
        ).all()
        return [(instance, template) for instance, template in rows]

    # ── Render (called from lifecycle._build_sync_frame) ─────────

    async def render_for_agent(
        self, db: AsyncSession, agent: Agent,
    ) -> dict[str, str]:
        """Return ``{settings_file_path: content}`` for the agent.

        The frame builder merges this into its ``files_map`` so the
        machine materialises the overlay on spawn. Returns ``{}`` for
        agents with no attached instances or engines without MCP
        support, so the caller doesn't need a guard.
        """
        settings_path = settings_path_for_engine(agent.engine)
        if settings_path is None:
            return {}

        pairs = await self.list_instances_for_agent(db, agent.id)
        overlays: list[RenderedInstance] = []
        for instance, template in pairs:
            if not instance.enabled:
                continue
            env_values = self._secrets.decrypt_dict(
                instance.env_values_encrypted,
            )
            rendered = render_instance(
                name=template.name,
                config_per_engine=template.config_per_engine or {},
                env_values=env_values,
                engine=agent.engine,
            )
            if rendered is not None:
                overlays.append(rendered)

        if not overlays:
            return {}

        merged = merge_for_engine(
            engine=agent.engine,
            admin_content=None,
            overlays=overlays,
        )
        return {settings_path: merged}

    # ── Builtin seed (startup hook) ──────────────────────────────

    async def seed_builtins(self) -> None:
        """Idempotently upsert builtin templates by name.

        Called from the FastAPI lifespan on every boot. Existing rows
        with matching ``name`` are updated in place (config drift
        between cluster versions propagates); missing rows are
        inserted. Never deletes — if a builtin is removed from the
        code, the DB row stays until an admin explicitly cleans it
        up via the API.
        """
        async with self._session_factory() as db:
            for spec in builtin_mod.BUILTIN_TEMPLATES:
                existing = (
                    await db.execute(
                        select(MCPServerTemplate).where(
                            MCPServerTemplate.name == spec.name
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(MCPServerTemplate(
                        name=spec.name,
                        display_name=spec.display_name,
                        description=spec.description,
                        icon=spec.icon,
                        config_per_engine=spec.config_per_engine,
                        required_env_vars=list(spec.required_env_vars),
                        supported_engines=list(spec.supported_engines),
                        source="builtin",
                        created_by=None,
                    ))
                else:
                    existing.display_name = spec.display_name
                    existing.description = spec.description
                    existing.icon = spec.icon
                    existing.config_per_engine = spec.config_per_engine
                    existing.required_env_vars = list(spec.required_env_vars)
                    existing.supported_engines = list(spec.supported_engines)
                    # Force ``source`` to stay "builtin" even if a
                    # previous build incorrectly labelled the row —
                    # makes the upgrade path self-healing.
                    existing.source = "builtin"
            await db.commit()


# Silence unused-import lint on `sa_delete`; kept imported because
# it's the natural replacement if we ever need bulk deletes in
# ``delete_custom``.
_ = sa_delete
