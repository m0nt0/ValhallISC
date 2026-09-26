# Build ValhallISC for Windows: dist\ValhallISC.exe (tray app, windowed) + dist\valhallisc-cli.exe (console CLI).
# Run from the project folder in PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
# Prerequisites: Python 3.12 (py launcher), WinFsp installed (only needed to run, not to build).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$ci = [bool]$env:GITHUB_ACTIONS

# Run a native command; on failure show the end of its output (as a GitHub annotation in CI, readable without
# downloading the log) and stop. Native commands never stop PowerShell on their own, whatever $ErrorActionPreference.
function Invoke-Step([string]$what, [scriptblock]$cmd, [switch]$warnOnly) {
    Write-Host "== $what"
    $ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 would otherwise stop at the first stderr line
    $out = & $cmd 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $out | Write-Host
    if ($code -eq 0) { return }
    $tail = ($out | Select-Object -Last 120) -join "%0A"
    if ($warnOnly) {
        if ($ci) { Write-Host "::warning title=$what failed (exit $code)::$tail" } else { Write-Warning "$what failed (exit $code)" }
        return
    }
    if ($ci) { Write-Host "::error title=$what failed (exit $code)::$tail" }
    throw "$what failed (exit $code)"
}

if (-not (Test-Path .venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
$py = ".venv\Scripts\python.exe"
Invoke-Step "pip upgrade" { & $py -m pip install --quiet --upgrade pip }
Invoke-Step "install dependencies" { & $py -m pip install --quiet -e ".[gui,dev,build]" }
Invoke-Step "unit tests" { & $py -m pytest -q --tb=line tests\unit } -warnOnly   # building anyway; CI gates on them separately

$sha = (git rev-parse --short HEAD 2>$null)
if (-not $sha) { $sha = "unknown" }
Set-Content -Path src\irisfs\_build.py -Value "GIT_SHA = `"$sha`"" -Encoding UTF8

Invoke-Step "PyInstaller" { & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\valhallisc.spec }
Invoke-Step "smoke: --version" { & dist\valhallisc-cli.exe --version }
# doctor exits non-zero when WinFsp is missing (as on a CI runner): informational only
Invoke-Step "smoke: doctor" { & dist\valhallisc-cli.exe doctor } -warnOnly
Get-ChildItem dist | Format-Table Name, Length
exit 0
