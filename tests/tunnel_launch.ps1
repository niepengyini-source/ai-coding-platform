$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\外网脚本.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
# Load function definitions only: never run the launcher against the real platform.
foreach ($function in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    Invoke-Expression $function.Extent.Text
}

function Assert($condition, [string]$message) {
    if (-not $condition) { throw $message }
}

$Cloudflared = $PSCommandPath
$script:LocalBase = 'http://127.0.0.1:8000'
$script:Ready = $true
$script:Events = @()
function Read-Host { throw 'Restart must not wait for keyboard input.' }
function Show-Banner {}
function Get-CurrentTunnelInfo { return [pscustomobject]@{ HostName = 'fixture.trycloudflare.com' } }
function Stop-TunnelProcesses { $script:Events += 'stop' }
function Start-TunnelDetached {
    $script:Events += 'create'
    return [pscustomobject]@{ HostName = 'fixture.trycloudflare.com'; ProcessId = 123 }
}
function Update-PublicHost { $script:Events += 'host'; return $true }
function Save-StatusFile($hostname, $tunnelPid, $state) { $script:Events += $state }
function Ensure-PlatformUsesHost { $script:Events += 'load' }
function Wait-TunnelReady { $script:Events += 'verify'; return $script:Ready }
function Show-Links($hostname, $changed) {
    Assert ($hostname -eq 'fixture.trycloudflare.com') 'Wrong address displayed.'
    $script:Events += 'display'
}

Invoke-Restart *> $null
Assert (($script:Events -join ',') -eq 'stop,create,host,待验证,load,verify,正常,display') 'Restart must verify and display the new address without prompting.'

$script:Ready = $false
$script:Events = @()
$failure = $null
try { Invoke-Restart *> $null } catch { $failure = $_ }
Assert ($null -ne $failure) 'Failed public verification must report an error.'
Assert ($script:Events -contains '待验证') 'Keep an unverified address marked pending.'
Assert ($script:Events -notcontains '正常' -and $script:Events -notcontains 'display') 'Never present an unverified address as ready.'
Write-Output 'PASS: unattended restart and failed-verification reporting.'
