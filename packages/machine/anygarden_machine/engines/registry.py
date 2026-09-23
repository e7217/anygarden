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

from anygarden_machine.engines import managed
from anygarden_machine.engines.channels import Channel, NpmGlobal, NpmManagedPrefix


@dataclass(frozen=True)
class DetectSpec:
    """How to detect an engine and read its installed version.

    ``mode="binary"``: run ``<binary> --version`` (uses ``binary`` as the
    on-disk name, which may differ from the engine key — e.g. ``claude-code``
    ships the ``claude`` binary).

    ``mode="module"``: import ``import_path`` and read ``version_attr`` — for
    in-process Python SDK engines that have no CLI binary.

    ``mode="managed"`` (#688): run the AnyGarden-managed, version-pinned
    install; ``binary`` on ``PATH`` is used only with the explicit opt-in.
    """

    mode: str  # "binary" | "module" | "managed"
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


ENGINE_LIFECYCLES: dict[str, EngineLifecycle] = {
    # #688 — Pi runs from an AnyGarden-managed prefix pinned to the version
    # the agent adapter was verified against; the global ``pi`` is ignored
    # unless ANYGARDEN_PI_USE_PATH=1.
    "pi-cli": EngineLifecycle(
        engine="pi-cli",
        detect=DetectSpec(mode="managed", binary="pi"),
        channel=NpmManagedPrefix(managed.PI_PINNED_VERSION, managed.managed_prefix),
        # Verified from the installed Pi 0.85.1 package manifest.
        package=managed.PI_PACKAGE,
    ),
    "codex-cli": EngineLifecycle(
        engine="codex-cli",
        detect=DetectSpec(mode="binary", binary="codex"),
        channel=_NPM,
        package="@openai/codex",
    ),
}


def get_lifecycle(engine: str) -> EngineLifecycle | None:
    """Return the lifecycle entry for ``engine`` or ``None`` if unknown.

    ``None`` is the allowlist rejection signal: an engine key absent here is
    not updatable, so the updater refuses it.
    """
    return ENGINE_LIFECYCLES.get(engine)


REMOVED_ENGINES = frozenset(
    {"claude-code", "claude_code", "gemini-cli", "gemini_cli", "openhands"}
)
ENGINE_REMOVED_MESSAGE = "This engine has been removed. Create a codex-cli or pi-cli agent and transfer the settings you want to keep; existing configuration and history are preserved."


def removed_engine_error(engine: str) -> str | None:
    return ENGINE_REMOVED_MESSAGE if engine in REMOVED_ENGINES else None
