# Windows-equivalent of `make dev` (see Makefile in repo root).
#
# Starts the cluster backend on port 8001 with auto-reload, and the
# Vite frontend in parallel. Stop with Ctrl+C
# (the trap below propagates termination to the child processes).
#
# Requires: PowerShell 5.1+, uv (https://docs.astral.sh/uv/),
# Node 20+ for the frontend.

$ErrorActionPreference = "Stop"

$DevPort = if ($env:ANYGARDEN_PORT) { $env:ANYGARDEN_PORT } else { "8001" }
$env:ANYGARDEN_PORT = $DevPort

Push-Location $PSScriptRoot/..
try {
    # No `alembic upgrade head` here (#647): the backend migrates its own
    # database at startup, against the URL it actually opens. This line used
    # alembic.ini's hardcoded URL, which pointed somewhere else entirely.
    Write-Host "[anygarden] starting backend on http://127.0.0.1:$DevPort" -ForegroundColor Cyan
    $backend = Start-Process -PassThru -NoNewWindow -FilePath "uv" `
        -ArgumentList @(
            "run", "--package", "anygarden",
            "uvicorn", "anygarden.app:create_app", "--factory",
            "--reload", "--host", "0.0.0.0", "--port", $DevPort,
            "--log-level", "debug"
        )

    Push-Location packages/cluster/frontend
    try {
        Write-Host "[anygarden] npm install" -ForegroundColor Cyan
        npm install --silent
        Write-Host "[anygarden] starting frontend dev server" -ForegroundColor Cyan
        # Foreground so Ctrl+C reaches Vite first; the finally block
        # below cleans up the backend.
        npm run dev
    } finally {
        Pop-Location
        if (-not $backend.HasExited) {
            Write-Host "[anygarden] stopping backend (pid=$($backend.Id))" -ForegroundColor Yellow
            Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
        }
    }
} finally {
    Pop-Location
}
