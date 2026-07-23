"""Anygarden — lightweight multi-agent chat server."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed distribution metadata, which
    # is generated from pyproject.toml. Avoids the hardcoded string
    # drifting from the real release version (#546).
    __version__ = version("anygarden")
except PackageNotFoundError:  # running from an uninstalled source tree
    __version__ = "0.0.0+dev"
