# 启动/重启本地 LLM（llama.cpp llama-server）
#
# 说明：
#   - 只绑 127.0.0.1，对外只经网关出 key
#   - KV cache 用 q8_0，省一半显存；槽数/上下文由 .env 的 MME_LLM_NP / MME_LLM_CTX 决定
#   - 启动成功后自动把 .env 的 UPSTREAM_LLM 指到本脚本的端口
#
# 用法：
#   .\deploy\local\start-llm.ps1              # 端口/槽数/上下文都读 .env
#   .\deploy\local\start-llm.ps1 -Port 8010 -Parallel 4 -Ctx 131072

param(
    [int]$Port = 0,
    [int]$Parallel = 0,
    [int]$Ctx = 0,
    [int]$Threads = 0,
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_LLM }
# 默认值来自 .env，改配置不用改脚本
if ($Parallel -eq 0) { $Parallel = [int](Get-Cfg "MME_LLM_NP" "2") }
if ($Ctx -eq 0) { $Ctx = [int](Get-Cfg "MME_LLM_CTX" "65536") }
if ($Threads -eq 0) { $Threads = [int](Get-Cfg "MME_LLM_THREADS" "32") }

$bin = Get-Cfg "MME_LLM_BIN" "D:\models\llamacpp\bin\llama-server.exe"
$gguf = Get-Cfg "MME_LLM_GGUF" "D:\models\gguf\Qwen3-30B-A3B-Instruct-2507-UD-Q4_K_XL.gguf"
$alias = Get-Cfg "MME_LLM_ALIAS" "qwen3-30b"

if (-not (Test-Path $bin)) { throw "找不到 llama-server: $bin（改 .env 的 MME_LLM_BIN）" }
if (-not (Test-Path $gguf)) { throw "找不到模型文件: $gguf（改 .env 的 MME_LLM_GGUF）" }

$health = "http://127.0.0.1:$Port/health"
if (Test-Http $health 3) {
    if (-not $Force) {
        Write-Host "LLM 已在 127.0.0.1:$Port 上运行，跳过（要重启用 -Force）" -ForegroundColor Yellow
        Set-DotEnv "UPSTREAM_LLM" "http://127.0.0.1:$Port/v1"
        return
    }
    Stop-PidFile (Join-Path $MME_LOG "llm.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) {
        Write-Host "端口 $Port 被 PID $owner 占用，尝试停止…" -ForegroundColor Yellow
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

$arguments = @(
    "-m", $gguf,
    "-ngl", "99",
    "-sm", "layer",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "-a", $alias,
    "-c", "$Ctx",
    "-np", "$Parallel",
    "-fa", "on",
    "-t", "$Threads"
)

# KV cache 量化：显存紧张时用 q8_0（省一半），老卡/异常时改成 f16
$kv = Get-Cfg "MME_LLM_KV" "q8_0"
if ($kv -ne "f16") {
    $arguments += @("--cache-type-k", $kv, "--cache-type-v", $kv)
}

Write-Host "启动 LLM（$gguf）端口 $Port，并发 $Parallel…" -ForegroundColor Cyan
$process = Start-Process -FilePath $bin -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $bin) `
    -RedirectStandardOutput (Join-Path $MME_LOG "llm.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "llm.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "llm.pid") -Value $process.Id -Encoding ASCII

if (-not (Wait-Http $health 600 "LLM")) {
    Write-Host "---- 最近日志 ----" -ForegroundColor Yellow
    Get-Content (Join-Path $MME_LOG "llm.err.log") -Tail 25 -ErrorAction SilentlyContinue
    throw "LLM 启动失败"
}

Set-DotEnv "UPSTREAM_LLM" "http://127.0.0.1:$Port/v1"
$models = (Invoke-RestMethod "http://127.0.0.1:$Port/v1/models" -TimeoutSec 10).data.id -join ", "
Write-Host "LLM 就绪：$models" -ForegroundColor Green
Write-Host "上游地址已写入 .env：UPSTREAM_LLM=http://127.0.0.1:$Port/v1" -ForegroundColor Gray

# 网关缓存了上游地址，改了 .env 要重启才生效
if (Test-Http "http://127.0.0.1:$PORT_GATEWAY/health" 3) {
    Write-Host "重启网关以加载新上游…" -ForegroundColor Cyan
    & "$PSScriptRoot\start-gateway.ps1" -Force
}
