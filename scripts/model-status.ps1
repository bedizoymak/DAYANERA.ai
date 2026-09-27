<#
.SYNOPSIS
  DAYANERA.ai - local model status (read-only).

.DESCRIPTION
  Shows the configured model roles (OLLAMA_MODEL / OLLAMA_FAST_MODEL /
  OLLAMA_HEAVY_MODEL), whether each is installed in the local Ollama service,
  which models are currently loaded, and how much of each loaded model is in
  GPU VRAM (full GPU vs. partial CPU offload). Changes nothing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\model-status.ps1
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$cfg = Get-EnvConfig
$ollamaUrl = $cfg['OLLAMA_BASE_URL']
if (-not $ollamaUrl) { $ollamaUrl = 'http://127.0.0.1:11434' }

Write-Step "Ollama ($ollamaUrl)"
try {
    $ver = (Invoke-RestMethod -Uri "$ollamaUrl/api/version" -TimeoutSec 5).version
    $tags = @((Invoke-RestMethod -Uri "$ollamaUrl/api/tags" -TimeoutSec 10).models)
    $ps = @((Invoke-RestMethod -Uri "$ollamaUrl/api/ps" -TimeoutSec 10).models)
} catch {
    Write-Fail "Ollama erisilemiyor: $($_.Exception.Message)"; exit 1
}
Write-Ok "Ollama $ver"

function Get-Installed([string]$name) {
    if (-not $name) { return $null }
    $full = if ($name.Contains(':')) { $name } else { "${name}:latest" }
    return $tags | Where-Object { $_.name -eq $name -or $_.name -eq $full -or $_.model -eq $full } | Select-Object -First 1
}

Write-Step 'Yapilandirilan modeller (.env)'
$roles = [ordered]@{ 'DEFAULT (aktif)' = $cfg['OLLAMA_MODEL']; 'FAST' = $cfg['OLLAMA_FAST_MODEL']; 'HEAVY' = $cfg['OLLAMA_HEAVY_MODEL'] }
foreach ($role in $roles.Keys) {
    $name = $roles[$role]
    if (-not $name) { Write-Host ("    {0,-16} (tanimli degil)" -f $role); continue }
    $m = Get-Installed $name
    if ($m) {
        $d = $m.details
        Write-Ok ("{0,-16} {1}  [{2:N1} GB, {3}, {4}]" -f $role, $name, ($m.size / 1GB), $d.parameter_size, $d.quantization_level)
    } else {
        Write-Warn2 ("{0,-16} {1}  KURULU DEGIL" -f $role, $name)
    }
}

Write-Step 'Kurulu modeller'
foreach ($m in ($tags | Sort-Object name)) {
    Write-Host ("    {0,-45} {1,6:N1} GB  {2}" -f $m.name, ($m.size / 1GB), $m.details.quantization_level)
}

Write-Step 'Bellekte yuklu modeller (GPU / CPU dagilimi)'
if ($ps.Count -eq 0) { Write-Host '    (su anda yuklu model yok - ilk istekte yuklenir)' }
foreach ($m in $ps) {
    $gpuPct = if ($m.size -gt 0) { [math]::Round(100.0 * $m.size_vram / $m.size) } else { 0 }
    $line = "{0}  toplam {1:N1} GB, VRAM {2:N1} GB, baglam {3}" -f $m.name, ($m.size / 1GB), ($m.size_vram / 1GB), $m.context_length
    if ($gpuPct -ge 100) { Write-Ok "$line -> %100 GPU" }
    elseif ($gpuPct -le 0) { Write-Warn2 "$line -> tamamen CPU" }
    else { Write-Warn2 "$line -> KISMI: %$gpuPct GPU / %$(100 - $gpuPct) CPU (yavas)" }
}

Write-Step 'GPU'
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    foreach ($row in (& $smi.Source --query-gpu=name,memory.total,memory.used,memory.free --format=csv,noheader,nounits)) {
        $f = $row.Split(',') | ForEach-Object { $_.Trim() }
        Write-Host ("    {0}: {1} MiB toplam, {2} MiB kullanimda, {3} MiB bos" -f $f[0], $f[1], $f[2], $f[3])
    }
} else {
    Write-Warn2 'nvidia-smi bulunamadi (VRAM bilgisi yok).'
}

$ol = Test-PortLoopbackOnly 11434
if ($ol -eq $false) { Write-Warn2 'Ollama 127.0.0.1 disinda dinliyor! OLLAMA_HOST ortam degiskenini kaldirin.' }
