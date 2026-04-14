"""Tests for engine auto-detection."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_machine.detector import (
    EngineInfo,
    _detect_binary,
    _detect_env_var,
    _detect_python_import,
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


class TestPythonImportDetection:
    """Test Python import-based engine detection."""

    async def test_detect_python_import_found(self) -> None:
        """Should detect engine when Python import succeeds."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"0.5.0\n", b""))
        mock_proc.returncode = 0

        with (
            patch("doorae_machine.detector.shutil.which", return_value="/usr/bin/python3"),
            patch(
                "doorae_machine.detector.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "doorae_machine.detector.asyncio.wait_for",
                return_value=(b"0.5.0\n", b""),
            ),
        ):
            # Need to also patch wait_for to return proper value
            mock_proc2 = MagicMock()
            mock_proc2.communicate = AsyncMock(return_value=(b"0.5.0\n", b""))
            mock_proc2.returncode = 0

            with patch(
                "doorae_machine.detector.asyncio.create_subprocess_exec",
                return_value=mock_proc2,
            ):
                with patch(
                    "doorae_machine.detector.asyncio.wait_for",
                    return_value=(b"0.5.0\n", b""),
                ):
                    result = await _detect_python_import(
                        "deepagents",
                        "deepagents",
                        'import deepagents; print(deepagents.__version__)',
                    )

        assert result is not None
        assert result.engine == "deepagents"
        assert result.version == "0.5.0"


class TestEnvVarDetection:
    """Test environment variable-based detection."""

    def test_detect_env_var_present(self) -> None:
        """Should detect engine when env var is set."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-1234567890abcdef"}):
            result = _detect_env_var("openai", "OPENAI_API_KEY")

        assert result is not None
        assert result.engine == "openai"
        assert result.version == "key=sk-12345..."
        assert result.path == "env:OPENAI_API_KEY"

    def test_detect_env_var_missing(self) -> None:
        """Should return None when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            result = _detect_env_var("openai", "OPENAI_API_KEY")

        assert result is None
