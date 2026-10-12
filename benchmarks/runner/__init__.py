"""Live benchmark runner for Anygarden (see ``benchmarks/README.md``).

Drives scenarios against an isolated node with a real engine, checks hard
gates through the REST API and appends one result file per run of the
suite to ``benchmarks/results/``.

    uv run python -m benchmarks.runner s0 --base http://127.0.0.1:8002
    uv run python -m benchmarks.runner run --mode both --k 3
    uv run python -m benchmarks.runner report
"""
