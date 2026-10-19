# feat(cli): print where to open the UI once the node is up (#902)

- Commit: `cc1ba8a` (cc1ba8aa9e75026ba748ef2ed6430ed5a137a167)
- Author: Changyong Um
- Date: 2026-10-19T...:...:...+09:00
- PR: #902

## Situation

`anygarden start` (the `dispatch start` subcommand introduced for integrated
node mode) printed uvicorn's bind address (`http://0.0.0.0:8000`) and then
immediately switched to JSON-structured logs.  A first-time user reading the
terminal had no human-readable URL to paste into a browser, and
`http://0.0.0.0:8000` does not resolve on most operating systems anyway.
The bind address is also meaningless on Docker where port-mapping decouples
the container bind from the host URL.

## Task

- Print a human-readable browser URL immediately after the uvicorn startup
  completes, mapped to `localhost` for loopback and wildcard binds.
- Support all common bind-address forms: IPv4 loopback, IPv4 wildcard,
  IPv6 loopback (`::1`), IPv6 wildcard (`::`), explicit hostname, and
  bare IPv6 addresses (which require bracket notation in URLs).
- Add a pure-Python unit test covering all cases before wiring it into the
  server lifecycle.

## Action

**`packages/cluster/anygarden/cli.py`**

- Added `_LOOPBACK_OR_ANY` set (`{"127.0.0.1", "0.0.0.0", "::", "::1",
  "localhost", ""}`) and a `_browser_url(host, port) -> str` helper
  (lines ~354–363) that maps any member to `localhost` and wraps bare
  IPv6 in brackets.
- In `start_node`, computed `browser_url = _browser_url(config.host,
  config.port)` before starting the server.
- Defined a local `_NodeServer(uvicorn.Server)` subclass that overrides
  `startup(sockets=None)` to call `await super().startup(sockets=sockets)`
  and then, if `self.started`, emits
  `click.echo(f"Open {browser_url} in your browser.")` (line ~527-528).
  This fires right after uvicorn prints its own "running on …" line and
  before the JSON log stream starts.

**`packages/cluster/tests/test_cli_browser_url.py`** (new)

- Seven parametrised cases covering `127.0.0.1`, `0.0.0.0`, `::`,
  `localhost`, a LAN IP (`192.168.1.20`), a hostname, and a full
  IPv6 address (`fd00::5`).

## Decisions

**Why subclass `uvicorn.Server` instead of printing before `server.run()`?**

The `startup` hook fires after uvicorn has successfully bound the socket and
is about to accept connections, which means:
1. The URL is guaranteed reachable when the message appears (no race with a
   port-already-in-use error).
2. It lands directly under uvicorn's own `Running on …` line in the terminal,
   giving the user a natural reading order: uvicorn confirms the bind, then
   the node confirms where to open it.

Printing before `server.run()` was rejected because at that point the socket
is not yet bound — a misconfigured port would still show the URL before
failing, which is confusing.

**Why map `""` (empty string) to `localhost`?**

Docker compose and some CI environments export `HOST=""`. Pydantic's
`AnygardenSettings` has a default, but the empty-string case must be handled
defensively at the URL-building layer too, since an empty-string host in a
URL is syntactically invalid.

**Why `_LOOPBACK_OR_ANY` as a module-level frozenset instead of inline?**

The set is referenced by `_browser_url` (used in `start_node`) and tested
in isolation in `test_cli_browser_url.py`. Keeping it named and at module
level makes the intent explicit and avoids re-creating the set on every call.

**Alternatives rejected:**

- *Always print `http://localhost:{port}`* — incorrect for non-loopback binds
  like a LAN IP or hostname; a user specifying `--host 192.168.1.20` expects
  the correct URL, not `localhost`.
- *Print inside the existing `click.echo(f"Starting AnyGarden node …")` call*
  — that fires before the socket is bound; a port collision would show the URL
  then exit with an error.
- *Show the URL in the existing `deprecated_server_main` / `main` path as well*
  — those paths call `uvicorn.run()` directly and have no post-startup hook
  without a similar subclass; deferred to a separate issue since `anygarden
  start` is the recommended entry point going forward.

**Assumption:** `uvicorn.Server.startup` signature is stable across the
`0.x` range. Verified by inspection: `uvicorn 0.52.1` — signature is
`(self, sockets: list[socket.socket] | None = None) -> None`, confirming the
`sockets` kwarg is forwarded correctly.

## Result

- `anygarden start` now prints `Open http://localhost:8000 in your browser.`
  (or the appropriate URL for non-loopback binds) immediately after the node
  is ready to accept connections.
- Seven unit tests in `test_cli_browser_url.py` all pass, covering loopback,
  wildcard, IPv4 LAN, hostname, and IPv6 cases.
- The change is confined to `start_node`; the legacy `main`/`deprecated_server_main`
  paths are unaffected.
- Step 2 of 7 in issue #902 (onboarding first-run flow); remaining steps cover
  the engine-rescan API, auth failure notices, first-account tab selection,
  room pre-selection, and README updates.
