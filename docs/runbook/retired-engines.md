# Moving from retired engines

Claude Code (`claude-code`), Gemini CLI (`gemini-cli`), and OpenHands (`openhands`) execution adapters have been removed. Codex (`codex-cli`) and Pi (`pi-cli`) run through the Python agent runtime. TypeScript retains its room transport, protocol, routing, and coordination client; it does not execute these engines.

Existing agent rows, profiles, files, role instructions, permissions, and conversation history are preserved. Retired agents are excluded from available engine choices. Starting them returns migration guidance; automatic starts and reconnect manifests cannot launch them. The machine refuses old manifests before writing files or spawning a process. There is no automatic engine or runtime conversion.

To move an existing retired agent:

1. Keep the original agent and its files/history for reference.
2. Create a new Python Codex or Pi agent. Select an explicit provider for Pi and the intended model.
3. Copy the role, permissions, skills, MCP settings, and credentials you intend to retain using the existing configuration controls. Review engine-specific settings before copying; native session formats are not interchangeable.
4. Join the new agent to the intended rooms. Validate its configuration before authorizing a model call.

For an existing Codex/Pi agent with `runtime=typescript`, explicitly change the runtime to `python` in the agent configuration API (`PUT /api/v1/agents/{id}` with `{"runtime_set":true,"runtime":"python"}`). This follows the existing generation/restart behavior. Setting a Codex/Pi agent to TypeScript is rejected.

The agent wheel no longer depends on Claude/OpenHands SDKs or their agent-only FastAPI constraint. The cluster still requires FastAPI. Shared private provider-key delivery, Codex/Pi endpoint handling, OAuth settings, and the separate LLM gateway service/API remain in place. This change does not migrate or delete gateway configuration or usage history.

Validation for this change uses fake executables, temporary SQLite databases, and loopback HTTP/WS. It does not establish real-provider compatibility, perform deployment, or modify an operating database.
