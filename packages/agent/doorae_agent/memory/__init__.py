"""Cross-engine memory helpers (#237).

The ``compose_memory_block`` function is the single place every engine
adapter consults when it builds its ``system_prompt``. Keeping the
block shape and wording in one module makes the memory / ephemeral
contract visible to agents of all engines without per-engine drift.

File-level documentation lives in ``memory/notes.md`` inside each
agent's directory; the DB carries the ``agents.memory_md`` snapshot
(see plan §3.2 decision 4).
"""

from __future__ import annotations

from doorae_agent.memory.compose import compose_memory_block

__all__ = ["compose_memory_block"]
