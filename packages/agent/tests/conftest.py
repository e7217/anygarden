"""Shared test fixtures for anygarden-agent."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    """Record ``@pytest.mark.req`` IDs as a JUnit ``req`` property (#781).

    ``scripts/req_report.py`` reads the property back from the JUnit XML
    to report each requirement's pass/fail state.
    """
    for item in items:
        ids = sorted({i for m in item.iter_markers("req") for i in m.args})
        if ids:
            item.user_properties.append(("req", ",".join(ids)))
