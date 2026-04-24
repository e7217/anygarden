"""Engine catalog — models and reasoning levels supported per engine."""

from doorae.engines.catalog import (
    ENGINE_CATALOG,
    VIRTUAL_ENGINE_TO_BASE,
    EngineCatalogEntry,
    EngineModel,
    base_engine,
    get_engine_entry,
    is_gateway_engine,
    is_valid_model,
    is_valid_reasoning_effort,
)

__all__ = [
    "ENGINE_CATALOG",
    "VIRTUAL_ENGINE_TO_BASE",
    "EngineCatalogEntry",
    "EngineModel",
    "base_engine",
    "get_engine_entry",
    "is_gateway_engine",
    "is_valid_model",
    "is_valid_reasoning_effort",
]
