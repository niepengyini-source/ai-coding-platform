<#
AI辅助编码平台 · 外网链接助手（外网脚本.ps1）
本文件必须保存为“UTF-8 带 BOM”，且不可改成 GBK；否则 Windows PowerShell 5.1 会读错中文并报语法错误。
只做三件事：检查状态、启动平台与临时隧道、重建外网链接。
由项目根目录的“启动外网.cmd”和“重启外网链接.cmd”双击调用；一般不需要手动运行本文件。

  -Mode Start       平台未运行则启动；隧道不可用或链接失效则自动重建；最后显示外网链接
  -Mode Restart     保持平台运行，重建隧道并换用新链接
  -Mode RestartAll  重建隧道并重启平台（本机与局域网地址也会短暂不可用）
  -Mode Status      只检查，不修改任何东西
  -Mode Test        自动化回归测试：复制到临时目录、虚构数据库，不碰真实数据
  -OpenBrowser      -Mode Start 时用浏览器打开本机平台
  -NoPause          结束后不等按键，供其他程序调用
  -Port             平台端口；默认取 .env 的 PLATFORM_PORT
#>
param(
    [ValidateSet('Start', 'Restart', 'RestartAll', 'Status', 'Test')]
    [string]$Mode = 'Status',
    [switch]$OpenBrowser,
    [switch]$NoPause,
    [int]$Port,
    [string]$TestRoot,
    [int]$TestRunSeconds = 25
)
$ErrorActionPreference = 'Stop'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$RealRoot = Split-Path -Parent $PSScriptRoot
# 回归测试（-Mode Test）时把整个平台复制到 TestRoot 下运行，不碰真实数据。
$Root = if ($TestRoot) { (Resolve-Path -LiteralPath $TestRoot).Path } else { $RealRoot }
Set-Location -LiteralPath $Root
$Runtime = Join-Path $Root 'runtime'
if (-not (Test-Path -LiteralPath $Runtime)) { New-Item -ItemType Directory -Path $Runtime | Out-Null }
$PathTools = Join-Path $Runtime 'tools'
$Cloudflared = Join-Path $PathTools 'cloudflared.exe'
$EnvFile = Join-Path $Root '.env'
$ReadyTimeoutSeconds = 90
$FailLogMarker = 'Register tunnel error from server side'

function Read-LogText([string]$Path) {
    # cloudflared 会一直占用日志文件，只能以共享方式读取。
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        try {
            $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
            return $reader.ReadToEnd()
        } finally { $stream.Dispose() }
    } catch {
        return ''
    }
}

function Write-Step([string]$Message) { Write-Host ('  · ' + $Message) -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host ('  √ ' + $Message) -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host ('  ! ' + $Message) -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host ('  × ' + $Message) -ForegroundColor Red }
function Write-Line([string]$Message = '') { Write-Host $Message }

function Confirm-Now([string]$Question) {
    $answer = Read-Host ($Question + '（输入 y 确认）')
    return $answer.Trim() -eq 'y'
}

function Invoke-Web($Url, [int]$TimeoutSec = 20) {
    $request = @{ Uri = $Url; UseBasicParsing = $true; TimeoutSec = $TimeoutSec; MaximumRedirection = 0 }
    if ($PSVersionTable.PSVersion.Major -ge 6) { $request['SkipHttpErrorCheck'] = $true }
    # 连接错误、DNS 未生效、Cloudflare 的 5xx 都按“这次没打开”处理，交给调用方重试。
    try {
        return Invoke-WebRequest @request
    } catch {
        if ($_.Exception.Response) {
            $url = $request.Uri
            return [pscustomobject]@{
                StatusCode = [int]$_.Exception.Response.StatusCode
                Content = ''
                Headers = @{}
                Uri = $url
            }
        }
        return $null
    }
}

function Invoke-PublicCheck([string]$HostName) {
    # 用 Windows 自带的 curl 再验证一次，避免系统代理等设置干扰判断。
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (-not (Test-Path -LiteralPath $curl)) { return $null }
    $target = 'https://' + $HostName + '/login/?r=' + [guid]::NewGuid().ToString('N')
    $code = & $curl -s -o NUL -w '%{http_code}' --max-redirs 0 --max-time 15 $target 2>$null
    return $code
}

