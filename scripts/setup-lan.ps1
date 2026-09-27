<#
.SYNOPSIS
  DAYANERA.ai - one-time, idempotent LAN access setup (run as Administrator).

.DESCRIPTION
  Publishes the localhost-only stack on the home LAN as https://dayanera.ai.lan
  through Caddy (deploy\caddy\Caddyfile). Safe to re-run: nothing is duplicated.
    1. .env: adds dayanera.ai.lan to TRUSTED_HOSTS_EXTRA (other values kept).
    2. hosts file on THIS machine: one "<LAN IP> dayanera.ai.lan" line (updated
       in place if the IP changed; never duplicated).
    3. Windows Firewall: inbound TCP 80/443 for caddy.exe only, Private profile,
       remote addresses LocalSubnet only. Broad rules that Windows may have
       auto-created for caddy.exe are disabled. 8000/5173/11434/54329 are NOT opened.
    4. Starts the server (scripts\start-dayanera-server.ps1) and trusts Caddy's
       local root CA in the Windows machine certificate store.
    5. Logon task "DAYANERA Server" (current user) that runs
       scripts\start-dayanera-server.ps1 hidden at every logon (replaced, not duplicated).
  -SetPrivateNetwork additionally marks the current LAN connection as Private
  (required for the Private-only firewall rules to apply; home network only).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-lan.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup-lan.ps1 -SetPrivateNetwork
#>
[CmdletBinding()]
param(
    [string]$HostName = 'dayanera.ai.lan',
    [switch]$SetPrivateNetwork
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
Set-Location $root

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Yonetici olarak calistirin (hosts, guvenlik duvari ve sertifika deposu icin gerekli).'
}

function Get-LanIPv4 {
    $cfg = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } |
        Sort-Object { $_.IPv4DefaultGateway[0].RouteMetric + $_.NetIPv4Interface.InterfaceMetric } | Select-Object -First 1
    if (-not $cfg) { throw 'Varsayilan agi olan etkin bir IPv4 arabirimi bulunamadi.' }
    return $cfg
}

