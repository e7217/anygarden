"""The Pi bridge allowlist tracks the agent tools the server lists (#804).

A tool added to ``tools/list`` without the bridge's allowlist would work for
Codex agents and silently never reach Pi agents, as ``ask_peer`` did.
"""

from anygarden.agent.runtime.execution.pi_self_tools import ALLOWED_TOOL_NAMES

from anygarden.mcp.project_tools import PROJECT_TOOL_SCHEMAS
from anygarden.mcp.tools import TOOL_SCHEMAS


def test_every_listed_agent_tool_is_on_the_pi_bridge_allowlist():
    listed = {tool["name"] for tool in [*TOOL_SCHEMAS, *PROJECT_TOOL_SCHEMAS]}
    assert listed == ALLOWED_TOOL_NAMES
