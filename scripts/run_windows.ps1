$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'setup_windows.ps1')
}
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& $Python -m lilies @args
