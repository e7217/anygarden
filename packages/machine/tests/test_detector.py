"""Tests for engine auto-detection."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from doorae_machine.detector import (
    _detect_binary,
    _detect_python_module,
    detect_engines,
)


class TestBinaryDetection:
    """Test binary engine detection via --version."""

    async def test_detect_binary_found(self) -> None:
        """Should detect engine when binary exists and returns version."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"claude-code 1.2.3\n", b"")
        )
        mock_proc.returncode = 0

        with (
            patch("doorae_machine.detector.shutil.which", return_value="/usr/bin/claude-code"),
            patch(
                "doorae_machine.detector.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            result = await _detect_binary("claude-code", "claude-code")

        assert result is not None
        assert result.engine == "claude-code"
        assert result.version == "claude-code 1.2.3"
        assert result.path == "/usr/bin/claude-code"

    async def test_detect_binary_not_found(self) -> None:
        """Should return None when binary is not on PATH."""
        with patch("doorae_machine.detector.shutil.which", return_value=None):
            result = await _detect_binary("claude-code", "claude-code")

        assert result is None

    async def test_detect_binary_timeout(self) -> None:
        """Should return None when version command times out."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        with (
            patch("doorae_machine.detector.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "doorae_machine.detector.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "doorae_machine.detector.asyncio.wait_for",
                side_effect=asyncio.TimeoutError(),
            ),
        ):
            result = await _detect_binary("codex", "codex")

        assert result is None


# ── Issue #357 — Python module detection ────────────────────────────


class TestPythonModuleDetection:
    """In-process SDK engines (e.g. openhands) advertise via import.

    The cluster's ``/api/v1/agents/engines/available`` endpoint serves
    the agent-creation UI from ``machine_engines`` — what this
    detector advertises. An engine missing here silently fails to
    appear in the dropdown even if the catalog has it (the bug #357
    fixes for openhands).
    """

    def test_module_present_returns_engine_info(self) -> None:
        fake_module = types.ModuleType("fake_openhands_sdk")
        fake_module.__version__ = "1.21.1"
        fake_module.__file__ = "/site-packages/fake_openhands_sdk/__init__.py"

        with patch.dict(sys.modules, {"fake_openhands_sdk": fake_module}):
            info = _detect_python_module(
                "openhands", "fake_openhands_sdk", "__version__"
            )

        assert info is not None
        assert info.engine == "openhands"
        assert info.version == "1.21.1"
        assert info.path == "/site-packages/fake_openhands_sdk/__init__.py"

    def test_module_missing_returns_none(self) -> None:
        # Real ImportError — module name guaranteed not to exist.
        info = _detect_python_module(
            "openhands",
            "definitely_not_a_real_module_xyzzy_357",
            "__version__",
        )
        assert info is None

    def test_module_without_version_falls_back(self) -> None:
        """No ``__version__`` attr → ``"unknown"``, still detected.

        SDK upgrades sometimes drop or rename the version attribute;
        the detector should still advertise the engine so the UI
        keeps showing it. Operators see "version=unknown" in the
        machine row, which is a clearer signal than the engine
        disappearing from the dropdown.
        """
        fake_module = types.ModuleType("fake_no_version")
        fake_module.__file__ = "/x/__init__.py"

        with patch.dict(sys.modules, {"fake_no_version": fake_module}):
            info = _detect_python_module(
                "fake", "fake_no_version", "__version__"
            )

        assert info is not None
        assert info.version == "unknown"

    def test_unexpected_import_error_returns_none(self) -> None:
        """A non-ImportError exception during import → degrade gracefully.

        Same shape as the binary path's OSError handling: the
        detector must not crash the daemon on a bad SDK install. The
        engine is reported absent and a warning is logged.
        """
        with patch(
            "doorae_machine.detector.importlib.import_module",
            side_effect=RuntimeError("circular import"),
        ):
            info = _detect_python_module("x", "fake_path", "__version__")
        assert info is None


class TestDetectEnginesIncludesPythonModules:
    """End-to-end: detect_engines() merges binary + Python sources."""

    async def test_openhands_appears_when_sdk_installed(self) -> None:
        """If ``openhands.sdk`` imports, the result lists ``openhands``.

        This is the user-visible regression #357 fixes: openhands in
        the catalog but invisible in the agent-creation UI because
        the detector never advertised it.
        """
        # Stub openhands.sdk so detection succeeds without requiring
        # the real package in test environments.
        fake_sdk = types.ModuleType("openhands.sdk")
        fake_sdk.__version__ = "1.21.0"
        fake_sdk.__file__ = "/x/openhands/sdk/__init__.py"
        fake_pkg = types.ModuleType("openhands")
        fake_pkg.sdk = fake_sdk  # type: ignore[attr-defined]

        with (
            patch.dict(
                sys.modules,
                {"openhands": fake_pkg, "openhands.sdk": fake_sdk},
            ),
            # Force binary detection to no-op so the assertion focuses
            # on the openhands path.
            patch("doorae_machine.detector.shutil.which", return_value=None),
        ):
            result = await detect_engines()

        engine_names = [e.engine for e in result.engines]
        assert "openhands" in engine_names
        oh = next(e for e in result.engines if e.engine == "openhands")
        assert oh.version == "1.21.0"

    async def test_openhands_omitted_when_sdk_missing(self) -> None:
        """Without openhands.sdk in sys.modules + no binary, result is empty.

        Confirms detection actually gates on import — a stale entry
        could sneak in via class-level state otherwise.
        """
        # Ensure neither openhands nor openhands.sdk is cached.
        with (
            patch.dict(sys.modules, {}, clear=False),
            patch("doorae_machine.detector.shutil.which", return_value=None),
            patch(
                "doorae_machine.detector.importlib.import_module",
                side_effect=ImportError("simulated missing SDK"),
            ),
        ):
            sys.modules.pop("openhands", None)
            sys.modules.pop("openhands.sdk", None)
            result = await detect_engines()

        engine_names = [e.engine for e in result.engines]
        assert "openhands" not in engine_names
