---
id: 8
title: Two-engine Runtime contract and direct endpoint configuration
status: accepted (contract) — implementation sequenced under #652
date: 2026-09-22
---

# 8. Two-engine Runtime contract and direct endpoint configuration

Supersedes [ADR-004](./004-embedded-litellm-gateway.md) (embedded LiteLLM
gateway) and reverses the OpenHands direction of
[ADR-005](./005-openhands-validation-plan.md). Implements the engine/gateway
narrative decision of [#652](https://github.com/e7217/anygarden/issues/652);
documentation deliverable of [#661](https://github.com/e7217/anygarden/issues/661).

## Context

ADR-004 embedded a LiteLLM subprocess to solve three problems: air-gapped
relaying, centralized usage tracking, and protocol unification across CLI
engines. Since that decision:

- The engine layer is being consolidated onto a single **Runtime protocol**
  (`capabilities()` + `run()`, per `contracts.py`) — supervision, receipts and
  events live in the `ExecutionManager` layer (`LocalExecutionManager`:
  `start/events/cancel/reconcile`), which wraps runtimes. Both target engines
  (`CodexRuntime`, `PiRuntime` — pi-coding-agent 0.85.1, merged in #621)
  implement that protocol. Adding an engine no longer means absorbing a
  foreign SDK.
- The delegation path was validated end-to-end on real machines (federation
  two-node E2E, task #50/#54) and by the approved Track C provider canary
  (task #69) — evidence for the **federation path**.
- The 2026-09-17 incident (an ambient-credential provider probe during
  adapter development) confirmed the design rule that runtimes must never
  inherit ambient credentials: `HOME`/`PI_*` directories and provider keys
  are staged only from the caller-built `Invocation.environment`.

OpenHands never passed its Phase 5 validation ([ADR-005](./005-openhands-validation-plan.md)
was left as an open plan), and the gateway's maintenance weight
(LiteLLM upgrade treadmill, protocol routing we no longer need) stayed with us.

## Decision

1. **Two supported engines**: `codex-cli` and `pi-cli`, enforced by the
   Runtime contract allowlist (`SUPPORTED_ENGINES`). Both follow the same
   supervision, cancellation, timeout and receipt semantics.
2. **Direct endpoint configuration, no embedded gateway.** Local or
   self-hosted models are reached by pointing each engine's CLI at the
   endpoint directly:
   - `pi-cli`: `--provider <id> --model <id>` with the adapter-owned
     `PI_CODING_AGENT_DIR` (verified name on installed pi 0.85.1,
     `dist/config.js:406,422`) pinning `models.json`; `$ENV_VAR`
     interpolation inside `models.json` provides endpoints and keys without
     ambient inheritance. A `provider` is **required** for `pi-cli`.
   - `codex-cli`: OpenAI-compatible `api_base` endpoints are the #660
     acceptance target. Codex speaks the **Responses** protocol — endpoint
     support is judged by Responses support, not Chat Completions
     compatibility. The installed Codex (0.155.1) is newer than the
     Runtime-pinned 0.154.0; version-gate regressions are part of the #660
     acceptance check.
3. **Provider identifier rule**: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` —
   custom names such as `openai-codex` or `my-local` are valid; a leading
   `-` stays rejected so `--provider=value` cannot be misread as an option.
   (Replaces the earlier `isidentifier()` check that rejected hyphenated
   names; the relaxation lands with #653.)
4. **No ambient environment inheritance.** Runtimes receive `HOME`, config
   directories and credentials exclusively from the staged invocation
   environment. Development and regression probes run against mock
   executables; `PI_OFFLINE=1` marks the offline configuration but is not a
   network-blocking guarantee — isolation comes from the fake executables
   and from never staging real credentials. Real provider calls happen only
   through the approved Track C canary process.
5. **Gateway retirement sequence** (#652): usage ledger first (#655 —
   neutral naming, full history preservation), engine removals next
   (#656–#659: gemini-cli, openhands, claude-code and the gateway itself),
   then local-model documentation (#660). Existing agents on removed
   engines are never silently converted: execution is blocked with UI
   transition guidance.

## Explicitly given up

- **Non-OpenAI protocol unification** (e.g. Anthropic `/v1/messages`
  through the same port). Each engine speaks its own CLI's protocol.
- **Gateway-database usage tracking.** Usage moves to the neutral ledger
  (#655) with full history preservation; aggregation semantics are
  regression-tested for equivalence.

## Open decision (not given up)

- **Air-gapped relaying through the embedded gateway.** An offline machine
  can no longer borrow another machine's egress via `/api/v1/llm/*` once
  the gateway retires. Whether a relay is still needed is an open user
  decision — the option is deliberately **not** declared dead.

## Verification status

- Federation path: real-machine two-node E2E and the provider canary are
  completed evidence (tasks #50/#54/#69).
- Room execution path on the new contract: the acceptance regression suite
  (task #94) gates the transition. Today the gateway's remaining consumer
  is the OpenHands engine (#359 reverse-proxy path) — the other engines
  call upstream directly — so the suite must cover the codex/pi direct
  paths plus the removal of the OpenHands consumer.

## References

- [#652](https://github.com/e7217/anygarden/issues/652) — direction and sequencing
- [#653](https://github.com/e7217/anygarden/issues/653) / [#654](https://github.com/e7217/anygarden/issues/654) / [#655](https://github.com/e7217/anygarden/issues/655) — Phase 1 implementation
- [#660](https://github.com/e7217/anygarden/issues/660) — local-model endpoint configuration and its acceptance criteria
- [ADR-004](./004-embedded-litellm-gateway.md), [ADR-005](./005-openhands-validation-plan.md) — superseded
