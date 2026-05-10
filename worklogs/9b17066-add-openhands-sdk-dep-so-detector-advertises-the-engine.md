# fix(machine): add openhands-sdk dep so detector advertises the engine

- Commit: `9b17066` (9b17066...)
- Author: Changyong Um
- Date: 2026-05-10T15:50:12+09:00
- PR: —

## Situation

The agent-creation UI was silently hiding OpenHands as a selectable
engine even though ``oh-agent04`` (already created previously) was
running the engine end-to-end. The frontend lists engines from
``GET /api/v1/agents/engines/available``, which the backend (in
``packages/cluster/doorae/api/v1/agents.py``) computes by joining
``machine_engines`` against online ``machines``. Inspection showed
``machine_engines`` carried only three rows for the active machine:
``claude-code``, ``codex``, ``gemini-cli``. No ``openhands`` row.

Running the detector manually
(``doorae_machine.detector.detect_engines``) reproduced the gap:

    Detected: [('claude-code', ...), ('codex', ...), ('gemini-cli', ...)]

Probing the import the detector uses
(``importlib.import_module('openhands.sdk')``) failed with
``ModuleNotFoundError`` inside the workspace-root venv that the
machine process actually runs in. ``openhands-sdk`` was however
installed in the per-package agent venv and in the ``uvx`` cache
venv that ``spawner.py`` uses for the agent runtime — but those are
different venvs, and the detector only sees its own ``sys.path``.
Result: silent ``ImportError`` → ``_detect_python_module`` returns
``None`` → engine never advertised → UI hides it.

## Task

- Make ``import openhands.sdk`` succeed inside the machine process'
  own venv so ``_detect_python_module`` can succeed.
- Keep the change minimal and explanatory — adding a heavyweight
  SDK to the machine deps is asymmetric with the other engines
  (binary detection via ``shutil.which`` needs no Python deps), so
  the rationale must be visible in code review.
- No existing tests need changing; the detector test
  ``test_openhands_appears_when_sdk_installed`` already locks the
  contract — it was passing because of its in-test stub, not because
  the runtime venv could satisfy the import.

## Action

- ``packages/machine/pyproject.toml`` — added
  ``"openhands-sdk>=1.21"`` to ``[project] dependencies``, with a
  9-line comment block explaining:
  - why this SDK lives in the machine deps even though the agent
    runtime (in a separate ``uvx`` cache venv) is what consumes it,
  - that binary engines (claude-code/codex/gemini-cli) need no
    equivalent dep because they're detected via ``shutil.which``,
  - the failure mode that motivated the change (silent
    ``ImportError`` → engine hidden from UI).
- After ``uv sync --all-packages`` the workspace-root ``.venv``
  picks up the SDK; the manual ``detect_engines()`` run now returns
  4 entries including ``('openhands', '1.21.1')``.

## Decisions

- **Add the SDK to ``packages/machine`` deps vs. an alternative
  detection mechanism** — three options weighed:
  1. *Probe by querying ``uvx doorae-agent --check-engines``*: most
     accurate (each engine self-declares from the same venv it'll
     actually run in), but requires a new CLI subcommand and a
     non-trivial subprocess hop on every detection. Future work,
     not blocking.
  2. *Binary-fallback for openhands*: ship a thin ``openhands``
     stub on PATH so the binary detector finds it. Adds packaging
     surface and is dishonest — the engine isn't really a binary.
  3. *Add the SDK to machine deps*: simplest, most explicit. Costs
     one heavy dep per Python-module engine, but there's only one
     such engine today and the symmetry break is documented in a
     comment. Picked this.
- **Trigger to revisit option 1**: when a second in-process Python
  engine appears (e.g. a future ``deep-agents`` or a hosted SDK
  variant), the per-engine SDK dep accumulation in
  ``packages/machine`` will start to look ugly. At that point the
  ``--check-engines`` subcommand becomes the better factoring.
- **Why no test change**: the existing
  ``test_openhands_appears_when_sdk_installed`` already covers the
  contract via a fake module injected into ``sys.modules``. The
  bug was an environment issue (machine venv didn't satisfy the
  import) that no Python test could expose because pytest runs in
  whatever venv invokes it. A test added today would be
  tautological. The actual lock is the dep itself plus the
  pre-existing detector test.

## Result

- ``detect_engines()`` returns 4 entries including
  ``('openhands', '1.21.1')`` — confirmed via direct invocation.
- After a machine restart, the cluster's register handler will
  replace the ``machine_engines`` rows with the new 4-engine set,
  the ``/engines/available`` endpoint will surface ``openhands``,
  and the agent-creation dialog will show OpenHands alongside the
  three CLI engines.
- ``packages/machine`` test suite: 346 passed, 2 skipped (no
  changes).
- Operational note: existing ``oh-agent04`` keeps working unchanged
  — it was already running on the engine; the change only affects
  the create-agent UI's visibility filter.
