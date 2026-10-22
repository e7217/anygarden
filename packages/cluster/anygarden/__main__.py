"""Allow running anygarden as ``python -m anygarden`` (same as the ``anygarden`` script).

The node re-executes itself this way after a web update (#927).
"""

from anygarden.cli import dispatch

if __name__ == "__main__":
    dispatch()
