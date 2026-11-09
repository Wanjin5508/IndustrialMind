$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" -m uvicorn rfq_workbench.main:create_app --factory --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
