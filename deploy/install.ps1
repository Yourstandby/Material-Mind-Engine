<#
  Material Mind Engine — Windows 服务器安装脚本（幂等，可重复执行）

  用法（管理员 PowerShell）：
    .\deploy\install.ps1 -Upstream "http://192.168.1.10:11434/v1" -AdminToken "随机串"

    .\deploy\install.ps1 -InstallServices -OpenFirewall -Port 8000 -GatewayPort 8100

  说明：
    1. 只建环境、装依赖、建目录、生成 .env，不改系统之外的东西
    2. -InstallServices 需要先安装 NSSM 并加入 PATH
    3. 重复执行不会覆盖已有 .env 和已有账号库
#>

param(
  [string]$Root = (Split-Path -Parent $PSScriptRoot),
  [int]$Port = 8000,
  [int]$GatewayPort = 8100,
  [string]$Upstream = "",
  [string]$UpstreamKey = "",
  [string]$AdminToken = "",
  [int]$Workers = 3,
  [switch]$InstallServices,
  [switch]$OpenFirewall
)

$ErrorActionPreference = "Stop"
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

Step "检查 Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "未找到 python，请先安装 Python 3.12 并勾选 Add to PATH" }
$version = & python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$version -lt [version]"3.11") { throw "Python 版本过低（$version），需要 3.11+" }
Ok "Python $version"

$venv = Join-Path $Root ".venv"
$py = Join-Path $venv "Scripts\python.exe"

Step "创建虚拟环境"
if (-not (Test-Path $py)) {
  & python -m venv $venv
  Ok "已创建 $venv"
} else {
  Ok "已存在，跳过"
}

Step "安装依赖（阿里云镜像）"
& $py -m pip install -q --upgrade pip -i https://mirrors.aliyun.com/pypi/simple
& $py -m pip install -q -r (Join-Path $Root "requirements.txt") -i https://mirrors.aliyun.com/pypi/simple
& $py -m pip install -q -r (Join-Path $Root "requirements-web.txt") -i https://mirrors.aliyun.com/pypi/simple
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，请检查网络或改用离线 wheel" }
Ok "依赖安装完成"

Step "创建数据目录"
foreach ($dir in @("inbox", "inbox\_done", "library", "logs")) {
  $path = Join-Path $Root $dir
  if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
}
Ok "inbox / library / logs 就绪"

Step "生成 .env"
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
  Copy-Item (Join-Path $Root ".env.example") $envFile
  Ok "已从 .env.example 创建"
} else {
  Ok ".env 已存在，保留原内容"
}

if ($Upstream) {
  $lines = @(
    "",
    "# 由 install.ps1 写入：模型统一走内网网关",
    "OPENAI_BASE_URL=http://127.0.0.1:$GatewayPort/v1",
    "EMBEDDING_BASE_URL=http://127.0.0.1:$GatewayPort/v1",
    "UPSTREAM_LLM=$Upstream"
  )
  if ($UpstreamKey) { $lines += "UPSTREAM_LLM_KEY=$UpstreamKey" }
  if ($AdminToken) { $lines += "GATEWAY_ADMIN_TOKEN=$AdminToken" }
  Add-Content -Path $envFile -Value $lines -Encoding UTF8
  Ok "已写入上游地址 $Upstream"
}

if ($InstallServices) {
  Step "安装 Windows 服务"
  $nssm = Get-Command nssm -ErrorAction SilentlyContinue
  if (-not $nssm) {
    Warn "未找到 nssm，跳过。下载 https://nssm.cc/download 后解压并把 win64 加入 PATH"
    Warn "或手动执行：$Root\deploy\nssm-commands.txt"
  } else {
    $services = @(
      @{ Name = "MME-Web"; Args = @("-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "$Port", "--workers", "$Workers"); Env = @("WATCH_INBOX=1") },
      @{ Name = "MME-Gateway"; Args = @("-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "$GatewayPort"); Env = @("UPSTREAM_LLM=$Upstream", "GATEWAY_ADMIN_TOKEN=$AdminToken") }
    )
    foreach ($svc in $services) {
      $existing = Get-Service -Name $svc.Name -ErrorAction SilentlyContinue
      if ($existing) { & nssm stop $svc.Name | Out-Null; & nssm remove $svc.Name confirm | Out-Null }
      $svcArgs = $svc.Args
      & nssm install $svc.Name $py @svcArgs | Out-Null
      & nssm set $svc.Name AppDirectory $Root | Out-Null
      & nssm set $svc.Name Start SERVICE_AUTO_START | Out-Null
      & nssm set $svc.Name AppStdout (Join-Path $Root "logs\$($svc.Name).log") | Out-Null
      & nssm set $svc.Name AppStderr (Join-Path $Root "logs\$($svc.Name).err.log") | Out-Null
      $envPairs = $svc.Env | Where-Object { $_ -notmatch "=$" }
      if ($envPairs.Count -gt 0) { & nssm set $svc.Name AppEnvironmentExtra @envPairs | Out-Null }
      & nssm start $svc.Name | Out-Null
      Ok "服务 $($svc.Name) 已安装并启动"
    }
  }
}

if ($OpenFirewall) {
  Step "放行防火墙端口（仅域/专用网络）"
  foreach ($rule in @(@{ N = "MME-Web"; P = $Port }, @{ N = "MME-Gateway"; P = $GatewayPort })) {
    Remove-NetFirewallRule -DisplayName $rule.N -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $rule.N -Direction Inbound -Protocol TCP `
      -LocalPort $rule.P -Action Allow -Profile Domain,Private | Out-Null
    Ok "$($rule.N) 端口 $($rule.P) 已放行"
  }
}

Step "完成"
Write-Host @"

下一步：
  1. 启动 Web：  $py -m uvicorn server.main:app --host 0.0.0.0 --port $Port --workers $Workers
  2. 启动网关：  $py -m uvicorn gateway.main:app --host 0.0.0.0 --port $GatewayPort
  3. 发 API Key：$py -m scripts.make_key --name team-a --rpm 120
  4. 把 Key 写进 .env 的 OPENAI_API_KEY / EMBEDDING_API_KEY
  5. 浏览器打开 http://<服务器IP>:$Port 注册管理员账号

"@ -ForegroundColor Gray
