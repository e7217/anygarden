"""Local room registration policy. Never constructed from federation payloads.

The machine has already staged the agent's configuration. Room execution keeps
that configuration, OAuth and tools; remote execution retains isolated defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .codex import CodexRuntime
from .contracts import Invocation
from .endpoint import validate_endpoint_invocation
from .pi import PiRuntime


@dataclass(frozen=True)
class RoomInvocation(Invocation):
    def validate(self) -> None:
        # Only the local room factory can construct this type. Remote contracts
        # continue rejecting trusted permissions and external workspace writes.
        if self.permission_level == "trusted":
            replace(self, permission_level="standard").validate()
        else:
            super().validate()


def room_environment(invocation: Invocation) -> dict[str, str]:
    validate_endpoint_invocation(invocation)
    return dict(invocation.environment)


class RoomCodexRuntime(CodexRuntime):
    def command(self, invocation, session, output):
        from anygarden_agent.integrations.codex_cli import _resolve_codex_flags

        cmd = super().command(invocation, session, output)
        cmd.remove("--ignore-user-config")
        cmd.remove("--ignore-rules")
        sandbox, approval = _resolve_codex_flags(invocation.permission_level)
        for index, arg in enumerate(cmd):
            if arg.startswith("sandbox_mode="):
                cmd[index] = f"sandbox_mode={sandbox}"
            elif arg.startswith("approval_policy="):
                cmd[index] = f"approval_policy={approval}"
        return cmd

    environment = staticmethod(room_environment)


class RoomPiRuntime(PiRuntime):
    def __init__(self, executable: Path):
        super().__init__(executable)
        self.self_tools_config: Path | None = None

    def command(self, invocation, session, output):
        cmd = super().command(invocation, session, output)
        for flag in (
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-themes",
        ):
            cmd.remove(flag)
        if invocation.permission_level == "restricted":
            cmd += ["--tools", "read,grep,find,ls"]
        elif self.self_tools_config is not None:
            from .pi_self_tools import EXTENSION_PATH

            cmd += ["--extension", str(EXTENSION_PATH)]
        return cmd

    def environment(self, invocation):
        # Pi settings/session locations remain per-agent. Local HOME and staged
        # environment are preserved for its configured skills and tools.
        original = room_environment(invocation)
        safe = {
            k: v
            for k, v in original.items()
            if not k.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_"))
        }
        result = PiRuntime.environment(replace(invocation, environment=safe))
        if "HOME" in original:
            result["HOME"] = original["HOME"]
        from .pi_self_tools import CONFIG_ENV

        result.pop(CONFIG_ENV, None)
        if invocation.permission_level != "restricted":
            if "ANYGARDEN_AGENT_TOKEN" in original:
                result["ANYGARDEN_AGENT_TOKEN"] = original["ANYGARDEN_AGENT_TOKEN"]
            if self.self_tools_config is not None:
                result[CONFIG_ENV] = str(self.self_tools_config)
        return result


def staged_environment() -> dict[str, str]:
    from anygarden_agent import secrets

    from .endpoint import CHILD_KEY, CONFIG_KEY, INPUT_KEY
    from .pi_auth import CONFIG_KEY as PI_CONFIG_KEY
    from .pi_auth import INPUT_KEY as PI_INPUT_KEY

    # Preserve the existing local engine configuration, but never pass the
    # server transport identity or private endpoint descriptor into a tool.
    env = secrets.env_with_secrets()
    for key in list(env):
        if key in {CHILD_KEY, CONFIG_KEY, INPUT_KEY, PI_CONFIG_KEY, PI_INPUT_KEY} or (
            key.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_"))
            and key != "ANYGARDEN_AGENT_TOKEN"  # staged self-MCP credential
        ):
            env.pop(key)
    return env
