# 启动 MME Web（FastAPI + 原生前端 + inbox 监听）
#
# 注意：
#   - CHROMA_MODE=server 时可以开多 worker（向量库由独立进程调度）
#   - CHROMA_MODE=embedded 时只能单 worker（多进程会抢 chroma 写锁）
#   - 默认 1 worker，够用；并发高了再加

param(
    [int]$Port = 0,
    [int]$Workers = 1,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_WEB }
if (-not (Test-Path $MME_VENV_PY)) { throw "找不到虚拟环境：$MME_VENV_PY" }

$health = "http://127.0.0.1:$Port/api/health"
if (Test-Http $health 5) {
    if (-not $Force) { Write-Host "MME Web 已在 $Port 上运行，跳过" -ForegroundColor Yellow; return }
    Stop-PidFile (Join-Path $MME_LOG "web.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
}

Write-Host "启动 MME Web 端口 $Port（$Workers worker）…" -ForegroundColor Cyan
$arguments = @("-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "$Port", "--workers", "$Workers", "--log-level", "warning")
$process = Start-Process -FilePath $MME_VENV_PY -ArgumentList $arguments -WorkingDirectory $MME_ROOT `
    -RedirectStandardOutput (Join-Path $MME_LOG "web.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "web.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "web.pid") -Value $process.Id -Encoding ASCII
if (-not (Wait-Http $health 180 "MME Web")) {
    Get-Content (Join-Path $MME_LOG "web.err.log") -Tail 25 -ErrorAction SilentlyContinue
    throw "MME Web 启动失败"
}
