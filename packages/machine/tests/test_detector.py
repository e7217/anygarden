"""Tests for engine auto-detection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from doorae_machine.detector import _detect_binary


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
