# SMO Space Mission Simulator — Windows Installation Script
# Requires: Python 3.11+ installed and on PATH

$ErrorActionPreference = "Stop"

Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  SMO Simulator — Windows Installation" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan

# Find project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Find Python
$Python = $null
foreach ($cmd in @("python3.13", "python3.12", "python3.11", "python")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver) {
            $parts = $ver.Split(".")
            if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                $Python = $cmd
                break
            }
        }
    } catch {}
}

if (-not $Python) {
    Write-Host "ERROR: Python 3.11+ not found. Download from https://python.org" -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $Python" -ForegroundColor Green

# Create venv
Set-Location $ProjectRoot
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $Python -m venv .venv
}
& .venv\Scripts\Activate.ps1

# Install
Write-Host "Installing SMO packages..."
pip install -q -e packages/smo-common
pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner
pip install -q sgp4 numpy aiohttp 2>$null

# Verify
Write-Host "`nVerifying installation..."
$pass = $true
foreach ($mod in @("smo_common", "smo_simulator", "smo_mcs", "smo_planner")) {
    try {
        python -c "import $mod" 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "  ✓ $mod" -ForegroundColor Green }
        else { Write-Host "  ✗ $mod" -ForegroundColor Red; $pass = $false }
    } catch { Write-Host "  ✗ $mod" -ForegroundColor Red; $pass = $false }
}

if ($pass) {
    Write-Host "`nInstallation complete!" -ForegroundColor Green
    Write-Host "`nTo start: bash start.sh (Git Bash) or run services individually:" -ForegroundColor Green
    Write-Host "  python -m smo_simulator.server --config configs/eosat1/"
    Write-Host "  python -m smo_mcs.server --config configs/eosat1/ --port 9090"
    Write-Host "  python -m smo_planner.server --config configs/eosat1/ --port 9091"
} else {
    Write-Host "`nInstallation completed with errors." -ForegroundColor Red
    exit 1
}
