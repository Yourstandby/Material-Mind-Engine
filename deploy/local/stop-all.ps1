# 停止本脚本体系启动的服务（按 logs\*.pid）
#
# 不会停止「别人启动的」llama-server（会话 0 / 服务方式启动的需要管理员权限）。

param([switch]$IncludeLlm)

. "$PSScriptRoot\config.ps1"

foreach ($name in @("web", "gateway", "ocr", "embed", "chroma")) {
    Stop-PidFile (Join-Path $MME_LOG "$name.pid")
}
if ($IncludeLlm) { Stop-PidFile (Join-Path $MME_LOG "llm.pid") }

Write-Host "已按 pid 文件停止。排查残留：" -ForegroundColor Gray
foreach ($port in @($PORT_WEB, $PORT_GATEWAY, $PORT_OCR, $PORT_EMBED, $PORT_CHROMA)) {
    $owner = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    if ($owner) { Write-Host "   端口 $port 仍被 PID $owner 占用" -ForegroundColor Yellow }
}
