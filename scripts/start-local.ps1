<#
.SYNOPSIS
  DAYANERA.ai - one-command local startup (localhost only).

.DESCRIPTION
  1. Starts the local Docker PostgreSQL container and waits until healthy.
  2. Applies migrations and seeds the initial admin (idempotent).
  3. Checks the native Windows Ollama service and the configured model.
  4. Starts the FastAPI backend on APP_HOST:APP_PORT (loopback only).
  5. Builds (if needed) and starts the browser UI on FRONTEND_HOST:FRONTEND_PORT.
  6. Verifies that every listener is bound to 127.0.0.1 / ::1 only.
  Nothing is ever bound to 0.0.0.0. No data is deleted.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
  powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1 -Dev      # Vite dev server
  powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1 -Open     # open the browser
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$Open,
    [switch]$Rebuild,
    [switch]$NoFrontend
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
Set-Location $root
$cfg = Get-EnvConfig
Assert-Loopback $cfg
$appHost = $cfg['APP_HOST']; $appPort = [int]$cfg['APP_PORT']
$feHost = $cfg['FRONTEND_HOST']; $fePort = [int]$cfg['FRONTEND_PORT']
$pgPort = [int]$cfg['POSTGRES_PORT']
$runDir = Join-Path $root 'data\run'
$logDir = Join-Path $root 'data\logs'
foreach ($d in @($runDir, $logDir)) { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null } }
$py = Get-VenvPython

Write-Step 'PostgreSQL (Docker, yalnizca 127.0.0.1)'
docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) { Write-Fail 'Docker Desktop calismiyor. Baslatip tekrar deneyin.'; exit 1 }
Invoke-Compose @('up', '-d', 'postgres')
if (-not (Wait-PostgresHealthy 120)) { Write-Fail 'PostgreSQL saglikli duruma gelmedi (docker logs dayanera-postgres).'; exit 1 }
Write-Ok "PostgreSQL hazir (127.0.0.1:$pgPort)"

Write-Step 'Migrasyon ve ilk yonetici'
Push-Location (Join-Path $root 'backend')
try {
    & $py -m app.cli migrate
    if ($LASTEXITCODE -ne 0) { throw 'Migrasyon basarisiz' }
    & $py -m app.cli seed
    if ($LASTEXITCODE -ne 0) { throw 'Seed basarisiz' }
} finally { Pop-Location }
Write-Ok 'Sema guncel, yonetici hesabi mevcut'

Write-Step 'Ollama (yerel Windows servisi)'
$ollamaUrl = $cfg['OLLAMA_BASE_URL']
if (-not (Test-HttpOk "$ollamaUrl/api/version" 3)) {
    $ollamaExe = Find-Ollama
    $appExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
    if (Test-Path -LiteralPath $appExe) {
        Write-Warn2 'Ollama calismiyor; Ollama uygulamasi baslatiliyor...'
        Start-Process -FilePath $appExe -WindowStyle Hidden | Out-Null
    } elseif ($ollamaExe) {
        Write-Warn2 'Ollama calismiyor; ollama serve baslatiliyor (127.0.0.1)...'
        $env:OLLAMA_HOST = '127.0.0.1:11434'
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
    }
    if (-not (Wait-Http "$ollamaUrl/api/version" 30)) {
        Write-Warn2 'Ollama erisilemiyor: genel sohbet ve yanit uretimi calismaz; hesap motoru ve arsiv calisir.'
    }
}
if (Test-HttpOk "$ollamaUrl/api/version" 3) {
    $tags = (Invoke-WebRequest -Uri "$ollamaUrl/api/tags" -UseBasicParsing -TimeoutSec 5).Content
    if ($tags -like "*$($cfg['OLLAMA_MODEL'])*") { Write-Ok "Ollama calisiyor, model mevcut: $($cfg['OLLAMA_MODEL'])" }
    else { Write-Warn2 "Model eksik. Calistirin: ollama pull $($cfg['OLLAMA_MODEL'])" }
    $ol = Test-PortLoopbackOnly 11434
    if ($ol -eq $false) { Write-Warn2 'Ollama 127.0.0.1 disinda dinliyor! OLLAMA_HOST ortam degiskenini kaldirin.' }
}

