<#
.SYNOPSIS
  DAYANERA.ai - home-server / LAN status (read-only).

.DESCRIPTION
  URL, server LAN IP, Caddy, UI/API health (local and via https://dayanera.ai.lan),
  PostgreSQL, Ollama, configured models + GPU placement (scripts\model-status.ps1),
  listening ports and whether internal services remain loopback-only. Changes nothing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\lan-status.ps1
#>
[CmdletBinding()]
param([string]$HostName = 'dayanera.ai.lan')
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$cfg = Get-EnvConfig
$ErrorActionPreference = 'Continue'

function Show([bool]$ok, [string]$msg) { if ($ok) { Write-Ok $msg } else { Write-Fail $msg } }

Write-Step 'Adres'
$net = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
$lanIp = if ($net) { $net.IPv4Address[0].IPAddress } else { '?' }
$category = if ($net) { (Get-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex).NetworkCategory } else { '?' }
Write-Host "    URL        : https://$HostName"
Write-Host "    Sunucu IP  : $lanIp ($($net.InterfaceAlias), ag profili: $category)"
$resolved = @(Resolve-DnsName $HostName -Type A -ErrorAction SilentlyContinue | Where-Object IPAddress | ForEach-Object IPAddress)
Show ($resolved -contains $lanIp) ("$HostName -> " + ($(if ($resolved) { $resolved -join ', ' } else { 'cozulemedi' })))
if ($category -ne 'Private') { Write-Warn2 'Ag profili Private degil: LAN guvenlik duvari kurallari uygulanmaz (diger cihazlar baglanamaz).' }

Write-Step 'Servisler'
Show (Test-HttpOk 'http://127.0.0.1:2019/config/' 2) 'Caddy (yonetim API 127.0.0.1:2019)'
Show (Test-HttpOk "http://127.0.0.1:$($cfg['FRONTEND_PORT'])/" 3) "Arayuz 127.0.0.1:$($cfg['FRONTEND_PORT'])"
Show (Test-HttpOk "http://127.0.0.1:$($cfg['APP_PORT'])/api/v1/health" 3) "Arka uc 127.0.0.1:$($cfg['APP_PORT'])/api/v1/health"
$pg = (docker inspect -f '{{.State.Health.Status}}' dayanera-postgres 2>$null)
Show ($pg -eq 'healthy') "PostgreSQL (dayanera-postgres): $pg"
Show (Test-HttpOk "$($cfg['OLLAMA_BASE_URL'])/api/version" 3) "Ollama $($cfg['OLLAMA_BASE_URL'])"
try {
    $r = Invoke-WebRequest -Uri "https://$HostName/api/v1/health" -UseBasicParsing -TimeoutSec 5
    Show ($r.StatusCode -eq 200) "https://$HostName/api/v1/health (TLS dogrulandi)"
} catch { Show $false "https://$HostName/api/v1/health: $($_.Exception.Message)" }
Write-Host "    Aktif model: $($cfg['OLLAMA_MODEL'])"

Write-Step 'Dinlenen portlar'
$expect = [ordered]@{ '80' = 'LAN'; '443' = 'LAN'; '2019' = 'lo'; '5173' = 'lo'; '8000' = 'lo'; '11434' = 'lo'; '54329' = 'lo' }
foreach ($key in @($expect.Keys)) {
    $port = [int]$key
    $addrs = @(Get-ListenerAddresses $port)
    $owner = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 |
        ForEach-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName })
    $text = "{0,-6} {1,-22} {2}" -f $port, ($(if ($addrs) { $addrs -join ', ' } else { '(dinlenmiyor)' })), $owner
    if ($expect[$key] -eq 'lo') {
        $lo = Test-PortLoopbackOnly $port
        if ($lo -eq $false) { Write-Fail "$text  <- LOOPBACK DISINDA!" } elseif ($lo) { Write-Ok "$text  yalnizca localhost" } else { Write-Warn2 $text }
    } else {
        Show ($addrs.Count -gt 0) "$text  LAN (Caddy)"
    }
}

Write-Step 'Guvenlik duvari (gelen, etkin, Caddy)'
Get-NetFirewallRule -Direction Inbound -Enabled True | Where-Object { $_.Name -like 'DAYANERA-*' -or $_.DisplayName -match 'caddy' } | ForEach-Object {
    Write-Host ("    {0} | {1} | uzak: {2}" -f $_.DisplayName, $_.Profile, (($_ | Get-NetFirewallAddressFilter).RemoteAddress -join ','))
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'model-status.ps1')
