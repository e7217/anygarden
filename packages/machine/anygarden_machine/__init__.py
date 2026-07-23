"""Machine daemon for Anygarden agent orchestration."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the installed distribution metadata. The
    # daemon reports this as ``daemon_version`` on register (#546), so a
    # stale hardcoded string would mislabel every machine in the admin UI.
    __version__ = version("anygarden-machine")
except PackageNotFoundError:  # running from an uninstalled source tree
    __version__ = "0.0.0+dev"
