# 注册/移除「登录时自动拉起 MME 全栈」的计划任务（不需要管理员）
#
# 需要开机即启（不依赖登录）时，用管理员 + NSSM 的方式，见 deploy/DEPLOY.md。
#
# 用法：
#   .\deploy\local\install-autostart.ps1
#   .\deploy\local\install-autostart.ps1 -Remove

param(
    [switch]$Remove,
    [switch]$WithLlm
)

. "$PSScriptRoot\config.ps1"

$taskName = "MME-Local-Stack"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已移除计划任务 $taskName" -ForegroundColor Green
    return
}

$scriptPath = Join-Path $PSScriptRoot "start-all.ps1"
$arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
if ($WithLlm) { $arguments += " -WithLlm" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $MME_ROOT
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Material Mind Engine 本地模型栈（Embedding / OCR / 网关 / Web）" -Force | Out-Null

Write-Host "已注册计划任务 $taskName（当前用户登录时启动）" -ForegroundColor Green
Write-Host "  立即试跑：Start-ScheduledTask -TaskName $taskName" -ForegroundColor Gray
Write-Host "  取消：    .\deploy\local\install-autostart.ps1 -Remove" -ForegroundColor Gray
