$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BundledPython = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
} else {
    $null
}
$Python = if ($BundledPython -and (Test-Path -LiteralPath $BundledPython)) {
    $BundledPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -m venv (Join-Path $ProjectRoot '.venv')
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e "$ProjectRoot[dev]"
Write-Host 'Lilies in the box development environment is ready.'
