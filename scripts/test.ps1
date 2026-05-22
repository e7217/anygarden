# Windows-equivalent of `make test` — runs Python tests across all
# workspace packages. Usage: ``./scripts/test.ps1``

$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot/..
try {
    foreach ($pkg in @("packages/machine", "packages/agent", "packages/cluster")) {
        Write-Host "[anygarden] pytest $pkg" -ForegroundColor Cyan
        Push-Location $pkg
        try {
            uv run pytest -x
        } finally {
            Pop-Location
        }
    }
} finally {
    Pop-Location
}
