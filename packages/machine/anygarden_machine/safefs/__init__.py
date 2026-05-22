"""Filesystem write helpers that refuse to follow symlinks.

Background
----------
``_materialize_agent_dir`` and ``ManifestStore`` previously used
``Path.write_text`` / ``Path.write_bytes`` to stage files under the
agent directory. Those methods open the path with the libc default,
which transparently follows a symlink at the final component and
writes to the link's resolved target. An agent that left a prepared
symlink in a prior session (or a buggy prune that missed one) could
therefore redirect a materialize-time write to a root-owned file
outside the agent root.

This package centralises the symlink-refusing write contract along
with a ``secure_chmod`` helper that pins owner-only permissions in a
platform-appropriate way (POSIX mode bits on Linux/macOS, ACL DACL on
Windows). Callers should never use ``os.chmod`` / ``Path.chmod``
directly because Windows interprets POSIX mode bits as the read-only
attribute only and silently leaves the file world-readable.

Platform dispatch
-----------------
The public API (``safe_write_text``, ``safe_write_bytes``,
``secure_chmod``) is dispatched at import time based on
``sys.platform``. Each backend module is self-contained so platform
specific imports (``ctypes.windll``, ``win32security``) stay isolated.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from ._win import safe_write_bytes, safe_write_text, secure_chmod
else:
    from ._posix import safe_write_bytes, safe_write_text, secure_chmod

__all__ = ["safe_write_bytes", "safe_write_text", "secure_chmod"]
