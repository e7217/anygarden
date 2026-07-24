"""Engine lifecycle registry (#553).

The single source of truth for *how each engine is detected and updated*.
Today that knowledge is scattered across three packages and four sites
(agent adapter registration, machine detector, cluster catalog, per-adapter
hint strings). This registry consolidates the machine-side lifecycle facts —
detection spec, install channel, and package identifier — into one entry per
engine.

Adding an engine:
  * usual case → append one :class:`EngineLifecycle` entry here;
  * only when the install channel is genuinely new → also add one
    :class:`~anygarden_machine.engines.channels.Channel` implementation.

The ``package`` field is the **allowlist source** for updates: the server
sends only an engine key, and the updater resolves it to a package name here
(never trusting a server-provided string).
"""

from __future__ import annotations

from dataclasses import dataclass

from anygarden_machine.engines.channels import Channel, NpmGlobal, PipVenv


@dataclass(frozen=True)
class DetectSpec:
    """How to detect an engine and read its installed version.

    ``mode="binary"``: run ``<binary> --version`` (uses ``binary`` as the
    on-disk name, which may differ from the engine key — e.g. ``claude-code``
    ships the ``claude`` binary).

    ``mode="module"``: import ``import_path`` and read ``version_attr`` — for
    in-process Python SDK engines that have no CLI binary.
    """

    mode: str  # "binary" | "module"
    binary: str | None = None
    import_path: str | None = None
    version_attr: str | None = None


@dataclass(frozen=True)
class EngineLifecycle:
    """Machine-side lifecycle facts for one engine."""

    engine: str
    detect: DetectSpec
    channel: Channel
    package: str


# Channel instances are stateless; share one per kind.
_NPM = NpmGlobal()
_PIP = PipVenv()


ENGINE_LIFECYCLES: dict[str, EngineLifecycle] = {
    "claude-code": EngineLifecycle(
        engine="claude-code",
        detect=DetectSpec(mode="binary", binary="claude"),
        channel=_NPM,
        # NOTE: assumed npm package; verify on a live machine (plan Phase F).
        package="@anthropic-ai/claude-code",
    ),
    "codex-cli": EngineLifecycle(
        engine="codex-cli",
        detect=DetectSpec(mode="binary", binary="codex"),
        channel=_NPM,
        package="@openai/codex",
    ),
    "gemini-cli": EngineLifecycle(
        engine="gemini-cli",
        detect=DetectSpec(mode="binary", binary="gemini"),
        channel=_NPM,
        package="@google/gemini-cli",
    ),
    "openhands": EngineLifecycle(
        engine="openhands",
        detect=DetectSpec(
            mode="module",
            import_path="openhands.sdk",
            version_attr="__version__",
        ),
        channel=_PIP,
        package="openhands-sdk",
    ),
}


def get_lifecycle(engine: str) -> EngineLifecycle | None:
    """Return the lifecycle entry for ``engine`` or ``None`` if unknown.

    ``None`` is the allowlist rejection signal: an engine key absent here is
    not updatable, so the updater refuses it.
    """
    return ENGINE_LIFECYCLES.get(engine)
