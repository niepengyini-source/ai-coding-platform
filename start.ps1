param([string]$LanAddress = '', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:DJANGO_SETTINGS_MODULE = 'config.settings'
$platformPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $platformPython)) {
    Write-Host '请先按README建立Python虚拟环境并安装依赖。'
    exit 1
}
& $platformPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $platformPython manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $platformPython manage.py initialize
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($LanAddress) {
    & $platformPython server.py --host $LanAddress --port $Port
} else {
    & $platformPython server.py --port $Port
}
