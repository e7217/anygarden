# Direct model endpoints (Codex and Pi)

An administrator can connect a Pi agent to a local or custom HTTP model server when creating it (**Machines → New Agent → Pi → Connection type → Direct model server**) or later from **Agent settings → Model connection**. New Codex agents use the Codex CLI model connection in the normal UI. Existing Codex direct connections remain editable in **Model connection** and can be disabled there.

For Pi's built-in providers such as `zai`, use [Pi native provider authentication](pi-native-auth.md) instead.

## At creation (#685)

Select Pi, then **Direct model server**, and enter the base URL (for example `http://10.0.0.5:8000/v1`). Pi defaults to **Chat Completions**. The provider name is entered once and is used only as this agent's name for the server. Click **Load models** to fill the model suggestions from `<base_url>/models`, then pick a model; a model is required for a direct endpoint. The API and runtime still support previously configured Codex direct connections.

Without authentication, the endpoint is saved in the same request that creates the agent, so the first start already uses it. With **API key**, the agent is created first, then the key is stored through the write-only credential endpoint and bound to the endpoint (one extra restart). The key never appears in the create request.

**Load models** runs from the AnyGarden server (`POST /api/v1/engine-endpoints/models`, admin only) and is labelled *reachable from the AnyGarden server*. The agent calls the model from its machine, so a successful probe doesn't guarantee that the machine can reach the URL, and the reverse is also true. The probe is GET-only, doesn't follow redirects, ignores proxy environment variables, times out after 5 s, reads at most 1 MiB, and returns fixed error messages without the key or the upstream body. The settings panel can probe with the agent's stored credential by reference, and it warns when the configured model ID isn't in the served list.

## In agent settings

1. Connect an updated machine that advertises `direct_endpoint_v1` and supports the agent's engine. A configured agent's currently assigned machine must support this capability; the form refuses incompatible placement before saving. Open **Agent settings → Model connection**; Overview shows a short summary of the active connection.
2. Enter an explicit provider ID, model ID and base URL (for example `http://localhost:8000/v1`). `localhost` refers to the machine running the agent, not the web browser or cluster server. Use a URL reachable from that machine.
3. Choose **Responses** for Codex. Pi supports **Responses** and **Chat Completions**. The server must implement the chosen protocol; OpenAI-compatible Chat Completions alone is insufficient for Codex.
4. Choose **No authentication**, or store a new API key and select its stored reference. Click **Apply connection** to save the provider, model, URL, protocol and credential reference together. Configuration changes restart the agent. Pi switches back to a native provider through the **Connection type** selector, which requires a native provider, model and matching native key. A key save failure attempts to restore the previous direct connection and displays the current state for retry. Codex's **Disable direct connection** action clears the direct provider/model override and returns it to the CLI default.

The default engine connection remains available when direct connection is disabled. Direct endpoint settings are local administrator policy; remote delegation messages cannot select an endpoint or supply credentials.

## Credentials and restart behavior

Keys are encrypted with the cluster's existing `ANYGARDEN_MCP_SECRETS_KEY` (Fernet) and bound to one agent and one engine. Keep that encryption key stable across cluster restarts. API/UI responses expose only labels, references and revisions. A stored value cannot be read back. Replace a selected credential to rotate it; the revision and agent generation change. A selected credential cannot be deleted until the endpoint is disabled or changed to another reference.

Do not include credentials in the base URL: user information, query strings and fragments are rejected. Model keys travel only in authenticated server-to-machine state and the private agent stdin payload, then the runtime child's environment. They are excluded from CLI arguments and generated configuration files.

The machine persists only an `endpoint_configured` marker, not the endpoint secret payload. A cold machine restart without fresh configuration refuses execution before creating a process. Reconnecting to the cluster and receiving a new authenticated state restores the in-memory configuration so execution can resume. Missing, corrupt or wrong-engine credential references fail closed; there is no fallback to host credentials.

## Runtime-specific behavior

Codex receives a dedicated `ag_direct` provider through structured command-line configuration overrides. Only Responses is supported. Keyless connections omit the environment-key reference.

Pi writes a managed `models.json` under the agent's isolated `runtime_home/.pi/agent` directory. The file references `AG_DIRECT_API_KEY`, never its value. Configuring a new provider replaces the previous managed provider entry; disabling the direct endpoint removes that managed file. An existing models.json without an AnyGarden ownership marker blocks activation and remains untouched. A managed file changed outside AnyGarden blocks activation, changes and removal; resolve the conflict explicitly before retrying. Agent directories and session directories remain separate from the CLI installation and other agents.

Pi requires a nonempty key for model selection even when a local server needs no authentication. In that mode AnyGarden supplies the nonsecret placeholder `ag-keyless-local`; a server that accepts keyless Pi requests must tolerate this placeholder bearer value. This is distinct from a selected credential that failed to load, which is always an error.

Pi's saved `auth.json` can take precedence over `models.json`. If that isolated file contains authentication for the selected direct provider, AnyGarden refuses execution with a conflict error. Authentication for other providers is preserved and does not block execution. Existing auth files are never edited or deleted by endpoint setup; resolve the selected-provider login conflict explicitly before retrying.

## Schema and validation

Migration `075_direct_endpoints` follows `074_usage_ledger`, which follows `073_agent_provider`. It adds nullable endpoint fields and the encrypted credential table without changing existing agent providers or usage rows. Downgrading removes endpoint settings and credential storage; back up configuration first if a downgrade is required.

Automated acceptance uses fake CLI binaries and a loopback HTTP server for protocol path, selected model and authentication checks, plus configuration, encryption, migration, restart and UI regressions. It does not demonstrate compatibility with a real model provider or authorize a provider call.
