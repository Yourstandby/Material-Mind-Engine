# 放行局域网访问 MME 的两个「对外」端口（需要管理员权限）
#
# 只开：
#   8100  网关（LLM / Embedding / OCR 全走这里，有 API Key）
#   8300  MME Web
#
# 绝不开：8001 Chroma、8002 Embedding、8010 LLM、8101 OCR
#         —— 这几个只绑 127.0.0.1，开了也没用，反而多一个攻击面。
#
# 用法（管理员 PowerShell）：
#   .\deploy\local\open-firewall.ps1            # 放行
#   .\deploy\local\open-firewall.ps1 -Remove    # 撤销

param(
    [switch]$Remove
)

$rules = @(
    @{ Name = "MME Gateway (8100)"; Port = 8100 },
    @{ Name = "MME Web (8300)";     Port = 8300 }
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "需要管理员权限。" -ForegroundColor Yellow
    Write-Host "  开始菜单搜索 PowerShell → 右键「以管理员身份运行」，然后：" -ForegroundColor Gray
    Write-Host "  cd D:\OC\MME" -ForegroundColor White
    Write-Host "  .\deploy\local\open-firewall.ps1" -ForegroundColor White
    exit 1
}

foreach ($rule in $rules) {
    Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue

    if ($Remove) {
        Write-Host "已移除：$($rule.Name)" -ForegroundColor Green
        continue
    }

    New-NetFirewallRule `
        -DisplayName $rule.Name `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort $rule.Port `
        -Action Allow `
        -Profile Private, Domain `
        -RemoteAddress LocalSubnet `
        -Description "Material Mind Engine：给局域网访问，仅限同网段" | Out-Null

    Write-Host "已放行 TCP $($rule.Port)（Private/Domain，仅同网段）→ $($rule.Name)" -ForegroundColor Green
}

if (-not $Remove) {
    Write-Host ""
    Write-Host "验证（在别的电脑上执行）：" -ForegroundColor Cyan
    Write-Host "  Test-NetConnection 172.16.1.210 -Port 8100" -ForegroundColor White
    Write-Host "  Test-NetConnection 172.16.1.210 -Port 8300" -ForegroundColor White
}
