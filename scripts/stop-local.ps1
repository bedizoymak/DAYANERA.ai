<#
.SYNOPSIS
  DAYANERA.ai - clean shutdown. Never deletes volumes, documents, messages or logs.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1
  powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1 -KeepDatabase
#>
[CmdletBinding()]
param([switch]$KeepDatabase)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$cfg = Get-EnvConfig
$runDir = Join-Path $root 'data\run'

Write-Step 'Uygulama surecleri durduruluyor'
foreach ($name in @('frontend', 'backend')) {
    $pidFile = Join-Path $runDir "$name.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $procId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        Stop-ProcessTree $procId
        Remove-Item -LiteralPath $pidFile -Force
        Write-Ok "$name durduruldu (PID $procId)"
    }
}
# Fallback: anything still listening on our loopback ports that belongs to this checkout
foreach ($p in @([int]$cfg['APP_PORT'], [int]$cfg['FRONTEND_PORT'])) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
        if ($proc -and ($proc.CommandLine -like '*app.cli*serve*' -or $proc.CommandLine -like '*vite*')) {
            Stop-ProcessTree $c.OwningProcess
            Write-Ok "port $p uzerindeki surec durduruldu (PID $($c.OwningProcess))"
        }
    }
}

if (-not $KeepDatabase) {
    Write-Step 'PostgreSQL konteyneri durduruluyor (veri hacmi korunur)'
    Invoke-Compose @('stop', 'postgres')
    Write-Ok 'PostgreSQL durdu; dayanera_pgdata hacmi korundu'
}
Write-Ok 'Kapatma tamamlandi. Ollama bagimsiz bir Windows servisidir ve calismaya devam eder.'
