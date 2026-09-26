<#
.SYNOPSIS
  DAYANERA.ai - runs all automated tests and prints a clear PASS/FAIL summary.

.DESCRIPTION
  Always:
    1. backend unit + API/DB integration tests (temporary 'dayanera_test' database,
       fake LLM; includes real-corpus, OCR and transcription tests when available)
    2. frontend typecheck, Vitest component tests and production build
    3. Git private-data preflight
    4. localhost-only binding verification of running services
  -Live (stack must be running: scripts\start-local.ps1):
    5. live API end-to-end smoke test with the real local Qwen model
    6. Playwright browser smoke test (installed Microsoft Edge)
  -Restart (with -Live): stops and restarts the stack, then verifies that chats,
    documents, calculations and audit history persisted.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1
  powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1 -Live -Restart
#>
[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$Restart,
    [switch]$SkipFrontend
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$ErrorActionPreference = 'Continue'
$root = Get-RepoRoot
Set-Location $root
$cfg = Get-EnvConfig
Assert-Loopback $cfg
$py = Get-VenvPython
$results = New-Object System.Collections.ArrayList
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logDir = Join-Path $root 'data\logs'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "test-all-$stamp.log"

function Invoke-Gate([string]$name, [scriptblock]$block) {
    Write-Step $name
    $t0 = Get-Date
    # UTF-8 log (Tee-Object in Windows PowerShell 5.1 would write UTF-16)
    & $block 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; [System.IO.File]::AppendAllText($logFile, $line + "`r`n", [System.Text.Encoding]::UTF8) }
    $code = $LASTEXITCODE
    $secs = [int]((Get-Date) - $t0).TotalSeconds
    $status = if ($code -eq 0) { 'PASS' } else { 'FAIL' }
    [void]$results.Add([pscustomobject]@{ Gate = $name; Result = $status; Seconds = $secs; ExitCode = $code })
}

Write-Step 'PostgreSQL (test veritabani icin)'
Invoke-Compose @('up', '-d', 'postgres')
if (-not (Wait-PostgresHealthy 90)) { Write-Fail 'PostgreSQL hazir degil'; exit 1 }

Invoke-Gate 'Backend birim + entegrasyon testleri (pytest)' { & $py -m pytest tests/backend -p no:cacheprovider -q }

if (-not $SkipFrontend) {
    $node = (Get-Command node).Source
    Push-Location (Join-Path $root 'frontend')
    Invoke-Gate 'Frontend tip denetimi (tsc)' { & $node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit }
    Invoke-Gate 'Frontend bilesen testleri (vitest)' { & $node node_modules/vitest/vitest.mjs run }
    Invoke-Gate 'Frontend uretim derlemesi (vite build)' { & $node node_modules/vite/bin/vite.js build }
    Pop-Location
}

Invoke-Gate 'Git ozel veri on kontrolu' { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'git-check-private-data.ps1') }

Invoke-Gate 'Baglanti adresleri yalnizca localhost' {
    $bad = 0
    foreach ($p in @([int]$cfg['APP_PORT'], [int]$cfg['FRONTEND_PORT'], [int]$cfg['POSTGRES_PORT'], 11434)) {
        $r = Test-PortLoopbackOnly $p
        $addrs = (Get-ListenerAddresses $p) -join ', '
        if ($r -eq $false) { Write-Host "  port $p NON-LOOPBACK: $addrs"; $bad++ }
        elseif ($r -eq $true) { Write-Host "  port $p -> $addrs (loopback)" }
        else { Write-Host "  port $p dinlenmiyor" }
    }
    $global:LASTEXITCODE = $bad
}

if ($Live) {
    Invoke-Gate 'Canli API uctan uca duman testi (gercek Qwen)' { & $py -m pytest tests/e2e -m live -k main -p no:cacheprovider -q -s }
    Push-Location (Join-Path $root 'frontend')
    Invoke-Gate 'Playwright tarayici duman testi (Edge)' { & (Get-Command node).Source node_modules/@playwright/test/cli.js test }
    Pop-Location
    if ($Restart) {
        Invoke-Gate 'Yeniden baslatma (stop/start)' {
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stop-local.ps1')
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start-local.ps1')
        }
        Invoke-Gate 'Yeniden baslatma sonrasi kalicilik' { & $py -m pytest tests/e2e -m live -k persistence -p no:cacheprovider -q }
    }
}

Write-Host ''
Write-Host '================ TEST OZETI ================' -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Result -ne 'PASS' })
[System.IO.File]::AppendAllText($logFile, "`r`n$($results | Format-Table -AutoSize | Out-String)", [System.Text.Encoding]::UTF8)
Write-Host "Ayrintili gunluk: $logFile"
if ($failed.Count -gt 0) { Write-Host "SONUC: BASARISIZ ($($failed.Count) kapi)" -ForegroundColor Red; exit 1 }
Write-Host 'SONUC: TUM KAPILAR GECTI' -ForegroundColor Green
exit 0
