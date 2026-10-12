"""Benchmark result files, roll-ups and the trend report.

One JSON file per suite run lands in ``benchmarks/results/`` and is
committed, so the history of pass^k, false-done rate and cost per success
survives the machine it was measured on. Raw evidence (transcripts,
screenshots) stays local.

Run statuses:

- ``pass`` / ``fail`` — every expected hard gate passed / did not.
- ``error`` — the driver crashed; excluded from pass^k and flagged.
- judged overrides (``annotate``): ``invalid`` (plan defect) and
  ``environment`` (host limit, e.g. sandbox) are excluded; a judge may also
  turn a mechanical result into ``pass`` or ``fail`` with a reason.

A scenario is only ``pass^k`` when it has at least k counted runs and all
of them passed; fewer counted runs is ``incomplete``, never a pass.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = 1
COUNTED = {"pass", "fail"}
EXCLUDED = {"error", "invalid", "environment"}


def effective_status(run: dict) -> str:
    judged = run.get("judged") or {}
    return judged.get("status") or run["status"]


@dataclass
class Rollup:
    scenario: str
    mode: str
    k: int
    counted: int = 0
    passed: int = 0
    excluded: int = 0
    claimed: int = 0
    false_done: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    wall_seconds: float = 0.0
    runs: int = 0

    @property
    def verdict(self) -> str:
        if self.counted < self.k:
            return "incomplete"
        return "pass" if self.passed == self.counted else "fail"

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.counted if self.counted else None

    @property
    def false_done_rate(self) -> float | None:
        return self.false_done / self.claimed if self.claimed else None

    @property
    def cost_per_pass(self) -> float | None:
        return self.cost_usd / self.passed if self.passed else None


def rollup(result: dict) -> list[Rollup]:
    """Per (scenario, mode) figures for one result file."""
    k = int(result.get("k", 3))
    groups: dict[tuple[str, str], Rollup] = {}
    for run in result.get("runs", []):
        key = (run["scenario"], run["mode"])
        r = groups.setdefault(key, Rollup(run["scenario"], run["mode"], k))
        r.runs += 1
        status = effective_status(run)
        if status in EXCLUDED:
            r.excluded += 1
            continue
        r.counted += 1
        r.passed += status == "pass"
        if run.get("claimed_done"):
            r.claimed += 1
            r.false_done += status != "pass"
        cost = (run.get("metrics") or {}).get("cost") or {}
        r.cost_usd += float(cost.get("cost_usd") or 0)
        r.tokens += int(cost.get("prompt_tokens") or 0) + int(cost.get("completion_tokens") or 0)
        r.wall_seconds += float(run.get("wall_seconds") or 0)
    return [groups[k] for k in sorted(groups)]


def uplift(rows: list[Rollup]) -> list[dict]:
    """Team minus solo pass rate for scenarios measured in both modes."""
    by = {(r.scenario, r.mode): r for r in rows}
    out = []
    for scenario in sorted({r.scenario for r in rows}):
        solo, team = by.get((scenario, "solo")), by.get((scenario, "team"))
        if not solo or not team or solo.pass_rate is None or team.pass_rate is None:
            continue
        out.append(
            {
                "scenario": scenario,
                "solo": solo.pass_rate,
                "team": team.pass_rate,
                "uplift": team.pass_rate - solo.pass_rate,
                "cost_ratio": (
                    (team.cost_usd / team.counted) / (solo.cost_usd / solo.counted)
                    if solo.cost_usd and solo.counted and team.counted
                    else None
                ),
            }
        )
    return out


def load(results_dir: Path) -> list[dict]:
    files = sorted(results_dir.glob("*.json"))
    results = [json.loads(f.read_text(encoding="utf-8")) | {"_file": f.name} for f in files]
    return sorted(results, key=lambda r: r.get("started_at", ""))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"${value:.2f}"


def report(results: list[dict]) -> str:
    """Markdown: latest snapshot, team-vs-solo uplift and per-scenario trend."""
    if not results:
        return "# Benchmark report\n\nNo results yet.\n"
    latest = results[-1]
    rows = rollup(latest)
    engine = latest.get("engine") or {}
    lines = [
        "# Benchmark report",
        "",
        (
            f"Latest: `{latest['_file']}` · commit `{latest.get('commit', '?')[:10]}` · "
            f"{engine.get('engine', '?')} {engine.get('model') or ''} · k={latest.get('k')}"
        ),
        "",
        "| Scenario | Mode | Verdict | Passed | Excluded | False done | Cost/pass | Avg wall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        avg_wall = f"{r.wall_seconds / r.counted / 60:.1f}m" if r.counted else "—"
        lines.append(
            f"| {r.scenario} | {r.mode} | {r.verdict} | {r.passed}/{r.counted} | {r.excluded} | "
            f"{_pct(r.false_done_rate)} | {_usd(r.cost_per_pass)} | {avg_wall} |"
        )
    ups = uplift(rows)
    if ups:
        lines += ["", "## Team vs solo", "", "| Scenario | Solo | Team | Uplift | Cost ratio |", "|---|---|---|---|---|"]
        for u in ups:
            ratio = "—" if u["cost_ratio"] is None else f"{u['cost_ratio']:.1f}×"
            lines.append(
                f"| {u['scenario']} | {_pct(u['solo'])} | {_pct(u['team'])} | "
                f"{u['uplift']:+.0%} | {ratio} |"
            )
    trend: dict[tuple[str, str], list[str]] = defaultdict(list)
    for result in results:
        for r in rollup(result):
            trend[(r.scenario, r.mode)].append(
                f"{result.get('started_at', '')[:10]} `{result.get('commit', '?')[:7]}` "
                f"{r.verdict} {r.passed}/{r.counted}"
            )
    lines += ["", "## Trend", ""]
    for (scenario, mode), points in sorted(trend.items()):
        lines.append(f"- **{scenario}** ({mode}): " + " → ".join(points[-6:]))
    return "\n".join(lines) + "\n"


def annotate(path: Path, scenario: str, mode: str, n: int, status: str, reason: str) -> dict:
    """Record an independent judge's verdict on one run (keeps the mechanical status)."""
    if status not in COUNTED | {"invalid", "environment"}:
        raise ValueError(f"unknown judged status: {status}")
    result = json.loads(path.read_text(encoding="utf-8"))
    for run in result["runs"]:
        if (run["scenario"], run["mode"], run["n"]) == (scenario, mode, n):
            run["judged"] = {"status": status, "reason": reason}
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return run
    raise KeyError(f"no run {scenario}/{mode}/{n} in {path}")


def new_result(meta: dict[str, Any]) -> dict:
    return {"schema": SCHEMA, **meta, "runs": []}
