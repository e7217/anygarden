"""Durable local receipts; one manager owns this directory for its lifetime."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

from .contracts import (
    FAILURE_CODES,
    ExecutionEvent,
    Invocation,
    Receipt,
    RuntimeResult,
    SessionScope,
    canonical,
)


class ExecutionConflict(ValueError):
    """An invocation ID was reused with different content."""


class ReceiptStore:
    def __init__(self, directory: Path):
        if os.name != "posix":
            raise RuntimeError("local execution ownership currently requires POSIX")
        import fcntl

        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = (directory / "owner.lock").open("a+b")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("execution directory already owned") from None
        try:
            path = directory / "receipts.sqlite3"
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            self.db = sqlite3.connect(path)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, scope TEXT NOT NULL, scope_data TEXT NOT NULL,
                    state TEXT NOT NULL, process_state TEXT NOT NULL,
                    outcome TEXT, reason TEXT, text TEXT, usage TEXT, pid INTEGER
                );
                CREATE TABLE IF NOT EXISTS events (
                    execution_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(execution_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS bindings (scope TEXT PRIMARY KEY, materialization TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    scope TEXT PRIMARY KEY, handle TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_usage_counters (
                    scope TEXT NOT NULL, handle TEXT NOT NULL,
                    cumulative TEXT, execution_id TEXT NOT NULL,
                    valid INTEGER NOT NULL,
                    PRIMARY KEY(scope,handle)
                );
            """)
            with self.db:
                for row in self.db.execute(
                    "SELECT id,state FROM executions WHERE outcome IS NULL"
                ).fetchall():
                    # Queued means launch intent was never committed. Other states may
                    # already have tool effects, even when the PID was never persisted.
                    queued = row["state"] == "queued"
                    self.finish(
                        row["id"],
                        RuntimeResult(
                            "cancelled" if queued else "unknown",
                            "not_started" if queued else "unknown",
                            "manager_restarted",
                        ),
                    )
        except BaseException:
            self._lock.close()
            raise

    def close(self) -> None:
        self.db.close()
        self._lock.close()

    def get(self, execution_id: str) -> Receipt:
        row = self.db.execute(
            "SELECT * FROM executions WHERE id=?", (execution_id,)
        ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        return Receipt(
            row["id"],
            row["state"],
            row["process_state"],
            row["outcome"],
            row["reason"],
            row["text"],
            json.loads(row["usage"]) if row["usage"] else None,
            row["reason"]
            if row["outcome"] == "failed" and row["reason"] in FAILURE_CODES
            else None,
        )

    def accept(self, invocation: Invocation) -> bool:
        with self.db:
            row = self.db.execute(
                "SELECT fingerprint FROM executions WHERE id=?",
                (invocation.execution_id,),
            ).fetchone()
            if row:
                if row[0] != invocation.fingerprint:
                    raise ExecutionConflict(
                        "execution ID has different invocation content"
                    )
                return False
            materialization = canonical(
                [
                    str(invocation.workspace.resolve()),
                    str(invocation.runtime_home.resolve()),
                    invocation.permission_level,
                ]
            )
            binding = self.db.execute(
                "SELECT materialization FROM bindings WHERE scope=?",
                (invocation.scope.key,),
            ).fetchone()
            if binding and binding[0] != materialization:
                raise ExecutionConflict(
                    "session scope requires a new workspace/policy epoch"
                )
            self.db.execute(
                "INSERT OR IGNORE INTO bindings VALUES (?,?)",
                (invocation.scope.key, materialization),
            )
            self.db.execute(
                "INSERT INTO executions(id,fingerprint,scope,scope_data,state,process_state) "
                "VALUES(?,?,?,?, 'queued','not_started')",
                (
                    invocation.execution_id,
                    invocation.fingerprint,
                    invocation.scope.key,
                    canonical(asdict(invocation.scope)),
                ),
            )
            self.event(invocation.execution_id, "accepted", {})
        return True

    def scope_for(self, execution_id: str) -> SessionScope:
        row = self.db.execute(
            "SELECT scope_data FROM executions WHERE id=?", (execution_id,)
        ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        return SessionScope(**json.loads(row[0]))

    def scope_blocked(self, scope: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM executions WHERE scope=? AND outcome='unknown'", (scope,)
            ).fetchone()
            is not None
        )

    def session(self, scope: str) -> str | None:
        row = self.db.execute(
            "SELECT handle FROM sessions WHERE scope=?", (scope,)
        ).fetchone()
        return row[0] if row else None

    def event(self, execution_id: str, kind: str, payload: dict) -> None:
        # Invoked inside the same transaction as state changes where applicable.
        self.db.execute(
            "INSERT INTO events VALUES (?, (SELECT COALESCE(MAX(sequence),0)+1 "
            "FROM events WHERE execution_id=?), ?, ?)",
            (execution_id, execution_id, kind, canonical(payload)),
        )

    def transition(
        self, execution_id: str, state: str, process_state: str, pid: int | None = None
    ):
        with self.db:
            self.db.execute(
                "UPDATE executions SET state=CASE WHEN state='cancel_requested' THEN state ELSE ? END,process_state=?,pid=? WHERE id=? AND outcome IS NULL",
                (state, process_state, pid, execution_id),
            )
            self.event(execution_id, state, {"process_state": process_state})

    def finish(self, execution_id: str, result: RuntimeResult) -> Receipt:
        with self.db:
            if (
                (
                    result.outcome == "succeeded"
                    and (
                        not result.text
                        or result.process_state not in {"finished", "stopped"}
                    )
                )
                or (
                    result.outcome == "failed"
                    and (
                        result.reason not in FAILURE_CODES
                        or result.process_state
                        not in {"not_started", "finished", "stopped"}
                    )
                )
                or (
                    result.outcome == "cancelled"
                    and result.process_state not in {"not_started", "stopped"}
                )
            ):
                result = replace(result, outcome="unknown", process_state="unknown",
                                 reason="invalid_terminal_evidence", text=None,
                                 session_handle=None)
            current = self.get(execution_id)
            if current.outcome is not None:
                return current
            # Same-scope manager FIFO serialization makes this baseline stable;
            # its update shares the terminal receipt transaction, including gaps.
            from .usage import SOURCE, counters, invocation_usage

            codex_usage = result.usage_source == SOURCE
            missing_codex_usage = (
                not codex_usage
                and result.process_state != "not_started"
                and self.scope_for(execution_id).engine == "codex-cli"
            )
            if codex_usage or missing_codex_usage:
                scope = self.db.execute("SELECT scope FROM executions WHERE id=?", (execution_id,)).fetchone()[0]
                requested = self.session(scope)
                row = self.db.execute(
                    "SELECT cumulative,execution_id,valid FROM session_usage_counters WHERE scope=? AND handle=?",
                    (scope, requested),
                ).fetchone() if requested else None
                baseline = None if row is None else {
                    "cumulative": json.loads(row["cumulative"]) if row["cumulative"] else None,
                    "execution_id": row["execution_id"], "valid": bool(row["valid"]),
                }
                # Recovery and manager exceptions may lack provider evidence.
                # A started gap must still invalidate an existing Codex counter;
                # known not-started work and other runtimes remain unchanged.
                if missing_codex_usage and baseline is not None:
                    result = replace(
                        result, usage_source=SOURCE, usage_cumulative=None,
                        usage_session_handle=None, usage_terminal=False,
                    )
                    codex_usage = True
            if codex_usage:
                normalized = invocation_usage(
                    requested_session=requested,
                    observed_session=result.usage_session_handle,
                    provider_cumulative=result.usage_cumulative,
                    terminal_usage=result.usage_terminal,
                    process_state=result.process_state, baseline=baseline,
                )
                result = replace(result, usage=normalized)
                if result.process_state != "not_started":
                    raw = counters(result.usage_cumulative)
                    valid = bool(result.usage_terminal and result.process_state in {"finished", "stopped"}
                                 and raw is not None and raw["input_tokens"] is not None and raw["output_tokens"] is not None)
                    # A missing terminal or a different observed handle cannot
                    # leave the old successful baseline valid for the next call.
                    for handle in {requested, result.usage_session_handle} - {None}:
                        handle_valid = valid and handle == result.usage_session_handle
                        evidence = raw if handle_valid else (baseline["cumulative"] if baseline and handle == requested else None)
                        evidence_execution_id = (baseline["execution_id"]
                                                 if not handle_valid and baseline and handle == requested
                                                 else execution_id)
                        self.db.execute(
                            "INSERT INTO session_usage_counters VALUES(?,?,?,?,?) "
                            "ON CONFLICT(scope,handle) DO UPDATE SET cumulative=excluded.cumulative,"
                            "execution_id=excluded.execution_id,valid=excluded.valid",
                            (scope, handle, canonical(evidence) if evidence is not None else None, evidence_execution_id, int(handle_valid)),
                        )
            state = "completed" if result.outcome == "succeeded" else result.outcome
            self.db.execute(
                "UPDATE executions SET state=?,process_state=?,outcome=?,reason=?,"
                "text=?,usage=? WHERE id=?",
                (
                    state,
                    result.process_state,
                    result.outcome,
                    result.reason,
                    result.text,
                    canonical(result.usage) if result.usage else None,
                    execution_id,
                ),
            )
            if result.outcome == "succeeded" and result.session_handle:
                scope = self.db.execute(
                    "SELECT scope FROM executions WHERE id=?", (execution_id,)
                ).fetchone()[0]
                self.db.execute(
                    "INSERT OR REPLACE INTO sessions VALUES (?,?)",
                    (scope, result.session_handle),
                )
            self.event(execution_id, "terminal", asdict(self.get(execution_id)))
        return self.get(execution_id)

    def events(self, execution_id: str, after: int) -> list[ExecutionEvent]:
        return [
            ExecutionEvent(
                execution_id, r["sequence"], r["kind"], json.loads(r["payload"])
            )
            for r in self.db.execute(
                "SELECT * FROM events WHERE execution_id=? AND sequence>? "
                "ORDER BY sequence LIMIT 100",
                (execution_id, after),
            )
        ]
