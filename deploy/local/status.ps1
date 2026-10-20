# 体检：每个组件是否活着 + 关键配置是否一致
#
# 用法：.\deploy\local\status.ps1

. "$PSScriptRoot\config.ps1"
Import-DotEnv

function Line($name, $url, $ok, $extra = "") {
    $mark = if ($ok) { "OK  " } else { "FAIL" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1,-10} {2} {3}" -f $mark, $name, $url, $extra) -ForegroundColor $color
}

Write-Host "MME 本地部署体检" -ForegroundColor Cyan
Write-Host ("  根目录   {0}" -f $MME_ROOT) -ForegroundColor DarkGray
Write-Host ("  LLM      {0}" -f (Get-Cfg "UPSTREAM_LLM")) -ForegroundColor DarkGray
Write-Host ("  Embed    {0}" -f (Get-Cfg "UPSTREAM_EMBED")) -ForegroundColor DarkGray
Write-Host ("  OCR      {0}" -f (Get-Cfg "UPSTREAM_OCR")) -ForegroundColor DarkGray
Write-Host ""

$llmBase = (Get-Cfg "UPSTREAM_LLM" "http://127.0.0.1:8010/v1").TrimEnd("/")
$embedBase = (Get-Cfg "UPSTREAM_EMBED" "http://127.0.0.1:8002/v1").TrimEnd("/")
$ocrBase = (Get-Cfg "UPSTREAM_OCR" "http://127.0.0.1:8101").TrimEnd("/")

$llmOk = Test-Http "$llmBase/models" 5
$llmName = ""
if ($llmOk) {
    try { $llmName = ((Invoke-RestMethod "$llmBase/models" -TimeoutSec 5).data.id -join ",") } catch { $llmName = "?" }
}
Line "LLM" "$llmBase" $llmOk $llmName

$embedOk = Test-Http "$embedBase/models" 5
$dim = ""
if ($embedOk) {
    try {
        $body = @{ model = (Get-Cfg "EMBEDDING_MODEL" "bge-m3"); input = @("体检") } | ConvertTo-Json
        $r = Invoke-RestMethod "$embedBase/embeddings" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 60
        $dim = "dim=" + $r.data[0].embedding.Count
        $expect = Get-Cfg "EMBEDDING_DIM"
        if ($expect -and $dim -ne "dim=$expect") { $embedOk = $false; $dim += " (期望 $expect)" }
    } catch { $embedOk = $false; $dim = $_.Exception.Message }
}
Line "Embedding" "$embedBase" $embedOk $dim

$ocrOk = Test-Http "$ocrBase/health" 5
Line "OCR" "$ocrBase" $ocrOk

$chromaMode = Get-Cfg "CHROMA_MODE" "embedded"
if ($chromaMode -eq "server") {
    $chromaOk = Test-Http "http://127.0.0.1:$PORT_CHROMA/api/v2/heartbeat" 5
    Line "Chroma" "http://127.0.0.1:$PORT_CHROMA" $chromaOk "server 模式"
} else {
    Line "Chroma" "embedded" $true "单进程模式，未起服务"
}

$gwOk = Test-Http "http://127.0.0.1:$PORT_GATEWAY/health" 5
Line "网关" "http://127.0.0.1:$PORT_GATEWAY" $gwOk

$webOk = Test-Http "http://127.0.0.1:$PORT_WEB/api/health" 10
$chroma = ""
if ($webOk) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$PORT_WEB/api/health" -TimeoutSec 10
        $chroma = "chroma=" + $h.chroma.ok + " embedding=" + $h.embedding.ready
        if (-not $h.chroma.ok) { $webOk = $false }
    } catch { $webOk = $false }
}
Line "Web" "http://127.0.0.1:$PORT_WEB" $webOk $chroma

Write-Host ""
$key = Get-Cfg "OPENAI_API_KEY"
if ($key) {
    $mask = $key.Substring(0, [Math]::Min(10, $key.Length)) + "…"
    Write-Host ("  MME 使用的 Key: {0}" -f $mask) -ForegroundColor DarkGray
    $unauth = 0
    try { Invoke-WebRequest "http://127.0.0.1:$PORT_GATEWAY/v1/models" -UseBasicParsing -TimeoutSec 8 | Out-Null }
    catch { $unauth = $_.Exception.Response.StatusCode.value__ }
    if ($unauth -eq 401) { Write-Host "  网关鉴权   OK（不带 key 返回 401）" -ForegroundColor Green }
    else { Write-Host "  网关鉴权   FAIL（期望 401，实际 $unauth）" -ForegroundColor Red }
} else {
    Write-Host "  .env 还没有 OPENAI_API_KEY，跑 scripts.make_key 生成后填进去" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "日志目录：$MME_LOG" -ForegroundColor DarkGray
