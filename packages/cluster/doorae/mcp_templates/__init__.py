"""MCP server template catalog (#124).

Builtin + admin-authored MCP server definitions, per-agent attachments
with Fernet-encrypted credentials, and spawn-time overlay of
engine-native settings files (``.mcp.json`` for claude-code,
``.codex/config.toml`` for codex, ``.gemini/settings.json`` for
gemini-cli).

The package splits along clear seams:

- :mod:`.encryption` — :class:`MCPSecrets`, a thin Fernet wrapper.
  Pure crypto; no DB.
- :mod:`.builtin` — :data:`BUILTIN_TEMPLATES`, the seed list. Pure
  data; no I/O.
- :mod:`.merge` — engine-specific config rendering and manifest
  file merging. Pure functions; tested in isolation.
- :mod:`.service` — :class:`MCPTemplateService`, the DB-facing
  orchestrator used by the API and the lifecycle. Composes the
  three pure layers above.
"""

from doorae.mcp_templates.encryption import MCPSecrets, MCPSecretsUnavailable
from doorae.mcp_templates.service import (
    MCPTemplateService,
    TemplateNotFound,
    TemplateNameConflict,
    TemplateInUse,
    TemplateImmutable,
    InvalidTemplateConfig,
    EngineIncompatible,
    MissingRequiredEnv,
)

__all__ = [
    "MCPSecrets",
    "MCPSecretsUnavailable",
    "MCPTemplateService",
    "TemplateNotFound",
    "TemplateNameConflict",
    "TemplateInUse",
    "TemplateImmutable",
    "InvalidTemplateConfig",
    "EngineIncompatible",
    "MissingRequiredEnv",
]
