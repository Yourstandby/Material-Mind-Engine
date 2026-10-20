# 启动模型网关（API Key / 限流 / 用量统计，统一 OpenAI 兼容出口）

param(
    [int]$Port = 0,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_GATEWAY }
if (-not (Test-Path $MME_VENV_PY)) { throw "找不到虚拟环境：$MME_VENV_PY" }

$health = "http://127.0.0.1:$Port/health"
if (Test-Http $health 3) {
    if (-not $Force) { Write-Host "网关已在 $Port 上运行，跳过" -ForegroundColor Yellow; return }
    Stop-PidFile (Join-Path $MME_LOG "gateway.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
}

Write-Host "启动网关端口 $Port…" -ForegroundColor Cyan
$arguments = @("-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "$Port", "--log-level", "warning")
$process = Start-Process -FilePath $MME_VENV_PY -ArgumentList $arguments -WorkingDirectory $MME_ROOT `
    -RedirectStandardOutput (Join-Path $MME_LOG "gateway.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "gateway.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "gateway.pid") -Value $process.Id -Encoding ASCII
if (-not (Wait-Http $health 120 "网关")) {
    Get-Content (Join-Path $MME_LOG "gateway.err.log") -Tail 20 -ErrorAction SilentlyContinue
    throw "网关启动失败"
}
