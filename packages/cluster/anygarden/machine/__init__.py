"""Machine daemon for Anygarden agent orchestration."""

# #754 — the daemon ships inside the ``anygarden`` distribution, so the
# ``daemon_version`` it reports on register (#546) is that version.
from anygarden import __version__

__all__ = ["__version__"]
