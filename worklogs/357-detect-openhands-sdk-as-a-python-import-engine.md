# fix(machine): detect openhands SDK as a Python-import engine (#357)

- Commit: `6b89386`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #357

## Situation

#355 added OpenHands V1 SDK as a fourth engine end-to-end —
adapter, MCP wiring, skills awareness, DelegateTool, full provider
catalog, validation plan, deprecation infrastructure — but
testers reported the agent-creation UI's engine dropdown still
only showed claude-code / codex / gemini-cli. The catalog had
openhands; the adapter loaded; the CLI accepted `--engine
openhands`; but the UI was blind to it.

## Task

Find the gap between "catalog has it" and "UI shows it", and
close it. Constraints:

- The fix has to slot into the existing detector pipeline so the
  daemon's lifecycle (advertise → cluster persists in
  `machine_engines` → API serves `engines/available` → UI
  populates the dropdown) keeps working unchanged for the three
  CLI engines.
- Detection must not crash the daemon on any conceivable bad
  state (missing module, partial install, version-attribute
  rename, namespace package). Same robustness contract as the
  existing binary path.
- Tests must lock the user-visible regression: openhands appears
  in `detect_engines()` output when the SDK is importable, and
  goes missing when it isn't.

## Action

The cluster's `/api/v1/agents/engines/available` endpoint reads
from the `machine_engines` table — what the daemon's detector
advertises. The detector at
`packages/machine/doorae_machine/detector.py` only looped over
`BINARY_ENGINES` via `shutil.which`. OpenHands ships as
`openhands.sdk` (in-process Python SDK), so it never surfaced.

- `packages/machine/doorae_machine/detector.py`:
  - New `PYTHON_MODULE_ENGINES: list[tuple[str, str, str]]` —
    `(engine_name, import_path, version_attr)`. First entry
    `("openhands", "openhands.sdk", "__version__")`.
  - `_detect_python_module(name, import_path, version_attr)`:
    attempts `importlib.import_module(import_path)`; on
    `ImportError`, returns `None` (the expected miss path); on
    any other exception (RuntimeError, partial import, broken
    metadata) logs `python_module_detection_failed` and returns
    `None` — same defensive shape the binary path uses for
    OSError. Reads `__version__` with a `getattr(default=None)`
    fallback, coerces non-string versions to `str`, and falls
    back to `__file__` (or import path for namespace packages)
    so `EngineInfo.path` always carries something locatable for
    operator debugging.
  - `detect_engines()` now runs binary detection concurrently as
    before, then loops `PYTHON_MODULE_ENGINES` synchronously
    (importlib doesn't gain from `asyncio.gather` and threading
    fights the import lock).
- `packages/machine/tests/test_detector.py`:
  - `TestPythonModuleDetection` (4 tests):
    - `test_module_present_returns_engine_info` — install a fake
      module via `sys.modules`, expect EngineInfo with version
      and resolved file path.
    - `test_module_missing_returns_none` — real ImportError
      against a guaranteed-nonexistent module name.
    - `test_module_without_version_falls_back` — fake module with
      no `__version__`; advertises with `version="unknown"`. The
      operator sees the fallback in their machine row instead of
      the engine disappearing.
    - `test_unexpected_import_error_returns_none` — patch
      `importlib.import_module` to raise `RuntimeError`; detector
      logs and reports absent.
  - `TestDetectEnginesIncludesPythonModules` (2 tests):
    - `test_openhands_appears_when_sdk_installed` — locks the
      user-visible regression. With `openhands.sdk` stubbed in
      `sys.modules` and binary detection forced to no-op,
      `detect_engines()` returns an EngineInfo with
      `engine="openhands"`. This is the test that fails on
      pre-#357 main and passes here.
    - `test_openhands_omitted_when_sdk_missing` — ensure detection
      actually gates on import; a stale entry from class-level
      state would surface here.

## Decisions

The plan in #357 issue body landed on Python-module detection
out of three options:

- **Static "always advertise openhands" entry in the detector's
  output** — simplest possible fix, but lies to the cluster: a
  machine without `openhands-sdk` installed would still claim
  support, and `/api/v1/agents/engines/available` would surface
  an engine the agent process can't actually run. Rejected
  because the silent-degradation shape is the same one #292
  cited when removing dead adapters.
- **Detect via a CLI bin shim** (e.g. ship a `doorae-openhands`
  command in `pyproject.toml` scripts that just exits 0). Would
  reuse the existing binary path but adds a shim layer that has
  no operational value — the engine isn't actually a CLI; the
  shim would just be a detection puppet. Rejected because the
  shim adds maintenance surface (its own version logic, install
  ordering with the SDK).
- **Python-module detection via `importlib.import_module`**
  (chosen). Honest signal: the detector advertises iff the agent
  can actually load the SDK. Same defensive shape as the binary
  path (graceful degradation on any exception). Adds a small
  list of `(name, path, version_attr)` tuples that future
  in-process engines (LangChain, etc.) can append to — same
  pattern, no new abstraction.

What tipped the scale: the user-visible bug is "UI says no
openhands". The fix has to reflect actual machine capability,
not announce capability the machine may not have. Module import
is the cheapest way to verify "this venv can run the engine in-
process".

What I didn't do:

- Cache the import result. `importlib.import_module` returns the
  cached module on the second call automatically; first detection
  pays the import cost (~few ms cold), subsequent advertise
  cycles are free. Adding a separate cache layer would be
  premature optimisation for a 3–10 ms operation that runs once
  per heartbeat at most.
- Walk `pyproject.toml` deps to predict imports. The actual
  available SDK depends on the agent venv, not the package
  metadata; a deps-walk could falsely advertise an engine the
  installed wheel hasn't fetched.
- Validate the SDK shape (e.g. assert `LLM` / `Agent` /
  `Conversation` exist on the imported module). Out of scope —
  the adapter's `start()` already does that and degrades to
  no-op if classes are missing. Adding a second validation here
  duplicates surface area.

Assumptions worth flagging if they break later:
- `openhands.sdk` is the canonical import path for OpenHands V1
  SDK. The package ships ``LLM``, ``Agent``, ``Conversation`` at
  this path per the SDK's public docs. If a future major rev
  reshuffles to a different path (`openhands.agent_sdk`?), the
  `PYTHON_MODULE_ENGINES` entry needs updating in lockstep with
  the adapter's import.
- `__version__` on the top-level module. Conventional but not
  guaranteed; the `version_attr` field is parameterised and the
  fallback is `"unknown"`, so a missing attribute degrades
  gracefully.
- Synchronous import is fast enough. If a future SDK pulls in a
  multi-second import chain (litellm sometimes does on
  cold-start), the detector's overall latency grows. The fix
  would be parallelising the Python pass with `asyncio.to_thread`,
  but that's a refactor for when the cost becomes measurable.

## Result

The agent-creation UI now lists openhands alongside the three CLI
engines on any machine where `openhands-sdk` is installed in the
doorae-agent venv. After deploying this commit and restarting the
machine daemon, the dropdown populates correctly via the existing
`engines/available` → `machine_engines` → detector advertise
path.

Coverage: 9 / 9 detector tests pass (6 new for #357 on top of the
3 existing binary tests). Full machine suite stays green at 346
tests (was 340 pre-#357, +6 new).

This is a follow-up to #355 — the migration plan called for
"flag legacy on CLI engines after Phase 5 validation" but never
spelled out the mechanical advertisement step. Worth folding the
Python-module detection pattern into the next migration plan
template so a similar in-process engine doesn't repeat the same
silent gap.
