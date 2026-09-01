$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    & (Join-Path $PSScriptRoot 'setup_windows.ps1')
}
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& $Python -c "from lilies.core.model import ChatService; from lilies.core.database import Database; from lilies.paths import data_root; service=ChatService(Database(data_root()/'lilies.db')); status=service.status(); print('Qwen2.5 0.5B:', 'ready' if status['modelInstalled'] and status['runtimeAvailable'] and status['workerAvailable'] else status); service.shutdown()"
