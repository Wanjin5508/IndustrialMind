param([string]$NodePath = 'node', [switch]$Production)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$nodeCommand = (Get-Command $NodePath -ErrorAction Stop).Source
$nodeVersion = & $nodeCommand -p 'process.versions.node'
if ([version]$nodeVersion -lt [version]'20.9.0') {
    throw 'Node.js 20.9+ required. Pass -NodePath with a supported node.exe path.'
}
$env:PATH = (Split-Path $nodeCommand -Parent) + ';' + $env:PATH
Set-Location "$projectRoot\apps\web"
$mode = if ($Production) { 'start' } else { 'dev' }
& $nodeCommand node_modules/next/dist/bin/next $mode --hostname 127.0.0.1
exit $LASTEXITCODE