function Test-TunnelAliveRemote([string]$HostName) {
    if (Test-TunnelAlive $HostName) { return $true }
    $code = Invoke-PublicCheck $HostName
    return $code -and ($code -match '^(2|3)\d\d$')
}

function Test-PlatformUp {
    $response = Invoke-Web $script:LocalBase 3
    return [bool]$response -and $response.StatusCode -ge 100 -and $response.StatusCode -lt 400
}

function Test-TunnelAlive([string]$HostName) {
    $response = Invoke-Web ('https://' + $HostName + '/login/') 20
    return [bool]$response -and $response.StatusCode -ge 100 -and $response.StatusCode -lt 400
}

function Read-EnvLines {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return @() }
    return @([IO.File]::ReadAllText($EnvFile, [Text.Encoding]::UTF8) -split "`r?`n")
}

function Write-EnvLines([string[]]$Lines) {
    $lines = @($Lines)
    $text = ($lines -join "`n")
    if ($lines.Count -gt 0) { $text += "`n" }
    [IO.File]::WriteAllText($EnvFile, $text, $Utf8)
}

function Get-EnvValue([string]$Name) {
    foreach ($line in (Read-EnvLines)) {
        $trimmed = $line.Trim()
        if ($trimmed -and -not $trimmed.StartsWith('#') -and $trimmed.Contains('=')) {
            $key, $value = $trimmed.Split('=', 2)
            if ($key.Trim() -eq $Name) { return $value.Trim().Trim('"').Trim("'") }
        }
    }
    return ''
}

function Set-EnvValue([string]$Name, [string]$Value) {
    $lines = @(Read-EnvLines)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trimmed = $lines[$i].Trim()
        if ($trimmed -and -not $trimmed.StartsWith('#') -and $trimmed.Contains('=') -and $trimmed.Split('=', 2)[0].Trim() -eq $Name) {
            $lines[$i] = $Name + '=' + $Value
            $found = $true
            break
        }
    }
    if (-not $found) {
        while ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') {
            $lines = @($lines | Select-Object -First ($lines.Count - 1))
        }
        $lines = @($lines) + @($Name + '=' + $Value)
    }
    Write-EnvLines $lines
}

