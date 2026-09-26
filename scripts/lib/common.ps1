# DAYANERA.ai - shared helpers for Windows PowerShell 5.1 and PowerShell 7.
# ASCII only (Windows PowerShell 5.1 reads BOM-less scripts as ANSI).

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:LoopbackHosts = @('127.0.0.1', 'localhost', '::1')

function Get-RepoRoot { return $script:RepoRoot }

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "    [UYARI] $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "    [HATA] $msg" -ForegroundColor Red }

function Read-DotEnv([string]$path) {
    $vars = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $vars }
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $idx = $t.IndexOf('=')
        if ($idx -lt 1) { continue }
        $k = $t.Substring(0, $idx).Trim()
        $v = $t.Substring($idx + 1).Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $vars[$k] = $v
    }
    return $vars
}

function Get-EnvConfig {
    $envPath = Join-Path $script:RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw ".env bulunamadi. Once calistirin: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1"
    }
    return Read-DotEnv $envPath
}

function Assert-Loopback([hashtable]$cfg) {
    foreach ($k in @('APP_HOST', 'FRONTEND_HOST', 'POSTGRES_HOST')) {
        $v = $cfg[$k]
        if ($v -and ($script:LoopbackHosts -notcontains $v)) {
            throw "$k=$v izin verilmiyor. Beta yalnizca 127.0.0.1/localhost uzerinde calisir (0.0.0.0 yasak)."
        }
    }
}

function Get-VenvPython {
    $p = Join-Path $script:RepoRoot 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $p)) { throw "Python sanal ortami yok. Once scripts\bootstrap.ps1 calistirin." }
    return $p
}

function Find-Python312 {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($ver in @('3.12', '3.13')) {
            & py "-$ver" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @('py', "-$ver") }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
        if ([version]$v -ge [version]'3.12') { return @('python') }
    }
    return $null
}

function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $p) { return $p }
    return $null
}

function Test-HttpOk([string]$url, [int]$timeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch { return $false }
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $url 2) { return $true }
        Start-Sleep -Milliseconds 800
    }
    return $false
}

function Get-DockerComposeCmd {
    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) { return @('docker', 'compose') }
    throw "Docker Compose bulunamadi. Docker Desktop kurun ve baslatin."
}

function Invoke-Compose([string[]]$composeArgs) {
    $c = Get-DockerComposeCmd
    $all = @($c[1]) + @('--project-directory', $script:RepoRoot, '-f', (Join-Path $script:RepoRoot 'docker-compose.yml')) + $composeArgs
    & $c[0] @all
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($composeArgs -join ' ') basarisiz (cikis $LASTEXITCODE)." }
}

function Wait-PostgresHealthy([int]$seconds = 90) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $state = (docker inspect -f '{{.State.Health.Status}}' dayanera-postgres 2>$null)
        if ($state -eq 'healthy') { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-ListenerAddresses([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return @() }
    return @($conns | Select-Object -ExpandProperty LocalAddress -Unique)
}

function Test-PortLoopbackOnly([int]$port) {
    $addrs = Get-ListenerAddresses $port
    if ($addrs.Count -eq 0) { return $null }
    foreach ($a in $addrs) {
        if (@('127.0.0.1', '::1') -notcontains $a) { return $false }
    }
    return $true
}

function Start-Detached([string]$exe, [string[]]$arguments, [string]$workDir, [string]$stdoutLog, [string]$stderrLog) {
    # Win32_Process.Create starts a hidden process that does NOT inherit this
    # console's handles, so the caller (a terminal, CI step or pipe) can exit.
    $quoted = ($arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ } }) -join ' '
    $cmdLine = 'cmd.exe /c ""' + $exe + '" ' + $quoted + ' 1>"' + $stdoutLog + '" 2>"' + $stderrLog + '""'
    $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $cmdLine; CurrentDirectory = $workDir; ProcessStartupInformation = $si }
    if ($r.ReturnValue -ne 0) { throw "Surec baslatilamadi (Win32_Process.Create=$($r.ReturnValue)): $exe" }
    return [int]$r.ProcessId
}

function Stop-ProcessTree([int]$procId) {
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
        & taskkill.exe /PID $procId /T /F *> $null
    }
}
