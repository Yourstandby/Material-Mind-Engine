# 启动本地重排服务（llama.cpp + bge-reranker-v2-m3，/rerank 打分端点）
#
# cross-encoder：输入 query+文本对，只出相关性分数，不生成文本。
# 与 embedding（bge-m3）是两个独立进程：embedding 走 --embeddings，这里必须 --reranking。

param(
    [int]$Port = 0,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_RERANK }

$bin = Get-Cfg "MME_LLM_BIN" "D:\models\llamacpp\bin\llama-server.exe"
$gguf = Get-Cfg "MME_RERANK_GGUF" "D:\models\bge-reranker-v2-m3\bge-reranker-v2-m3-Q8_0.gguf"
$alias = Get-Cfg "RERANK_MODEL" "bge-reranker-v2-m3"

if (-not (Test-Path $gguf)) { throw "找不到重排模型: $gguf（改 .env 的 MME_RERANK_GGUF）" }

$health = "http://127.0.0.1:$Port/health"
if (Test-Http $health 3) {
    if (-not $Force) {
        Write-Host "重排服务已在 127.0.0.1:$Port 上运行，跳过（要重启用 -Force）" -ForegroundColor Yellow
        return
    }
    Stop-PidFile (Join-Path $MME_LOG "rerank.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
}

$arguments = @(
    "-m", $gguf,
    "--reranking",
    "-c", "8192",
    "-b", "4096",
    "-ub", "1024",
    "-np", "4",
    "-ngl", (Get-Cfg "MME_RERANK_NGL" "99"),
    "-t", "8",
    "-a", $alias,
    "--host", "127.0.0.1",
    "--port", "$Port"
)
if (Get-Cfg "MME_RERANK_DEVICE" "CUDA0") { $arguments += @("--device", (Get-Cfg "MME_RERANK_DEVICE" "CUDA0")) }

Write-Host "启动重排服务端口 $Port（$gguf）…" -ForegroundColor Cyan
$process = Start-Process -FilePath $bin -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $bin) `
    -RedirectStandardOutput (Join-Path $MME_LOG "rerank.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "rerank.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "rerank.pid") -Value $process.Id -Encoding ASCII
if (-not (Wait-Http $health 120 "重排服务")) {
    Get-Content (Join-Path $MME_LOG "rerank.err.log") -Tail 25 -ErrorAction SilentlyContinue
    throw "重排服务启动失败"
}
Write-Host "重排就绪 http://127.0.0.1:$Port/rerank" -ForegroundColor Green
