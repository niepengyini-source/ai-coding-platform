@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 停止前，请确认所有编码成员已保存。默认只停止8000端口的平台服务。
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { $state=Get-Content -LiteralPath 'runtime\server_8000.json' -Raw -Encoding UTF8 | ConvertFrom-Json; if ($state.project_root -ne (Get-Location).Path -or $state.port -ne 8000) { throw '服务记录不属于当前平台。' }; $platformProcess=Get-Process -Id ([int]$state.pid); if ($platformProcess.Path -notin @($state.executable,$state.base_executable)) { throw '进程路径不匹配，未停止。' }; $recordedStart=[DateTimeOffset]::Parse($state.started_at).UtcDateTime; if ([Math]::Abs(($platformProcess.StartTime.ToUniversalTime()-$recordedStart).TotalSeconds) -gt 15) { throw '进程已变化，未停止。' }; $confirmation=Read-Host '输入STOP确认停止平台'; if ($confirmation -eq 'STOP') { Stop-Process -Id $platformProcess.Id; Write-Host '平台已停止，已保存的数据仍保留。' } else { Write-Host '已取消，没有停止。' } } catch { Write-Host ('未执行停止：'+$_.Exception.Message) }"
pause
