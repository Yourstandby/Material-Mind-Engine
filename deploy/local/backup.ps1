# 本地备份：把整个仓库历史打成一个 git bundle（单文件，可拷到移动硬盘/网盘）
#
# 为什么用 bundle 而不是复制目录：
#   - 一个文件就包含全部提交历史，体积小（当前约 0.4 MB）
#   - 不需要远端服务，也没有「未提交的改动丢失」问题
#   - 将来要推到 GitHub/Gitee 时，可以直接从它 clone，历史一条不少
#
# 用法：
#   .\deploy\local\backup.ps1
#   .\deploy\local\backup.ps1 -Dir E:\备份 -Keep 20
#
# 恢复（换台机器 / 仓库被删了）：
#   git clone D:\OC\backup\MME-20261021-1830.bundle MME-restore
#   # 然后照 docs/LOCAL_DEPLOY.md 配 .env、装依赖

param(
    [string]$Dir = "D:\OC\backup",
    [int]$Keep = 10
)

. "$PSScriptRoot\config.ps1"

$git = "git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $candidate = "D:\OC\tools\mingit\cmd\git.exe"
    if (Test-Path $candidate) { $git = $candidate }
    else { throw "找不到 git，先装 git 或把 D:\OC\tools\mingit\cmd 加进 PATH" }
}

# 说明：git 的进度/校验信息走 stderr，而 config.ps1 设了 Stop，
#       这里临时放宽，否则会被当成异常
function Invoke-Git {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $git @Arguments 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return @{ Code = $code; Output = $output }
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$bundle = Join-Path $Dir "MME-$stamp.bundle"

# 有未提交的改动就提醒一下（bundle 只备份已提交的内容）
Push-Location $MME_ROOT
try {
    $dirty = & $git status --porcelain
    if ($dirty) {
        Write-Host "提醒：工作区还有未提交的改动，bundle 只包含已提交的历史。" -ForegroundColor Yellow
        $dirty | Select-Object -First 8 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
        Write-Host "   要一起备份就先 commit。" -ForegroundColor Yellow
    }

    Write-Host "打包仓库历史 …" -ForegroundColor Cyan
    $created = Invoke-Git @("bundle", "create", $bundle, "--all")
    if ($created.Code -ne 0 -or -not (Test-Path $bundle)) {
        throw "bundle 生成失败：$($created.Output)"
    }

    $verified = Invoke-Git @("bundle", "verify", $bundle)
    if ($verified.Code -ne 0) { throw "bundle 校验失败：$($verified.Output)" }
} finally {
    Pop-Location
}

# .env 里有 key，单独存一份（不是给外人看的）
$envCopy = Join-Path $Dir "env-$stamp.txt"
if (Test-Path $MME_ENV) { Copy-Item $MME_ENV $envCopy -Force }

$size = "{0:N1} MB" -f ((Get-Item $bundle).Length / 1MB)
Write-Host ""
Write-Host "备份完成：" -ForegroundColor Green
Write-Host "   仓库历史  $bundle  ($size)" -ForegroundColor White
if (Test-Path $envCopy) { Write-Host "   配置文件  $envCopy  （含 API Key，别外传）" -ForegroundColor White }

# 只保留最近 N 份
$old = Get-ChildItem $Dir -Filter "MME-*.bundle" | Sort-Object LastWriteTime -Descending | Select-Object -Skip $Keep
foreach ($item in $old) {
    Remove-Item $item.FullName -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $Dir ("env-" + ($item.BaseName -replace '^MME-', '') + ".txt")) -Force -ErrorAction SilentlyContinue
}
if ($old) { Write-Host "   已清理 $($old.Count) 份旧备份（保留最近 $Keep 份）" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "恢复：git clone $bundle MME-restore" -ForegroundColor Gray
Write-Host "以后要推远端：git remote add myrepo <地址> ; git push -u myrepo master" -ForegroundColor Gray
