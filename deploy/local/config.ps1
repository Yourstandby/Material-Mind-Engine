# MME 本地部署共享配置
#
# 设计原则：.env 是唯一事实来源。
#   换机器 = 改 .env 里的 MME_* 路径/端口 + 三个模型地址，脚本不用动。
#
# 用法：
#   . "$PSScriptRoot\config.ps1"

$ErrorActionPreference = "Stop"

$MME_ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$MME_ENV = Join-Path $MME_ROOT ".env"
$MME_LOG = Join-Path $MME_ROOT "logs"
$MME_VENV_PY = Join-Path $MME_ROOT ".venv\Scripts\python.exe"

if (-not (Test-Path $MME_LOG)) { New-Item -ItemType Directory -Path $MME_LOG | Out-Null }

function Get-DotEnv {
    $map = @{}
    if (-not (Test-Path $MME_ENV)) { return $map }
    foreach ($line in Get-Content $MME_ENV -Encoding UTF8) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#") -or -not $text.Contains("=")) { continue }
        $index = $text.IndexOf("=")
        $key = $text.Substring(0, $index).Trim()
        $value = $text.Substring($index + 1).Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $map[$key] = $value
    }
    return $map
}

function Get-Cfg {
    param([string]$Name, [string]$Default = "")
    $map = Get-DotEnv
    if ($map.ContainsKey($Name) -and $map[$Name]) { return $map[$Name] }
    return $Default
}

function Set-DotEnv {
    param([string]$Name, [string]$Value)
    $lines = @()
    if (Test-Path $MME_ENV) { $lines = Get-Content $MME_ENV -Encoding UTF8 }
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=") {
            $found = $true
            "$Name=$Value"
        } else { $line }
    }
    if (-not $found) { $out = @($out) + "$Name=$Value" }
    Set-Content -Path $MME_ENV -Value $out -Encoding UTF8
}

function Import-DotEnv {
    foreach ($pair in (Get-DotEnv).GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value, "Process")
    }
}

function Test-Http {
    param([string]$Url, [int]$TimeoutSec = 3)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch { return $false }
}

function Wait-Http {
    param([string]$Url, [int]$TimeoutSec = 120, [string]$Name = $Url)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $Url 3) { Write-Host "   $Name 就绪" -ForegroundColor Green; return $true }
        Start-Sleep -Milliseconds 800
    }
    Write-Host "   $Name 启动超时（$TimeoutSec 秒），看 logs 目录" -ForegroundColor Red
    return $false
}

function Get-Port {
    param([string]$Name, [int]$Default)
    $value = Get-Cfg $Name "$Default"
    return [int]$value
}

function Stop-PidFile {
    param([string]$File)
    if (-not (Test-Path $File)) { return }
    $pidValue = (Get-Content $File -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidValue -and ($pidValue -match '^\d+$')) {
        $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
        if ($process) {
            # 连子进程一起杀：chroma 等入口会 fork 出 python 子进程，只杀父进程会留孤儿占着端口
            & taskkill.exe /F /T /PID $pidValue 2>&1 | Out-Null
            Start-Sleep -Milliseconds 400
            if (Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue) {
                Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
            }
            Write-Host "   已停止 $($process.ProcessName) (PID $pidValue)" -ForegroundColor Green
        }
    }
    Remove-Item $File -ErrorAction SilentlyContinue
}

$PORT_WEB = Get-Port "MME_PORT_WEB" 8300
$PORT_GATEWAY = Get-Port "MME_PORT_GATEWAY" 8100
$PORT_OCR = Get-Port "MME_PORT_OCR" 8101
$PORT_EMBED = Get-Port "MME_PORT_EMBED" 8002
$PORT_LLM = Get-Port "MME_PORT_LLM" 8010
$PORT_CHROMA = Get-Port "MME_PORT_CHROMA" 8001
