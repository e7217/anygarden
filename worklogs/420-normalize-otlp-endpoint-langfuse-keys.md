# fix(observability): normalize OTLP endpoint + build Langfuse auth from keys (#420 follow-up)

- Commit: `ff8e166` (ff8e166 on branch fix/otel-endpoint-normalize-langfuse-keys)
- Author: Changyong Um
- Date: 2026-06-09
- PR: — (follow-up to #420; no dedicated issue)

## Situation

While wiring the #420 OTEL → Langfuse tracing against a live self-hosted
Langfuse, two operator-facing rough edges surfaced. (1) The OTLP/HTTP exporter
uses an explicit `endpoint=` argument *verbatim* — the `/v1/traces` auto-append
only applies to the `OTEL_EXPORTER_OTLP_ENDPOINT` base env var, which anygarden
doesn't use — so passing a Langfuse base URL (`.../api/public/otel`) silently
POSTed to the wrong path and 404'd; worse, the `config.py` docstring wrongly
claimed the exporter appends the path. (2) Langfuse Basic auth had to be
hand-encoded as `base64(public:secret)` into `ANYGARDEN_OTEL_OTLP_HEADERS`.

## Task

- Accept either a base URL or a full traces path for the OTLP endpoint.
- Let operators supply Langfuse public/secret keys directly instead of
  hand-encoding the Authorization header.
- Correct the misleading docstring and document the OTEL env block.
- Constraint: keep the raw-header override path working and unchanged for
  non-Langfuse / generic OTLP collectors.

## Action

- `observability/tracing.py`: added `normalize_otlp_endpoint()` (append
  `/v1/traces` unless already present, after stripping a trailing slash) and
  `resolve_otlp_headers(config)` (raw `otel_otlp_headers` wins; else build
  `Authorization: Basic base64(public:secret)` from the Langfuse keys; else
  `{}`). `setup_tracing` now calls both and logs the normalized endpoint.
- `config.py`: corrected the `otel_otlp_endpoint` docstring; added
  `otel_langfuse_public_key` / `otel_langfuse_secret_key`.
- `.env.example`: documented the full OTEL block (base-URL endpoint, the
  keys-vs-raw-header auth choice, sampling, content capture).
- `tests/test_observability_tracing.py`: added a `_Cfg` stand-in and tests for
  endpoint normalization (base URL / full path / trailing slash), header
  resolution (override-wins / keys→Basic / empty), and `setup_tracing` with a
  base URL + keys.

## Decisions

- **Normalize in our code rather than switch to the base-env-var convention.**
  We could have set `OTEL_EXPORTER_OTLP_ENDPOINT` and let the SDK append the
  path, but anygarden passes an explicit `endpoint=` (clearer, testable, no
  global env coupling); normalizing the one string is simpler than rewiring the
  exporter construction. Rejected leaving it verbatim + only fixing the
  docstring — that keeps the 404 footgun.
- **Convenience keys alongside, not replacing, the raw header.** The raw
  `otel_otlp_headers` still wins so non-Langfuse backends and multi-header
  setups keep working; the keys are a strict add-on for the common Langfuse
  case. Rejected a Langfuse-only auth field that would have boxed out generic
  OTLP collectors.
- Assumption: Langfuse keeps HTTP Basic (`public:secret`) for OTLP ingest and
  the `/api/public/otel` base path. If a future Langfuse changes the auth
  scheme or path, `resolve_otlp_headers` / the `.env.example` example need a
  revisit (the raw-header override remains the escape hatch).

## Result

- `uv run pytest packages/cluster` → 1041 passed (7 new); `ruff` clean.
- Verified end-to-end against the live self-hosted Langfuse: a base URL
  (`http://<host>:3000/api/public/otel`) + public/secret keys normalized to
  `.../v1/traces`, built the Basic header, and exported a span
  (`force_flush ok = True`).
- Operators can now set `ANYGARDEN_OTEL_OTLP_ENDPOINT` (base URL) +
  `ANYGARDEN_OTEL_LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` with no manual encoding.
- Out of scope (separate, larger item): LLM-level `llm.generation` spans are
  still empty unless agents route their engine LLM calls through the gateway —
  this PR only fixes the trace-export ergonomics.
