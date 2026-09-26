<#
.SYNOPSIS
  DAYANERA.ai - DESTRUCTIVE local reset. Deletes the PostgreSQL volume
  (all chats, documents index, memory, AUDIT LOG) and optionally runtime files.

.DESCRIPTION
  !!! THIS PERMANENTLY DESTROYS LOCAL DATA !!!
  Nothing is deleted unless the exact confirmation argument is given:
      -ConfirmDestroy "TUM-YEREL-VERIYI-SIL"
  -IncludeRuntimeFiles additionally deletes data\ (raw document versions, media,
  indexes, logs, backups, models) and agent-notes\.
  The ISO corpus folder (iso booklets), source code and .env are never touched.
  Take a backup first: docker exec dayanera-postgres pg_dump -U dayanera dayanera > data\backups\manual.sql

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\reset-local.ps1 -ConfirmDestroy "TUM-YEREL-VERIYI-SIL"
#>
[CmdletBinding()]
param(
    [string]$ConfirmDestroy = '',
    [switch]$IncludeRuntimeFiles
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$phrase = 'TUM-YEREL-VERIYI-SIL'
Write-Host '!!! YIKICI ISLEM: yerel veritabani hacmi silinecek (sohbetler, belge indeksi, hafiza, denetim kaydi) !!!' -ForegroundColor Red
if ($ConfirmDestroy -ne $phrase) {
    Write-Fail "Onay eksik; hicbir sey silinmedi. Devam etmek icin: -ConfirmDestroy `"$phrase`""
    exit 3
}
& (Join-Path $PSScriptRoot 'stop-local.ps1') -KeepDatabase
Write-Step 'PostgreSQL konteyneri ve dayanera_pgdata hacmi siliniyor'
Invoke-Compose @('down', '-v')
Write-Ok 'Veritabani hacmi silindi'
if ($IncludeRuntimeFiles) {
    foreach ($d in @('data', 'agent-notes')) {
        $p = Join-Path $root $d
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Recurse -Force
            Write-Ok "silindi: $d"
        }
    }
}
Write-Ok 'Sifirlama tamamlandi. Yeniden baslatmak icin: scripts\bootstrap.ps1 ve scripts\start-local.ps1'