function Add-EnvValue([string]$Name, [string]$Value) {
    $items = @((Get-EnvValue $Name).Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($item in $Value.Split(',')) {
        $item = $item.Trim()
        if ($item -and ($items -notcontains $item)) { $items += $item }
    }
    Set-EnvValue $Name ($items -join ',')
}

function Initialize-Config {
    if ($Port -le 0) {
        $fromEnv = Get-EnvValue 'PLATFORM_PORT'
        $Port = if ($fromEnv -match '^\d+$') { [int]$fromEnv } else { 8000 }
    }
    $script:Port = $Port
    $script:LocalBase = 'http://127.0.0.1:' + $Port
    $hostFromEnv = Get-EnvValue 'PLATFORM_HOST'
    $script:LanHost = if ($hostFromEnv -and $hostFromEnv -ne '127.0.0.1') { $hostFromEnv } else { '' }
    $script:ServerStateFile = Join-Path $Runtime ('server_{0}.json' -f $Port)
    $script:TunnelLog = Join-Path $Runtime 'public-tunnel.err.log'
    $script:TunnelPidFile = Join-Path $Runtime 'public-tunnel.pid'
    $script:LinkFile = Join-Path $Runtime '外网访问地址.txt'
}

function Get-PlatformProcess {
    if (-not (Test-Path -LiteralPath $script:ServerStateFile)) { return $null }
    try {
        $state = [IO.File]::ReadAllText($script:ServerStateFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
    } catch { return $null }
    if ($state.port -ne $script:Port -or $state.project_root -ne $Root) { return $null }
    $process = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    try { if ($process.Path -notin @($state.executable, $state.base_executable)) { return $null } } catch { return $null }
    return $state
}

function Stop-Platform {
    $state = Get-PlatformProcess
    if (-not $state) { return }
    try {
        $state = [IO.File]::ReadAllText($script:ServerStateFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
        $recorded = [DateTimeOffset]::Parse($state.started_at).UtcDateTime
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction Stop
        if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -gt 15) {
            Write-Warn '平台进程的启动时间与记录不符，未停止。'
            return
        }
    } catch {
        Write-Warn '无法核对平台进程，未停止。'
        return
    }
    # 启动脚本 start.cmd 会有一个同命令行的父进程；一起结束，避免留下看不见的副本。
    $targets = @($process.Id)
    $pidFile = Join-Path $Runtime ('pid-{0}.txt' -f $process.Id)
    if (Test-Path -LiteralPath $pidFile) {
        $stored = ((Get-Content -LiteralPath $pidFile -Raw) -replace '\D', '')
        if ($stored) { $targets += [int]$stored }
        Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    }
    $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $process.Id) -ErrorAction SilentlyContinue
    if ($parent -and $parent.ParentProcessId) {
        $parentProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $parent.ParentProcessId) -ErrorAction SilentlyContinue
        if ($parentProcess -and $parentProcess.Name -in @('python.exe', 'pythonw.exe')) { $targets += [int]$parentProcess.ProcessId }
    }
    foreach ($targetPid in ($targets | Select-Object -Unique)) {
        try { Stop-Process -Id $targetPid -Force -ErrorAction Stop } catch {}
    }
    for ($i = 0; $i -lt 60; $i++) {
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    Write-Ok ('平台已停止（原进程 ' + $process.Id + '）。已保存的数据保留。')
}

function Start-PlatformDetached {
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '找不到 .venv\Scripts\python.exe，请先按README建立Python虚拟环境。' }
    $arguments = @('-X', 'utf8', '-B', 'server.py', '--port', $script:Port)
    if ($script:LanHost) { $arguments = @('-X', 'utf8', '-B', 'server.py', '--host', $script:LanHost, '--port', $script:Port) }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $outLog = Join-Path $Runtime ('platform-{0}.out.log' -f $stamp)
    $errLog = Join-Path $Runtime ('platform-{0}.err.log' -f $stamp)
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    # 记录进程号，便于 stop.cmd / 本脚本停止 start.cmd 留下的父进程副本。
    [IO.File]::WriteAllText((Join-Path $Runtime ('pid-{0}.txt' -f $process.Id)), [string]$process.Id, $Utf8)
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-PlatformUp) {
            Write-Ok ('平台已启动（进程 ' + $process.Id + '，日志 runtime\' + (Split-Path -Leaf $errLog) + '）。')
            return
        }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    $tail = ''
    if (Test-Path -LiteralPath $errLog) { $tail = (Get-Content -LiteralPath $errLog -Encoding UTF8 -Tail 6) -join "`n" }
    throw ('平台没有启动成功。' + "`n" + $tail + "`n" + '如提示端口已被占用，请先确认旧平台是否仍在运行。')
}

function Get-TunnelProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $Root + '\runtime\tools\cloudflared.exe*') })
}

