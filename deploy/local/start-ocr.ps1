# 启动本地 OCR 服务（RapidOCR / PP-OCRv4，CPU 即可，首次调用才载模型）

param(
    [int]$Port = 0,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_OCR }
if (-not (Test-Path $MME_VENV_PY)) { throw "找不到虚拟环境：$MME_VENV_PY，先跑 deploy\install.ps1" }

$health = "http://127.0.0.1:$Port/health"
if (Test-Http $health 3) {
    if (-not $Force) { Write-Host "OCR 已在 $Port 上运行，跳过" -ForegroundColor Yellow; return }
    Stop-PidFile (Join-Path $MME_LOG "ocr.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
}

Write-Host "启动 OCR 服务端口 $Port…" -ForegroundColor Cyan
$arguments = @("-m", "uvicorn", "ocr_service.main:app", "--host", "127.0.0.1", "--port", "$Port", "--log-level", "warning")
$process = Start-Process -FilePath $MME_VENV_PY -ArgumentList $arguments -WorkingDirectory $MME_ROOT `
    -RedirectStandardOutput (Join-Path $MME_LOG "ocr.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "ocr.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "ocr.pid") -Value $process.Id -Encoding ASCII
if (-not (Wait-Http $health 120 "OCR")) {
    Get-Content (Join-Path $MME_LOG "ocr.err.log") -Tail 20 -ErrorAction SilentlyContinue
    throw "OCR 启动失败"
}