function Find-Caddy {
    $cmd = Get-Command caddy -ErrorAction SilentlyContinue
    if ($cmd) { $p = $cmd.Source } else { $p = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\caddy.exe' }
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    $item = Get-Item -LiteralPath $p
    if ($item.Target) { return [string]($item.Target | Select-Object -First 1) }   # winget link -> real exe
    return $item.FullName
}

$caddy = Find-Caddy
if (-not $caddy) { throw 'caddy.exe bulunamadi. Kurun: winget install --id CaddyServer.Caddy --exact' }
$net = Get-LanIPv4
$lanIp = $net.IPv4Address[0].IPAddress
Write-Step "LAN: $($net.InterfaceAlias) $lanIp/$($net.IPv4Address[0].PrefixLength)"

# 1. .env trusted host --------------------------------------------------
Write-Step '.env TRUSTED_HOSTS_EXTRA'
$envPath = Join-Path $root '.env'
$content = [System.IO.File]::ReadAllText($envPath)
$m = [regex]::Match($content, '(?m)^TRUSTED_HOSTS_EXTRA=([^\r\n]*)')
$hosts = @()
if ($m.Success) { $hosts = @($m.Groups[1].Value.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
if ($hosts -contains $HostName) {
    Write-Ok "zaten mevcut: $HostName"
} else {
    $line = 'TRUSTED_HOSTS_EXTRA=' + (($hosts + $HostName) -join ',')
    if ($m.Success) { $content = $content.Substring(0, $m.Index) + $line + $content.Substring($m.Index + $m.Length) }
    else { if (-not $content.EndsWith("`n")) { $content += "`r`n" }; $content += "$line`r`n" }
    [System.IO.File]::WriteAllText($envPath, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "eklendi: $HostName (arka uc yeniden baslatilmali)"
}

# 2. hosts file on this machine -----------------------------------------
Write-Step "hosts: $HostName -> $lanIp"
$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$lines = @([System.IO.File]::ReadAllLines($hostsPath))
$pattern = '^\s*\d{1,3}(\.\d{1,3}){3}\s+' + [regex]::Escape($HostName) + '(\s|$)'
$wanted = "$lanIp`t$HostName`t# DAYANERA.ai (scripts\setup-lan.ps1)"
$matching = @($lines | Where-Object { $_ -match $pattern })
if ($matching.Count -eq 1 -and $matching[0] -eq $wanted) {
    Write-Ok 'zaten dogru'
} else {
    $kept = @($lines | Where-Object { $_ -notmatch $pattern })
    [System.IO.File]::WriteAllLines($hostsPath, [string[]]($kept + $wanted), (New-Object System.Text.ASCIIEncoding))
    Write-Ok "yazildi: $wanted"
}

# 3. Firewall -------------------------------------------------------------
Write-Step 'Windows Guvenlik Duvari (yalnizca Caddy 80/443, Private, LocalSubnet)'
foreach ($rule in @(@{ Name = 'DAYANERA-Caddy-HTTPS'; Port = 443; Display = 'DAYANERA Caddy HTTPS (443, LAN)' },
                    @{ Name = 'DAYANERA-Caddy-HTTP'; Port = 80; Display = 'DAYANERA Caddy HTTP->HTTPS (80, LAN)' })) {
    $params = @{ DisplayName = $rule.Display; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'
                 LocalPort = $rule.Port; Program = $caddy; Profile = 'Private'; RemoteAddress = 'LocalSubnet'; Enabled = 'True' }
    if (Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue) {
        $params.Remove('DisplayName'); $params.NewDisplayName = $rule.Display   # -DisplayName is a selector in Set-*
        Set-NetFirewallRule -Name $rule.Name @params
        Write-Ok "guncellendi: $($rule.Display)"
    } else {
        New-NetFirewallRule -Name $rule.Name @params | Out-Null
        Write-Ok "olusturuldu: $($rule.Display)"
    }
}
$conn = Get-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex
if ($conn.NetworkCategory -ne 'Private') {
    if ($SetPrivateNetwork) {
        Set-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex -NetworkCategory Private
        Write-Ok "ag profili Private yapildi: $($conn.Name)"
    } else {
        Write-Warn2 "Ag '$($conn.Name)' profili $($conn.NetworkCategory): Private kurallari uygulanmaz, diger cihazlar baglanamaz."
        Write-Warn2 'Ev aginiz ise: scripts\setup-lan.ps1 -SetPrivateNetwork'
    }
} else { Write-Ok "ag profili Private: $($conn.Name)" }

# 4. Start server + trust local CA -------------------------------------------
Write-Step 'Sunucu baslatiliyor'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start-dayanera-server.ps1')
if ($LASTEXITCODE -ne 0) { throw "start-dayanera-server.ps1 basarisiz (cikis $LASTEXITCODE)" }

# Windows auto-creates broad "allow any port / any address" rules the first time a new
# exe listens - i.e. only after Caddy has started. Keep only the scoped DAYANERA rules.
Write-Step 'Caddy icin otomatik olusturulan genis guvenlik duvari kurallari'
$caddyPaths = @($caddy, (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\caddy.exe'))
$broad = @(Get-NetFirewallRule -Direction Inbound -Enabled True | Where-Object { $_.Name -notlike 'DAYANERA-*' } |
    Where-Object { $caddyPaths -contains ($_ | Get-NetFirewallApplicationFilter).Program })
foreach ($r in $broad) { Disable-NetFirewallRule -Name $r.Name; Write-Warn2 "devre disi: $($r.DisplayName) [$($r.Profile)]" }
if ($broad.Count -eq 0) { Write-Ok 'yok' }

Write-Step 'Caddy yerel kok sertifikasi (Windows makine deposu)'
$rootCrt = Join-Path $root 'data\caddy\pki\authorities\local\root.crt'
$deadline = (Get-Date).AddSeconds(30)
while (-not (Test-Path -LiteralPath $rootCrt) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
if (-not (Test-Path -LiteralPath $rootCrt)) { throw "Caddy kok sertifikasi olusmadi: $rootCrt" }
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($rootCrt)
if (Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }) {
    Write-Ok "zaten guvenilir: $($cert.Subject)"
} else {
    Import-Certificate -FilePath $rootCrt -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
    Write-Ok "guvenilir yapildi: $($cert.Subject) ($($cert.Thumbprint))"
}
Write-Host "    Diger cihazlar icin kok sertifika: $rootCrt"

# 5. Logon task -----------------------------------------------------------------
Write-Step 'Oturum acilisinda baslatma gorevi'
$user = "$env:USERDOMAIN\$env:USERNAME"
$script = Join-Path $PSScriptRoot 'start-dayanera-server.ps1'
$log = Join-Path $root 'data\logs\server-start.log'
$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$script' *>> '$log'`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'DAYANERA Server' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $taskPrincipal -Description 'DAYANERA.ai: start-local.ps1 + Caddy (https://dayanera.ai.lan)' -Force | Out-Null
Write-Ok "gorev hazir: 'DAYANERA Server' ($user oturum acinca)"

Write-Host ''
Write-Host "DAYANERA.ai LAN: https://$HostName  ($lanIp)" -ForegroundColor Green
Write-Host "Diger Windows PC hosts satiri: $lanIp $HostName"