Write-Step 'Arka uc (FastAPI)'
$backendPidFile = Join-Path $runDir 'backend.pid'
if (Test-HttpOk "http://${appHost}:$appPort/api/v1/health" 2) {
    Write-Ok "Arka uc zaten calisiyor: http://${appHost}:$appPort"
} else {
    $backendPid = Start-Detached $py @('-m', 'app.cli', 'serve') (Join-Path $root 'backend') `
        (Join-Path $logDir 'backend.stdout.log') (Join-Path $logDir 'backend.stderr.log')
    Set-Content -LiteralPath $backendPidFile -Value $backendPid -Encoding ascii
    if (-not (Wait-Http "http://${appHost}:$appPort/api/v1/health" 90)) {
        Write-Fail 'Arka uc baslamadi. Gunluk: data\logs\backend.stderr.log'; exit 1
    }
    Write-Ok "Arka uc calisiyor: http://${appHost}:$appPort (PID $backendPid)"
}

if (-not $NoFrontend) {
    Write-Step 'Tarayici arayuzu (Vite)'
    $feDir = Join-Path $root 'frontend'
    $vite = Join-Path $feDir 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) { Write-Fail 'Frontend bagimliliklari yok: scripts\bootstrap.ps1'; exit 1 }
    $fePidFile = Join-Path $runDir 'frontend.pid'
    if (Test-HttpOk "http://${feHost}:$fePort/" 2) {
        Write-Ok "Arayuz zaten calisiyor: http://${feHost}:$fePort"
    } else {
        $node = (Get-Command node).Source
        if ($Dev) {
            $viteArgs = @($vite, '--host', $feHost, '--port', "$fePort", '--strictPort')
        } else {
            $dist = Join-Path $feDir 'dist\index.html'
            $needBuild = $Rebuild -or -not (Test-Path -LiteralPath $dist)
            if (-not $needBuild) {
                $distTime = (Get-Item -LiteralPath $dist).LastWriteTimeUtc
                $newer = Get-ChildItem -LiteralPath (Join-Path $feDir 'src') -Recurse -File | Where-Object { $_.LastWriteTimeUtc -gt $distTime } | Select-Object -First 1
                if ($newer) { $needBuild = $true }
            }
            if ($needBuild) {
                Push-Location $feDir
                try {
                    & $node $vite build | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw 'Frontend derlemesi basarisiz' }
                } finally { Pop-Location }
                Write-Ok 'Arayuz derlendi (dist)'
            }
            $viteArgs = @($vite, 'preview', '--host', $feHost, '--port', "$fePort", '--strictPort')
        }
        $fePid = Start-Detached $node $viteArgs $feDir (Join-Path $logDir 'frontend.stdout.log') (Join-Path $logDir 'frontend.stderr.log')
        Set-Content -LiteralPath $fePidFile -Value $fePid -Encoding ascii
        if (-not (Wait-Http "http://${feHost}:$fePort/" 60)) { Write-Fail 'Arayuz baslamadi. Gunluk: data\logs\frontend.stderr.log'; exit 1 }
        Write-Ok "Arayuz calisiyor: http://${feHost}:$fePort (PID $fePid)"
    }
}

Write-Step 'Baglanti adresi dogrulamasi (yalnizca localhost)'
$bad = $false
$ports = @($appPort, $pgPort)
if (-not $NoFrontend) { $ports += $fePort }
foreach ($p in $ports) {
    $r = Test-PortLoopbackOnly $p
    $addrs = (Get-ListenerAddresses $p) -join ', '
    if ($r -eq $true) { Write-Ok "port $p -> $addrs" }
    elseif ($r -eq $null) { Write-Warn2 "port $p dinlenmiyor" }
    else { Write-Fail "port $p loopback DISINDA dinliyor: $addrs"; $bad = $true }
}
if ($bad) { Write-Fail 'Guvenlik ihlali: servisler durduruluyor.'; & (Join-Path $PSScriptRoot 'stop-local.ps1') -KeepDatabase; exit 2 }

$url = if ($NoFrontend) { "http://${appHost}:$appPort/api/v1/openapi.json" } else { "http://${feHost}:$fePort" }
Write-Host ''
Write-Host "DAYANERA.ai hazir: $url" -ForegroundColor Green
Write-Host "Kullanici: $($cfg['INITIAL_ADMIN_USERNAME']) (parola .env icinde)."
Write-Host 'Durdurmak icin: powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1'
if ($Open) { Start-Process $url }
