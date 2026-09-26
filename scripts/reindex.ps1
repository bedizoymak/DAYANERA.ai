<#
.SYNOPSIS
  DAYANERA.ai - explicit manual full reindex.

.DESCRIPTION
  Rescans all watched folders (new/changed/removed files become versions or
  logical deletions) and re-extracts every active document synchronously.
  Raw versions are never modified; OCR results are reused from the local cache.
  PostgreSQL must be running (scripts\start-local.ps1 or docker compose up -d postgres).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\reindex.ps1
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$cfg = Get-EnvConfig
Assert-Loopback $cfg
$py = Get-VenvPython
Write-Step 'PostgreSQL kontrolu'
Invoke-Compose @('up', '-d', 'postgres')
if (-not (Wait-PostgresHealthy 90)) { Write-Fail 'PostgreSQL hazir degil.'; exit 1 }
Write-Step 'Tam yeniden indeksleme (senkron; OCR sayfalari uzun surebilir)'
Push-Location (Join-Path $root 'backend')
try {
    & $py -m app.cli reindex
    $code = $LASTEXITCODE
} finally { Pop-Location }
if ($code -ne 0) { Write-Fail "Yeniden indeksleme basarisiz (cikis $code)"; exit $code }
Write-Ok 'Yeniden indeksleme tamamlandi.'
