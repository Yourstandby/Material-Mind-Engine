# 启动本地 Embedding 服务（llama.cpp + bge-m3，OpenAI 兼容 /v1/embeddings）
#
# bge-m3 必须用 CLS pooling（--pooling cls），否则中文向量会退化。

param(
    [int]$Port = 0,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_EMBED }

$bin = Get-Cfg "MME_LLM_BIN" "D:\models\llamacpp\bin\llama-server.exe"
$gguf = Get-Cfg "MME_EMBED_GGUF" "D:\models\bge-m3\bge-m3-FP16.gguf"
$alias = Get-Cfg "EMBEDDING_MODEL" "bge-m3"
$pooling = Get-Cfg "MME_EMBED_POOLING" "cls"

if (-not (Test-Path $gguf)) { throw "找不到 embedding 模型: $gguf（改 .env 的 MME_EMBED_GGUF）" }

$health = "http://127.0.0.1:$Port/health"
if (Test-Http $health 3) {
    if (-not $Force) {
        Write-Host "Embedding 已在 127.0.0.1:$Port 上运行，跳过（要重启用 -Force）" -ForegroundColor Yellow
        return
    }
    Stop-PidFile (Join-Path $MME_LOG "embed.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
}

$arguments = @(
    "-m", $gguf,
    "--embeddings",
    "--pooling", $pooling,
    "-c", "8192",
    "-b", "8192",
    "-ub", "8192",
    "-ngl", "99",
    "-t", "16",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-a", $alias
)

Write-Host "启动 Embedding（$gguf，pooling=$pooling）端口 $Port…" -ForegroundColor Cyan
$process = Start-Process -FilePath $bin -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $bin) `
    -RedirectStandardOutput (Join-Path $MME_LOG "embed.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "embed.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "embed.pid") -Value $process.Id -Encoding ASCII

if (-not (Wait-Http $health 180 "Embedding")) {
    Get-Content (Join-Path $MME_LOG "embed.err.log") -Tail 20 -ErrorAction SilentlyContinue
    throw "Embedding 启动失败"
}

& $MME_VENV_PY "$PSScriptRoot\check_embed.py" --base "http://127.0.0.1:$Port/v1" --model $alias
if ($LASTEXITCODE -ne 0) { Write-Host "区分度自检未通过，检查 pooling / 模型是否匹配" -ForegroundColor Red }
