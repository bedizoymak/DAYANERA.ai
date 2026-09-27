<#
.SYNOPSIS
  DAYANERA.ai - start the home-server deployment (localhost stack + LAN HTTPS proxy).

.DESCRIPTION
  1. Waits for Docker Desktop (it starts with the user session).
  2. Runs scripts\start-local.ps1 (PostgreSQL, migrations, Ollama check,
     backend, UI - all 127.0.0.1 only; idempotent, never deletes data).
  3. Starts Caddy (deploy\caddy\Caddyfile) hidden, if it is not running:
     https://dayanera.ai.lan -> 127.0.0.1:5173 / 127.0.0.1:8000.
  Used by the "DAYANERA Server" logon task created by scripts\setup-lan.ps1.
  Output is appended to data\logs\server-start.log when run by the task.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-dayanera-server.ps1
#>
[CmdletBinding()]
param([int]$DockerWaitSeconds = 300)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
Set-Location $root

function Find-Caddy {
    $cmd = Get-Command caddy -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\caddy.exe'
    if (Test-Path -LiteralPath $p) { return $p }
    return $null
}

Write-Step 'Docker Desktop bekleniyor'
$deadline = (Get-Date).AddSeconds($DockerWaitSeconds)
$ErrorActionPreference = 'Continue'
do {
    docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -eq 0) { break }
    if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        $dd = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (Test-Path -LiteralPath $dd) { Start-Process $dd }
    }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)
$ErrorActionPreference = 'Stop'

Write-Step 'DAYANERA yerel yigini (start-local.ps1)'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start-local.ps1')
if ($LASTEXITCODE -ne 0) { Write-Fail "start-local.ps1 basarisiz (cikis $LASTEXITCODE)"; exit $LASTEXITCODE }

Write-Step 'Caddy (https://dayanera.ai.lan)'
$caddy = Find-Caddy
if (-not $caddy) { Write-Fail 'caddy.exe bulunamadi (winget install CaddyServer.Caddy).'; exit 1 }
if (Test-HttpOk 'http://127.0.0.1:2019/config/' 2) {
    Write-Ok 'Caddy zaten calisiyor'
} else {
    $logDir = Join-Path $root 'data\logs'
    $cfgPath = Join-Path $root 'deploy\caddy\Caddyfile'
    $caddyPid = Start-Detached $caddy @('run', '--config', $cfgPath, '--adapter', 'caddyfile') $root `
        (Join-Path $logDir 'caddy.stdout.log') (Join-Path $logDir 'caddy.stderr.log')
    Set-Content -LiteralPath (Join-Path $root 'data\run\caddy.pid') -Value $caddyPid -Encoding ascii
    if (-not (Wait-Http 'http://127.0.0.1:2019/config/' 30)) { Write-Fail 'Caddy baslamadi. Gunluk: data\logs\caddy.stderr.log'; exit 1 }
    Write-Ok "Caddy calisiyor (PID $caddyPid)"
}
Write-Host ''
Write-Host 'DAYANERA.ai LAN: https://dayanera.ai.lan' -ForegroundColor Green
