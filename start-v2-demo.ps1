$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot 'backend\venv\Scripts\python.exe'
Push-Location (Join-Path $projectRoot 'backend')
try {
    & $python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
    & $python -m v2.auth.bootstrap --database ../data/allocra-v2.sqlite3 --key-file ../data/auth-signing.key
    if ($LASTEXITCODE -ne 0) { throw 'Prototype setup failed.' }
} finally { Pop-Location }
$env:ALLOCRA_DB = Join-Path $projectRoot 'data\allocra-v2.sqlite3'
$env:ALLOCRA_SIGNING_KEY_FILE = Join-Path $projectRoot 'data\auth-signing.key'
$env:ALLOCRA_DEMO = '1'
Write-Host 'Prototype role logins enabled. Open http://localhost:5600/platform (or your configured UI port).'
& (Join-Path $projectRoot 'start.bat') --no-reload
