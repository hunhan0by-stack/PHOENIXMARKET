# Pre-release check: the full test suite (includes the stylesheet guard and the
# EN/TR key parity), a known-vulnerability scan of requirements.txt (pip-audit,
# needs network), a static security scan (bandit; B105 is skipped because it
# flags UI texts such as "Password") and, when a local database exists, a
# migration rehearsal on a copy of it (the original is never opened).
#
#   .\scripts\check.ps1                       # uses "python" from PATH
#   .\scripts\check.ps1 -Python .venv\Scripts\python.exe
param([string]$Python = "python")

Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "== Tests ==" -ForegroundColor Cyan
& $Python -m pytest -p no:cacheprovider -q
if ($LASTEXITCODE -ne 0) { Write-Host "TESTS FAILED" -ForegroundColor Red; exit 1 }

Write-Host "`n== Known vulnerabilities in requirements-dev.txt, which includes requirements.txt (pip-audit) ==" -ForegroundColor Cyan
& $Python -m pip_audit -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Write-Host "VULNERABLE DEPENDENCIES (or pip-audit missing: pip install -r requirements-dev.txt)" -ForegroundColor Red; exit 1 }

Write-Host "`n== Static security scan (bandit) ==" -ForegroundColor Cyan
$env:PYTHONIOENCODING = "utf-8"
& $Python -m bandit -q -r app.py wsgi.py services views scripts -s B105
if ($LASTEXITCODE -ne 0) { Write-Host "SECURITY SCAN FOUND ISSUES (or bandit missing: pip install -r requirements-dev.txt)" -ForegroundColor Red; exit 1 }

if (Test-Path "data/phoenixmarket.db") {
    Write-Host "`n== Migration rehearsal (copy of data/phoenixmarket.db) ==" -ForegroundColor Cyan
    & $Python scripts/rehearse_migration.py
    if ($LASTEXITCODE -ne 0) { Write-Host "REHEARSAL FAILED" -ForegroundColor Red; exit 1 }
    Write-Host "`n== Encryption rehearsal (copy of data/phoenixmarket.db, throw-away keys) ==" -ForegroundColor Cyan
    & $Python scripts/rehearse_encryption.py
    if ($LASTEXITCODE -ne 0) { Write-Host "ENCRYPTION REHEARSAL FAILED" -ForegroundColor Red; exit 1 }
}

Write-Host "`nALL CHECKS PASSED" -ForegroundColor Green
