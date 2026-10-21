# Pre-release check: the full test suite (includes the stylesheet guard and the
# EN/TR key parity) and, when a local database exists, a migration rehearsal on
# a copy of it (the original is never opened).
#
#   .\scripts\check.ps1                       # uses "python" from PATH
#   .\scripts\check.ps1 -Python .venv\Scripts\python.exe
param([string]$Python = "python")

Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "== Tests ==" -ForegroundColor Cyan
& $Python -m pytest -p no:cacheprovider -q
if ($LASTEXITCODE -ne 0) { Write-Host "TESTS FAILED" -ForegroundColor Red; exit 1 }

if (Test-Path "data/phoenixmarket.db") {
    Write-Host "`n== Migration rehearsal (copy of data/phoenixmarket.db) ==" -ForegroundColor Cyan
    & $Python scripts/rehearse_migration.py
    if ($LASTEXITCODE -ne 0) { Write-Host "REHEARSAL FAILED" -ForegroundColor Red; exit 1 }
}

Write-Host "`nALL CHECKS PASSED" -ForegroundColor Green
