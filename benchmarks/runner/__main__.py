"""CLI: ``uv run python -m benchmarks.runner {s0,run,report,annotate,validate,targets}``."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import ledger
from .client import Api, ApiError, Gates, items, login, save
from .receiver import MockReceiver
from .scenarios import PROTOCOL_SCENARIOS, Ctx, Scenario, deliverable
from .topology import SEEDERS, EngineConfig, Fixture

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "benchmarks" / "suite.json"
RESULTS = ROOT / "benchmarks" / "results"
REQUIREMENTS = ROOT / "docs" / "requirements.md"
_REQ_ID = re.compile(r"^\|\s*([A-Z]{2,5}-\d{2,3})\s*\|", re.MULTILINE)


# ── suite loading ───────────────────────────────────────────────────────


def load_scenarios(suite_path: Path, holdout_dir: Path | None = None) -> tuple[dict, list[Scenario]]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    out = []
    for entry in suite["scenarios"]:
        if entry.get("kind") == "deliverable":
            out.append(deliverable(entry))
        else:
            out.append(PROTOCOL_SCENARIOS[entry["id"]])
    if holdout_dir:
        for path in sorted(holdout_dir.glob("*.json")):
            spec = json.loads(path.read_text(encoding="utf-8"))
            out.append(deliverable({**spec, "id": f"HO-{spec['id']}", "holdout": True}))
    return suite, out


def validate(suite_path: Path, requirements: Path, holdout_dir: Path | None = None) -> list[str]:
    """Problems that would make a suite run meaningless, one line each."""
    problems = []
    try:
        suite, scenarios = load_scenarios(suite_path, holdout_dir)
    except KeyError as exc:
        return [f"unknown protocol scenario {exc}"]
    known = set(_REQ_ID.findall(requirements.read_text(encoding="utf-8")))
    seen: set[str] = set()
    for s in scenarios:
        if s.id in seen:
            problems.append(f"{s.id}: duplicate scenario id")
        seen.add(s.id)
        for req in s.requirements:
            if req not in known:
                problems.append(f"{s.id}: requirement {req} is not in {requirements.name}")
        if not set(s.modes) <= set(SEEDERS):
            problems.append(f"{s.id}: unknown mode in {s.modes}")
        if s.spec:
            for key in ("request", "must_include"):
                if not s.spec.get(key):
                    problems.append(f"{s.id}: deliverable needs '{key}'")
    if int(suite.get("k", 0)) < 1:
        problems.append("suite k must be >= 1")
    return problems


# ── one run ─────────────────────────────────────────────────────────────


async def capture(api: Api, fx: Fixture, out: Path) -> None:
    for key, room in fx.rooms.items():
        for name, path in (("messages", f"/api/v1/rooms/{room['id']}/messages"), ("tasks", f"/api/v1/rooms/{room['id']}/tasks")):
            try:
                save(out / f"{name}-{key}.json", await api(path, params={"limit": 200} if name == "messages" else None))
            except ApiError as exc:
                save(out / f"{name}-{key}-error.json", str(exc))
    turns = []
    for agent_id in fx.agent_ids():
        turns += items(await api("/api/v1/turns", params={"agent_id": agent_id, "limit": 500}))
    save(out / "turns.json", turns)


async def run_one(
    ctx: Ctx, scenario: Scenario, mode: str, n: int, engine: EngineConfig, evidence: Path, stamp: str
) -> dict:
    api = ctx.api
    record: dict = {"scenario": scenario.id, "mode": mode, "n": n}
    if scenario.spec.get("holdout"):
        record["holdout"] = True
    started = datetime.now(UTC)
    t0 = time.monotonic()
    fx: Fixture | None = None
    gates = Gates(expected=scenario.gates)
    try:
        fx = await SEEDERS[mode](api, engine, f"{scenario.id}-{mode}-{n}-{stamp}".lower())
        facts = await scenario.run(ctx, fx, gates, n)
        record["status"] = "pass" if gates.passed else "fail"
        record["claimed_done"] = facts.get("claimed_done")
    except Exception as exc:  # noqa: BLE001 — a crashed driver is a recorded result
        record["status"] = "error"
        record["error"] = repr(exc)[:500]
    record["wall_seconds"] = round(time.monotonic() - t0)
    record["gates"] = gates.entries
    out = evidence / scenario.id / mode / str(n)
    if fx is not None:
        await asyncio.sleep(ctx.settle)
        try:
            await capture(api, fx, out)
            record["metrics"] = await api(
                "/api/v1/metrics/collaboration",
                params={"project_id": fx.project["id"], "since": started.isoformat()},
            )
        except ApiError as exc:
            record["capture_error"] = str(exc)[:300]
        for agent_id in fx.agent_ids():
            try:
                await api(f"/api/v1/agents/{agent_id}/stop", "POST")
            except ApiError:
                pass
    save(out / "record.json", record)
    if record.get("holdout"):
        # Keep hold-out content out of the committed ledger.
        record["gates"] = [{k: v for k, v in g.items() if k != "value"} for g in record["gates"]]
    return record


def _commit() -> str:
    """HEAD, marked ``-dirty`` when tracked files differ: such a result
    measured code that no commit contains."""
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


async def cmd_run(args: argparse.Namespace) -> int:
    suite, scenarios = load_scenarios(args.suite, args.holdout_dir)
    problems = validate(args.suite, REQUIREMENTS, args.holdout_dir)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    modes = {"solo", "team"} if args.mode == "both" else {args.mode}
    k = args.k or int(suite["k"])
    engine = EngineConfig(args.engine, args.model, args.permission_level, args.turn_timeout)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    evidence = args.evidence_dir or ROOT / ".tmp" / "bench" / stamp
    api = await login(args.base, args.email, args.password)
    receiver = None
    if any(s.needs_receiver for s in scenarios):
        receiver = MockReceiver(port=args.receiver_port).start()
    ctx = Ctx(api, receiver)

    # S0 per topology: nothing else is interpretable if the stack can't answer.
    for mode in sorted(modes):
        s0 = await run_one(ctx, PROTOCOL_SCENARIOS["S0"], mode, 1, engine, evidence, stamp)
        if s0["status"] != "pass":
            print(f"S0 failed in {mode} topology: {s0.get('error') or s0['gates']}", file=sys.stderr)
            return 1

    result = ledger.new_result(
        {
            "suite": suite["name"],
            "suite_version": suite["version"],
            "commit": args.commit or _commit(),
            "started_at": datetime.now(UTC).isoformat(),
            "engine": {"engine": engine.engine, "model": engine.model, "permission_level": engine.permission_level},
            "k": k,
        }
    )
    path = args.results_dir / f"{stamp}-{result['commit'][:7]}.json"
    plan = [(s, m, n) for s in scenarios for m in sorted(modes & set(s.modes)) for n in range(1, k + 1)]
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(s: Scenario, m: str, n: int) -> None:
        async with sem:
            record = await run_one(ctx, s, m, n, engine, evidence, stamp)
        result["runs"].append(record)
        result["finished_at"] = datetime.now(UTC).isoformat()
        save(path, result)  # incremental: a crash keeps the finished runs
        print(f"{s.id} {m} #{n}: {record['status']}", flush=True)

    await asyncio.gather(*(guarded(s, m, n) for s, m, n in plan))
    if receiver:
        receiver.stop()
    await api.close()
    result["runs"].sort(key=lambda r: (r["scenario"], r["mode"], r["n"]))
    save(path, result)
    print(ledger.report([*ledger.load(args.results_dir)]))
    print(f"result: {path}\nevidence: {evidence}")
    return 0


async def cmd_s0(args: argparse.Namespace) -> int:
    api = await login(args.base, args.email, args.password)
    engine = EngineConfig(args.engine, args.model, args.permission_level, args.turn_timeout)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    evidence = args.evidence_dir or ROOT / ".tmp" / "bench" / stamp
    record = await run_one(Ctx(api), PROTOCOL_SCENARIOS["S0"], args.mode, 1, engine, evidence, stamp)
    print(json.dumps(record, ensure_ascii=False, indent=1))
    return 0 if record["status"] == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def live(p: argparse.ArgumentParser) -> None:
        p.add_argument("--base", default="http://127.0.0.1:8002")
        p.add_argument("--email", default="bench-admin@bench.test")
        p.add_argument("--password", default="bench-pass-1")
        p.add_argument("--engine", default="codex-cli")
        p.add_argument("--model")
        p.add_argument("--permission-level", default="standard")
        p.add_argument("--turn-timeout", type=int, default=300)
        p.add_argument("--evidence-dir", type=Path)

    p_run = sub.add_parser("run", help="run the suite and append a result file")
    live(p_run)
    p_run.add_argument("--mode", choices=("solo", "team", "both"), default="both")
    p_run.add_argument("--k", type=int)
    p_run.add_argument("--scenarios", help="comma-separated scenario ids")
    p_run.add_argument("--suite", type=Path, default=SUITE)
    p_run.add_argument("--holdout-dir", type=Path)
    p_run.add_argument("--results-dir", type=Path, default=RESULTS)
    p_run.add_argument("--concurrency", type=int, default=3)
    p_run.add_argument("--receiver-port", type=int, default=8189)
    p_run.add_argument("--commit")

    p_s0 = sub.add_parser("s0", help="stack gate only")
    live(p_s0)
    p_s0.add_argument("--mode", choices=("solo", "team"), default="solo")

    p_rep = sub.add_parser("report", help="print the trend report")
    p_rep.add_argument("--results-dir", type=Path, default=RESULTS)

    p_ann = sub.add_parser("annotate", help="record a judge's verdict on one run")
    for name in ("file", "scenario", "mode"):
        p_ann.add_argument(name)
    p_ann.add_argument("n", type=int)
    p_ann.add_argument("status", choices=("pass", "fail", "invalid", "environment"))
    p_ann.add_argument("reason")

    p_val = sub.add_parser("validate", help="check the suite against docs/requirements.md")
    p_val.add_argument("--suite", type=Path, default=SUITE)
    p_val.add_argument("--holdout-dir", type=Path)

    p_tgt = sub.add_parser("targets", help="print ANYGARDEN_PROJECT_ACTION_TARGETS for the mock receiver")
    p_tgt.add_argument("--receiver-port", type=int, default=8189)

    args = parser.parse_args(argv)
    if args.cmd == "run":
        return asyncio.run(cmd_run(args))
    if args.cmd == "s0":
        return asyncio.run(cmd_s0(args))
    if args.cmd == "report":
        print(ledger.report(ledger.load(args.results_dir)))
        return 0
    if args.cmd == "annotate":
        run = ledger.annotate(Path(args.file), args.scenario, args.mode, args.n, args.status, args.reason)
        print(json.dumps(run["judged"], ensure_ascii=False))
        return 0
    if args.cmd == "validate":
        problems = validate(args.suite, REQUIREMENTS, args.holdout_dir)
        print("\n".join(problems) or "suite ok")
        return 1 if problems else 0
    if args.cmd == "targets":
        url = f"http://127.0.0.1:{args.receiver_port}/submissions"
        print(json.dumps({"mock-portal": {"url": url, "label": "Mock portal", "action_kind": "submission"}}))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
