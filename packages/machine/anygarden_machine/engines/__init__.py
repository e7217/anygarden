"""Engine lifecycle: detection, latest-version check, and update (#553).

Public surface consolidating the machine-side facts and operations for each
supported engine. See :mod:`~anygarden_machine.engines.registry` for the
single source of truth and :mod:`~anygarden_machine.engines.channels` for the
install-channel abstraction.
"""

from __future__ import annotations

from anygarden_machine.engines.channels import Channel, NpmGlobal, PipVenv
from anygarden_machine.engines.registry import (
    ENGINE_LIFECYCLES,
    DetectSpec,
    EngineLifecycle,
    get_lifecycle,
)

__all__ = [
    "ENGINE_LIFECYCLES",
    "Channel",
    "DetectSpec",
    "EngineLifecycle",
    "NpmGlobal",
    "PipVenv",
    "get_lifecycle",
]
