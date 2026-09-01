$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $BundledPython = if ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    } else {
        $null
    }
    if ($BundledPython -and (Test-Path -LiteralPath $BundledPython)) {
        $Python = $BundledPython
    } else {
        $Python = (Get-Command python -ErrorAction Stop).Source
    }
}
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& $Python -m lilies --restore
Write-Host 'Windows desktop and taskbar restore request completed.'
