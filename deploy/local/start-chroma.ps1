# 启动 Chroma server（向量库「服务模式」）
#
# 为什么需要它：
#   embedded 模式下每个进程直接开 chroma_db 文件，靠文件锁互斥，
#   只适合单进程。改成 server 后由这一个进程统一管理，
#   多个 worker / 多个项目通过 HTTP 访问，不会再出现 database is locked。
#
# 安全：Chroma 自身没有任何鉴权，默认只绑 127.0.0.1。
#       要给局域网别的机器用，请在前面加一层带鉴权的反向代理（见 docs/LAN_ACCESS.md）。
#
# 用法：
#   .\deploy\local\start-chroma.ps1
#   .\deploy\local\start-chroma.ps1 -Force

param(
    [int]$Port = 0,
    [string]$Bind = "",
    [switch]$Force
)

. "$PSScriptRoot\config.ps1"
Import-DotEnv

if ($Port -eq 0) { $Port = $PORT_CHROMA }
if (-not $Bind) { $Bind = "127.0.0.1" }

$chroma = Join-Path $MME_ROOT ".venv\Scripts\chroma.exe"
if (-not (Test-Path $chroma)) { throw "找不到 chroma 命令：$chroma（先 pip install chromadb）" }

$path = Get-Cfg "CHROMA_PATH" "./chroma_db"
if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $MME_ROOT $path }
if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }

$health = "http://127.0.0.1:$Port/api/v2/heartbeat"
if (Test-Http $health 3) {
    if (-not $Force) {
        Write-Host "Chroma 已在 127.0.0.1:$Port 上运行，跳过（要重启用 -Force）" -ForegroundColor Yellow
        return
    }
    Stop-PidFile (Join-Path $MME_LOG "chroma.pid")
    $owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess)
    if ($owner) {
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

# 关掉匿名统计，别往外发数据
$env:ANONYMIZED_TELEMETRY = "False"

Write-Host "启动 Chroma server：$path → ${Bind}:$Port …" -ForegroundColor Cyan
$process = Start-Process -FilePath $chroma `
    -ArgumentList @("run", "--path", $path, "--host", $Bind, "--port", "$Port") `
    -WorkingDirectory $MME_ROOT `
    -RedirectStandardOutput (Join-Path $MME_LOG "chroma.out.log") `
    -RedirectStandardError (Join-Path $MME_LOG "chroma.err.log") `
    -WindowStyle Hidden -PassThru

Set-Content -Path (Join-Path $MME_LOG "chroma.pid") -Value $process.Id -Encoding ASCII

if (-not (Wait-Http $health 90 "Chroma")) {
    Get-Content (Join-Path $MME_LOG "chroma.err.log") -Tail 25 -ErrorAction SilentlyContinue
    throw "Chroma server 启动失败"
}

$mode = Get-Cfg "CHROMA_MODE" "embedded"
if ($mode -ne "server") {
    Write-Host "提醒：.env 里 CHROMA_MODE=$mode，MME 目前不会用它（要改成 server）" -ForegroundColor Yellow
} else {
    $cfgHost = Get-Cfg "CHROMA_HOST" "127.0.0.1"
    $cfgPort = Get-Cfg "CHROMA_PORT" "$Port"
    Write-Host "MME 将以 server 模式连接 ${cfgHost}:${cfgPort}" -ForegroundColor Green
}
