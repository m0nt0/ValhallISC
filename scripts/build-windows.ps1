# Build ValhallISC for Windows: dist\ValhallISC.exe (tray app, windowed) + dist\valhallisc-cli.exe (console CLI).
# Run from the project folder in PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
# Prerequisites: Python 3.12 (py launcher), WinFsp installed (only needed to run, not to build).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e ".[gui,dev,build]"

Write-Host "== unit tests"
& $py -m pytest -q tests\unit
if ($LASTEXITCODE -ne 0) { Write-Warning "unit tests failed (see above); building anyway" }

$sha = (git rev-parse --short HEAD 2>$null)
if (-not $sha) { $sha = "unknown" }
Set-Content -Path src\irisfs\_build.py -Value "GIT_SHA = `"$sha`"" -Encoding UTF8

& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\valhallisc.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "== smoke"
& dist\valhallisc-cli.exe --version
& dist\valhallisc-cli.exe doctor
Get-ChildItem dist | Format-Table Name, Length
