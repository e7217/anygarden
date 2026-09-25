# Pi native provider authentication

Pi agents use a private `PI_CODING_AGENT_DIR` at `<agent-root>/.pi/agent`.
Logging in as the machine's service user writes to that user's default Pi
directory and does not authenticate an AnyGarden agent. A Pi agent using a
built-in provider such as `zai` needs its own API key registered by an admin.

## Configure an agent

1. Set the agent engine to `pi-cli`, choose a built-in API-key provider, and
   select a model; native Pi agents require an explicit model. For Z.AI Coding Plan, use provider `zai` and the model ID
   supplied by the installed Pi catalog.
2. Open **Agent settings → Overview → Pi provider authentication**. Enter the
   API key and choose **Save key**. The key is write-only; the panel shows its
   provider and revision, never the value.
3. Start or restart the agent. It checks provider, model, and authentication
   with the pinned Pi executable and the agent's actual isolated directory
   before joining a room. A turn repeats the check before contacting the model.

The admin-only API is `GET/PUT/DELETE /api/v1/agents/{agent_id}/pi-auth`.
`PUT` accepts `{"value":"..."}`; replacing a key increments the revision and
restarts the agent. `DELETE` removes the stored key and restarts the agent.
Changing the provider never reuses a key registered for the previous provider:
save a new key before restarting. An agent with a Direct model connection uses
that connection's separate credential settings instead.

## Failure handling

An agent without a registered native key is held unavailable with a Pi auth
setup message. `AUTH_MISSING` means Pi could not find credentials in the agent
environment. `UNKNOWN_PROVIDER` means the selected provider is absent from
Pi's built-in catalog. `AUTH_CHECK_FAILED` means Pi returned an invalid or
unrecognized auth/model result; verify the provider and model with the pinned
Pi version. These are local setup results; upstream provider failures still
use the normal engine error path.

The cluster encrypts the key at rest. It travels over authenticated machine
state and private agent stdin; the machine does not persist the secret payload.
The agent writes only its selected provider entry to a 0600 `auth.json` with a
management marker. Existing unmanaged entries are preserved. A conflicting
entry or modified managed entry blocks startup rather than being overwritten.
After a cold machine restart, fresh state from the cluster is needed before a
native Pi agent can start.

Pi tools run under the agent's OS user and Pi has no OS sandbox. A process with
that user's filesystem access can read its `auth.json`; use separate OS users
when stronger isolation is required. OAuth and extension-registered provider
credentials are outside this API-key enrollment path.