function Stop-TunnelProcesses {
    $targets = @(Get-TunnelProcesses)
    if ($targets.Count -eq 0) { return 0 }
    foreach ($target in $targets) {
        try { Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop } catch {}
    }
    for ($i = 0; $i -lt 40; $i++) {
        if (@(Get-TunnelProcesses).Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
    foreach ($left in @(Get-TunnelProcesses)) {
        try { & taskkill.exe /F /T /PID $left.ProcessId 2>&1 | Out-Null } catch {}
    }
    Start-Sleep -Milliseconds 500
    $remaining = @(Get-TunnelProcesses).Count
    if ($remaining -eq 0) { Write-Ok ('已结束旧的隧道进程（' + $targets.Count + ' 个）。') }
    else { Write-Warn ('仍有 ' + $remaining + ' 个旧隧道进程没能结束，将在另一个状态端口上启动新隧道。') }
    return $remaining
}

function Get-FreeMetricsPorts {
    return @(20241..20260 | Where-Object { -not (Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue) })
}

function Start-TunnelDetached {
    $ports = @(Get-FreeMetricsPorts)
    if ($ports.Count -eq 0) { throw '找不到可用的本地状态端口（20241-20260 都被占用）。' }
    $lastError = ''
    foreach ($metricsPort in $ports) {
        $attemptLog = Join-Path $Runtime ('tunnel-attempt-' + $metricsPort + '-' + (Get-Date -Format 'HHmmss') + '.err.log')
        $arguments = @('tunnel', '--url', $script:LocalBase, '--no-autoupdate',
            '--grace-period', '5s', '--metrics', ('127.0.0.1:' + $metricsPort),
            '--logfile', $attemptLog, '--loglevel', 'info')
        # 不走系统代理：先清掉本进程的代理环境变量，避免隧道流量被 Clash 等代理改写。
        $proxyKeys = @('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy')
        $savedProxy = @{}
        foreach ($key in $proxyKeys) {
            $savedProxy[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $null, 'Process')
        }
        try {
            $process = Start-Process -FilePath $Cloudflared -ArgumentList $arguments -WorkingDirectory $Root `
                -WindowStyle Hidden -PassThru
        } finally {
            foreach ($key in $proxyKeys) {
                if ($null -ne $savedProxy[$key]) { [Environment]::SetEnvironmentVariable($key, $savedProxy[$key], 'Process') }
            }
        }
        $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
        $hostname = $null
        $protocol = ''
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 1
            if (Test-Path -LiteralPath $attemptLog) {
                $logText = Read-LogText $attemptLog
                $match = [regex]::Match($logText, 'https://([A-Za-z0-9-]+\.trycloudflare\.com)')
                if ($match.Success) {
                    $hostname = $match.Groups[1].Value.ToLowerInvariant()
                    # 记下实际用的连接协议（quic 或 http2），排障时有用。
                    $protocolMatch = [regex]::Match($logText, 'Initial protocol (\w+)')
                    if ($protocolMatch.Success) { $protocol = $protocolMatch.Groups[1].Value }
                    break
                }
            }
            if ($process.HasExited) { break }
        }
        if ($hostname) {
            $lines = @()
            if (Test-Path -LiteralPath $attemptLog) { $lines = @((Read-LogText $attemptLog) -split "`r?`n") }
            $failures = @($lines | Where-Object { $_ -like ('*' + $FailLogMarker + '*') }).Count
            if (Test-Path -LiteralPath $script:TunnelLog) {
                Rename-Item -LiteralPath $script:TunnelLog -NewName ('public-tunnel-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.err.log') -ErrorAction SilentlyContinue
            }
            $lines | Set-Content -LiteralPath $script:TunnelLog -Encoding UTF8
            Remove-Item -LiteralPath $attemptLog -ErrorAction SilentlyContinue
            [IO.File]::WriteAllText($script:TunnelPidFile, [string]$process.Id, $Utf8)
            Write-Ok ('隧道已启动（进程 ' + $process.Id + '，协议 ' + $(if ($protocol) { $protocol } else { '未知' }) + '，日志 runtime\public-tunnel.err.log）。')
            if ($failures -gt 0) {
                Write-Warn ('Cloudflare 拒绝了这次配对的 ' + $failures + ' 次尝试，新链接可能不生效；下一步会检查。')
            }
            return [pscustomobject]@{ ProcessId = $process.Id; HostName = $hostname; MetricsPort = $metricsPort; Failures = $failures; Protocol = $protocol }
        }
        try { Stop-Process -Id $process.Id -Force -ErrorAction Stop } catch {}
        if (Test-Path -LiteralPath $attemptLog) {
            $lastError = ((Read-LogText $attemptLog) -split "`r?`n" | Select-Object -Last 3) -join ' / '
        }
        Remove-Item -LiteralPath $attemptLog -ErrorAction SilentlyContinue
    }
    throw ('隧道没有启动成功。' + $(if ($lastError) { "`n最后一次输出：" + $lastError }))
}

function Wait-TunnelReady([string]$HostName, [int]$MaxSeconds = 180) {
    # 回归测试：给出文件路径时只等待它出现 OK，不访问外网。
    $simulated = Join-Path $env:TEMP ('tunnel-stub-' + $HostName + '.txt')
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        if ($script:OfflineTest) {
            if ((Test-Path -LiteralPath $simulated) -and ((Get-Content -LiteralPath $simulated -Raw).Trim() -eq 'OK')) { return $true }
            Start-Sleep -Seconds 1
            continue
        }
        if (Test-TunnelAlive $HostName) {
            # 带随机参数：隧道刚建好时 DNS 可能还没切换，避免读到旧缓存。
            return $true
        }
        # 新隧道刚建好时经常要等几十秒才生效：第三次检查后放慢节奏，别过早下结论。
        if ($attempt -le 3) { Start-Sleep -Seconds 3 } else { Start-Sleep -Seconds 10 }
    }
    return $false
}

function Update-PublicHost([string]$HostName) {
    $current = (Get-EnvValue 'PLATFORM_PUBLIC_HOST').ToLowerInvariant()
    Set-EnvValue 'PLATFORM_PUBLIC_HOST' $HostName
    if (-not $script:LanHost) { Add-EnvValue 'PLATFORM_ALLOWED_HOSTS' '127.0.0.1' }
    return $current -ne $HostName
}

function Get-HostProbeScript {
    # 用临时脚本文件而不是 manage.py shell -c，避免命令行引号与转义在不同 PowerShell 版本下出错。
    # 放在项目根目录，与 settings 的 BASE_DIR 一致；脚本自己把根目录加入 sys.path。
    return @(
        'import os, sys',
        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))',
        'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")',
        'import django',
        'django.setup()',
        'from django.conf import settings',
        'print("HOST=" + settings.PLATFORM_PUBLIC_HOST)',
        'print("ALLOWED=" + ",".join(settings.ALLOWED_HOSTS))'
    )
}

function Confirm-PlatformUsesHost([string]$HostName) {
    # 直接询问运行平台所用的配置：它是否已把新域名当作公网地址并允许访问。
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    $probe = Join-Path $Root '.host_probe.py'
    $oldHost = $env:PLATFORM_PUBLIC_HOST
    $env:LOOPBACK_TEST = $null
    $env:PLATFORM_PUBLIC_HOST = $HostName
    $output = ''
    try {
        [IO.File]::WriteAllText($probe, ((Get-HostProbeScript) -join "`n") + "`n", $Utf8)
        $output = (& $python -X utf8 $probe 2>&1 | Out-String)
    } catch {
        return $false
    } finally {
        Remove-Item -LiteralPath $probe -ErrorAction SilentlyContinue
        if ($null -eq $oldHost) { Remove-Item Env:PLATFORM_PUBLIC_HOST -ErrorAction SilentlyContinue }
        else { $env:PLATFORM_PUBLIC_HOST = $oldHost }
    }
    return $output -match ('HOST=' + [regex]::Escape($HostName) + '\s+ALLOWED=[^\r\n]*' + [regex]::Escape($HostName))
}

function Save-StatusFile([string]$HostName, [string]$TunnelPid, [string]$State) {
    $checkTime = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $lines = @(
        ('外网访问地址：https://' + $HostName + '/login/'),
        ('状态：' + $State + '；检查于 ' + $checkTime),
        '电脑需联网且不休眠，平台后台和隧道都需运行。',
        '所有成员使用同一网址；原有账号和项目继续使用。',
        '外网打不开时，双击“重启外网链接.cmd”换新链接；地址变化后需重新发给成员。',
        ('本次隧道进程：' + $TunnelPid + '；停止该进程即可停止外网访问，本机平台仍可使用。'),
        '',
        '以下一行可直接复制发给成员：',
        ('【AI辅助编码平台】请用浏览器打开：https://' + $HostName + '/login/  （请连同 https:// 一起复制；看到“证书”提示时选择继续访问）')
    )
    [IO.File]::WriteAllText($script:LinkFile, (($lines -join "`n") + "`n"), $Utf8)
}

function Get-CurrentTunnelInfo {
    $hostname = (Get-EnvValue 'PLATFORM_PUBLIC_HOST').ToLowerInvariant()
    $ids = @(Get-TunnelProcesses | ForEach-Object { $_.ProcessId })
    return [pscustomobject]@{ HostName = $hostname; ProcessIds = $ids }
}

function Show-Links([string]$HostName, [bool]$LinkChanged) {
    Write-Line ''
    Write-Host '  ──────────────────────────────────────────────' -ForegroundColor DarkGray
    Write-Line '  外网访问地址（发给成员的就是这一个）：'
    Write-Host ('    https://' + $HostName + '/login/') -ForegroundColor Black -BackgroundColor Green
    if ($script:LanHost) { Write-Line ('  同一局域网的备用地址：http://' + $script:LanHost + ':' + $script:Port + '/login/') }
    Write-Line ('  本机地址：' + $script:LocalBase + '/login/')
    Write-Line '  ──────────────────────────────────────────────' -ForegroundColor DarkGray
    if ($LinkChanged) {
        Write-Line ''
        Write-Host '  地址变了：请把上面这一条重新发给成员，大家重新登录一次。' -ForegroundColor Yellow
        Write-Line '  账号、项目、编码记录都保留，不会丢失。'
    }
    Write-Line ''
    Write-Line '  使用期间：隧道在后台运行，本窗口只用来查看地址和关闭隧道。'
    Write-Line ('  复制地址：地址和“可直接发给成员的一句话”已写入 ' + $script:LinkFile + '。')
    Write-Line '  暂停外网：按 Ctrl+C 并输入 Y；本机平台仍在运行。'
    Write-Line '  打不开时：双击“重启外网链接.cmd”，会自动换一条新链接。'
    Write-Line '  电脑需开机、不休眠、联网；免费临时链接不保证长期可用。'
}

function New-LifecycleTunnel {
    if (-not (Test-Path -LiteralPath $Cloudflared)) {
        throw ('找不到 ' + $Cloudflared + '。请确认 runtime\tools 下的 cloudflared.exe 还在。')
    }
    Write-Step '正在结束旧的隧道进程…'
    Write-Step '正在向 Cloudflare 申请新链接（约 30 秒）…'
    $tunnel = Start-TunnelDetached
    Write-Step ('正在等待新链接生效：' + $tunnel.HostName)
    if (Wait-TunnelReady $tunnel.HostName) {
        Write-Ok '新链接已可以访问。'
    } else {
        Write-Warn '新链接暂时没有响应。Cloudflare 免费临时链接有时要等几十秒才生效；若几分钟后仍打不开，请再运行一次“重启外网链接.cmd”，或改用更稳定的方案。'
    }
    $changed = Update-PublicHost $tunnel.HostName
    if (-not (Confirm-PlatformUsesHost $tunnel.HostName)) {
        Write-Warn '平台还在用旧地址，需要重启平台才能生效。'
        Stop-Platform
        Start-Sleep -Milliseconds 800
        Start-PlatformDetached
    } else {
        Write-Ok '平台已识别新地址，不需要重启平台。'
    }
    Save-StatusFile $tunnel.HostName ([string]$tunnel.ProcessId) '正常'
    return $changed
}

function Show-Banner {
    Write-Line ''
    Write-Host '  AI辅助编码平台 · 外网链接助手' -ForegroundColor Cyan
    Write-Line ('  项目：' + $Root)
    Write-Line ('  端口：' + $script:Port + $(if ($script:LanHost) { '（局域网地址 ' + $script:LanHost + '）' } else { '' }))
    Write-Line ''
}

function Invoke-Start {
    Show-Banner
    if (-not (Test-PlatformUp)) {
        $state = Get-PlatformProcess
        if ($state) {
            Write-Warn ('平台进程 ' + $state.pid + ' 还在但网址没有响应，先停止它再启动。')
            Stop-Platform
            Start-Sleep -Milliseconds 800
        }
        Write-Step '正在启动平台…'
        Start-PlatformDetached
    } else {
        Write-Ok ('平台已在运行（进程 ' + (Get-PlatformProcess).pid + '），不重复启动。')
    }
    $tunnel = Get-CurrentTunnelInfo
    $changed = $false
    if ($tunnel.HostName -and $tunnel.ProcessIds.Count -gt 0) {
        Write-Step '检查现在的外网链接是否还能打开…'
        if (Test-TunnelAlive $tunnel.HostName) {
            Write-Ok ('外网链接可用，地址不变：' + $tunnel.HostName)
            Save-StatusFile $tunnel.HostName ($tunnel.ProcessIds -join ',') '正常'
        } else {
            Write-Warn '现在的链接已经失效（隧道假死或过期），自动重建新链接。'
            Stop-TunnelProcesses | Out-Null
            $changed = New-LifecycleTunnel
        }
    } else {
        if ($tunnel.ProcessIds.Count -gt 0) {
            Write-Warn '隧道在运行，但 .env 没有记录公网地址，按新链接处理。'
        } else {
            Write-Step '没有正在运行的隧道，建立一条新的外网链接。'
        }
        Stop-TunnelProcesses | Out-Null
        $changed = New-LifecycleTunnel
    }
    $final = Get-CurrentTunnelInfo
    Show-Links $final.HostName $changed
    if ($OpenBrowser) { try { Start-Process $script:LocalBase } catch {} }
}

function Invoke-Restart([switch]$WithPlatform) {
    Show-Banner
    $current = Get-CurrentTunnelInfo
    if ($current.HostName) { Write-Line ('  现在的地址：https://' + $current.HostName + '/login/') }
    else { Write-Line '  现在还没有外网链接。' }
    Write-Line ''
    if (-not (Confirm-Now '这会换一条新的外网链接，成员需要重新登录。继续吗？')) {
        Write-Line '  已取消，什么都没改。'
        return
    }
    if ($WithPlatform) {
        Stop-Platform
        Start-Sleep -Milliseconds 800
    }
    Stop-TunnelProcesses | Out-Null
    $changed = New-LifecycleTunnel
    # New-LifecycleTunnel 在平台未运行或没有识别新地址时会自己启动平台。
    if (($WithPlatform -or -not (Test-PlatformUp)) -and -not (Test-PlatformUp)) {
        Write-Step '正在启动平台…'
        Start-PlatformDetached
    }
    Show-Links (Get-CurrentTunnelInfo).HostName $true
}

function Show-Status {
    Show-Banner
    $hostname = (Get-EnvValue 'PLATFORM_PUBLIC_HOST').ToLowerInvariant()
    $state = Get-PlatformProcess
    if (Test-PlatformUp) { Write-Ok ('平台运行中：' + $script:LocalBase + ($(if ($state) { '（进程 ' + $state.pid + '）' } else { '' }))) }
    else { Write-Fail '平台没有运行：请双击“启动外网.cmd”。' }
    $tunnel = Get-CurrentTunnelInfo
    if ($tunnel.ProcessIds.Count -gt 0) { Write-Ok ('隧道进程存在：' + ($tunnel.ProcessIds -join ', ')) }
    else { Write-Fail '隧道没有运行：外网无法访问。' }
    if (-not $hostname) {
        Write-Warn '.env 里还没有 PLATFORM_PUBLIC_HOST，说明还没开过外网。'
        return
    }
    Write-Line ('  已记录的地址：https://' + $hostname + '/login/')
    if (-not (Test-PlatformUp) -or $tunnel.ProcessIds.Count -eq 0) { return }
    Write-Step '正在检查外网能否打开…'
    if (Test-TunnelAlive $hostname) {
        Write-Ok '外网可以正常访问。'
    } else {
        Write-Fail '外网打不开：隧道断了或链接已过期。双击“重启外网链接.cmd”换新链接。'
    }
    if ($tunnel.ProcessIds.Count -gt 0) {
        $metricsPort = $null
        foreach ($process in (Get-TunnelProcesses)) {
            $metrics = [regex]::Match($process.CommandLine, '--metrics\s+127\.0\.0\.1?:?(\d+)')
            if ($metrics.Success) { $metricsPort = [int]$metrics.Groups[1].Value }
        }
        if ($metricsPort) {
            $body = Invoke-Web ('http://127.0.0.1:' + $metricsPort + '/metrics') 5
            if ($body) {
                $line = @($body.Content -split "`n" | Where-Object { $_ -like 'cloudflared_tunnel_ha_connections *' }) | Select-Object -First 1
                if ($line -and ($line -split '\s+')[1] -ne '0') { Write-Ok '隧道与 Cloudflare 的连接正常。' }
                elseif ($line) { Write-Warn '隧道进程在，但与 Cloudflare 的连接已断开。' }
            }
        }
    }
}

function Stop-TestPlatform {
    $state = Get-PlatformProcess
    if ($state) {
        try { Stop-Process -Id ([int]$state.pid) -Force -ErrorAction Stop } catch {}
    }
    Stop-TunnelProcesses | Out-Null
}

function Invoke-SelfTest {
    $script:OfflineTest = $true
    $script:TestMode = $true
    $env:LOOPBACK_TEST = '1'
    Show-Banner
    Write-Host '  —— 自动化回归测试（虚构数据，不使用真实数据库）——' -ForegroundColor Cyan

    $before = @(Read-EnvLines)
    $changed = New-LifecycleTunnel
    $after = @(Read-EnvLines)
    $hostname = Get-EnvValue 'PLATFORM_PUBLIC_HOST'
    $diff = Compare-Object -ReferenceObject $before -DifferenceObject $after
    Write-Ok ('新建链接后 .env 只改了 ' + $diff.Count + ' 行：' + (($diff | ForEach-Object { $_.InputObject }) -join ' / '))
    Write-Ok ('新链接：' + $hostname + '，地址已变化：' + $changed)

    $rerun = @(Read-EnvLines)
    Stop-TestPlatform
    $tunnel = Start-TunnelDetached
    Write-Ok ('第二次建链接得到新域名：' + $tunnel.HostName)
    $simulated = Join-Path $env:TEMP ('tunnel-stub-' + $tunnel.HostName + '.txt')
    [IO.File]::WriteAllText($simulated, 'OK', $Utf8)
    $ready = Wait-TunnelReady $tunnel.HostName
    Remove-Item -LiteralPath $simulated -ErrorAction SilentlyContinue
    Write-Ok ('新链接就绪检查通过：' + $ready)
    $final = Update-PublicHost $tunnel.HostName
    Write-Ok ('再次更新 .env，地址变化：' + $final)
    $uses = Confirm-PlatformUsesHost $tunnel.HostName
    Write-Ok ('平台配置已识别新域名：' + $uses)
    Save-StatusFile $tunnel.HostName ([string]$tunnel.ProcessId) '测试'
    Write-Ok '状态文件已写入：' + $script:LinkFile

    Write-Step ('让隧道运行 ' + $TestRunSeconds + ' 秒，检查与 Cloudflare 的配对是否稳定…')
    Start-Sleep -Seconds $TestRunSeconds
    $alive = Test-TunnelAliveRemote $tunnel.HostName
    $process = Get-Process -Id $tunnel.ProcessId -ErrorAction SilentlyContinue
    Write-Ok ('隧道进程仍在：' + [bool]$process + '；Cloudflare 拒绝配对次数：' + $tunnel.Failures + '；外网可打开：' + $alive)

    Stop-TestPlatform
    Write-Ok '测试平台与测试隧道已停止。'
    Write-Line ''
    Write-Ok '全部回归测试步骤执行完毕。'
}

try {
    Initialize-Config
    switch ($Mode) {
        'Status' { Show-Status }
        'Start' { Invoke-Start }
        'Restart' { Invoke-Restart }
        'RestartAll' { Invoke-Restart -WithPlatform }
        'Test' { Invoke-SelfTest }
    }
} catch {
    Write-Line ''
    Write-Fail ('操作没有完成：' + $_.Exception.Message)
    Write-Line '  平台数据没有改动。可把本窗口的内容截图留作排查。'
} finally {
    if (-not $NoPause) {
        Write-Line ''
        Write-Line '  按回车键关闭本窗口（关闭窗口不会停止平台和隧道）。'
        [void](Read-Host)
    }
}
