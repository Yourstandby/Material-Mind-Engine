# 一键启动：Embedding → OCR → 网关 → Chroma → Web
#
# LLM 默认不碰（可能已经作为服务在跑）；要一起拉起加 -WithLlm。
#
# 用法：
#   .\deploy\local\start-all.ps1
#   .\deploy\local\start-all.ps1 -WithLlm -Force

param(
    [switch]$WithLlm,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"

$forceArg = @()
if ($Force) { $forceArg = @("-Force") }

if ($WithLlm) {
    Write-Host "`n[1/6] LLM" -ForegroundColor Cyan
    & "$PSScriptRoot\start-llm.ps1" @forceArg
} else {
    Write-Host "`n[0/6] LLM 跳过（用 -WithLlm 一起启动）" -ForegroundColor DarkGray
}

Write-Host "`n[2/6] Embedding" -ForegroundColor Cyan
& "$PSScriptRoot\start-embed.ps1" @forceArg

Write-Host "`n[3/6] OCR" -ForegroundColor Cyan
& "$PSScriptRoot\start-ocr.ps1" @forceArg

Write-Host "`n[4/6] 网关" -ForegroundColor Cyan
& "$PSScriptRoot\start-gateway.ps1" @forceArg

Write-Host "`n[5/6] Chroma（向量库）" -ForegroundColor Cyan
& "$PSScriptRoot\start-chroma.ps1" @forceArg

Write-Host "`n[6/6] MME Web" -ForegroundColor Cyan
& "$PSScriptRoot\start-web.ps1" @forceArg

Write-Host "`n全部就绪：" -ForegroundColor Green
Write-Host "   Web      http://127.0.0.1:$PORT_WEB" -ForegroundColor White
Write-Host "   网关     http://127.0.0.1:$PORT_GATEWAY/v1" -ForegroundColor White
Write-Host "   Chroma   http://127.0.0.1:$PORT_CHROMA" -ForegroundColor White
Write-Host "   体检     .\deploy\local\status.ps1" -ForegroundColor Gray
