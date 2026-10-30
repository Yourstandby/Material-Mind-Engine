# Fix git -> github TLS failure on this machine.
# Cause: SteamTools (Watt Toolkit) MITMs github.com with a self-signed root,
# and the schannel backend fails revocation check (CRYPT_E_NO_REVOCATION_CHECK).
# Fix: append the SteamTools root cert to the Mozilla CA bundle, use OpenSSL backend.
$ErrorActionPreference = "Stop"

$srcBundle = "D:\OC\tools\portablegit\usr\ssl\certs\ca-bundle.crt"
$dir = Join-Path $env:USERPROFILE ".git-ca"
$out = Join-Path $dir "ca-bundle.pem"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item $srcBundle $out -Force

$roots = Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -match 'SteamTools|BeyondDimension' }
Write-Host "steamtools roots: $($roots.Count)"

$lines = New-Object System.Collections.Generic.List[string]
foreach ($c in $roots) {
    $lines.Add("# $($c.Subject)")
    $lines.Add("-----BEGIN CERTIFICATE-----")
    $b64 = [Convert]::ToBase64String($c.RawData)
    $n = 0
    while ($n -lt $b64.Length) {
        $len = [Math]::Min(64, $b64.Length - $n)
        $lines.Add($b64.Substring($n, $len))
        $n = $n + 64
    }
    $lines.Add("-----END CERTIFICATE-----")
}
Add-Content -Path $out -Value $lines

git config --global http.sslBackend openssl
git config --global http.sslCAInfo $out
Write-Host "bundle -> $out"
Write-Host "--- ls-remote ---"
git -C D:\OC\MME ls-remote --heads origin
