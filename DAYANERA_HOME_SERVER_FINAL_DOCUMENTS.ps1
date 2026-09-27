#Requires -Version 5.1
<#
DAYANERA.ai - Home Server Installer (idempotent)
Target: Windows 11 / PowerShell 5.1+ / project already extracted in
        C:\Users\EbruHomePC\Documents\DAYANERA.ai

What it does (safe to re-run; resumes after a reboot):
  - Elevates to Administrator
  - Enables WSL + VirtualMachinePlatform for Docker Desktop (stops for a reboot if needed)
  - Installs (only if missing) Git, Python 3.12, Node.js LTS, Ollama, Docker Desktop via winget
  - Normalizes .env paths / loopback settings (existing values and secrets preserved)
  - Rebuilds backend\.venv only if it was copied from another machine and is broken
  - Starts Docker Desktop and waits for the engine
  - Runs scripts\bootstrap.ps1 (Python + npm dependencies, config validation)
  - Starts Ollama (127.0.0.1 only) and pulls qwen3:14b if missing
  - Runs scripts\fetch-local-models.ps1 (faster-whisper, skipped if already present)
  - Starts DAYANERA via scripts\start-local.ps1 and health-checks 5173/8000/54329/11434

Never deletes: .env values, data\, "iso booklets", agent-notes, Docker volumes, model files.
Never runs reset-local.ps1. Nothing is bound to 0.0.0.0.
#>

[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\Users\EbruHomePC\Documents\DAYANERA.ai",
    [switch]$SkipModelPull
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Model roles (verified tags; see scripts\models\ for the heavy GGUF import).
$OllamaModel     = "qwen3:14b-q4_K_M"       # DEFAULT: official Ollama tag, fits 12 GB VRAM
$OllamaFastModel = "qwen3.5:9b-q4_K_M"      # FAST: official Ollama tag
$OllamaHeavyModel = "qwen3.8-27b-q2_K"      # HEAVY: local name created from the Modelfile below
$HeavySource     = "hf.co/bartowski/Qwen3.8-27B-GGUF:Q2_K"   # exact Q2_K GGUF (Ollama has no official Q2_K)
$HeavyModelfile  = "scripts\models\qwen3.8-27b-q2_K.Modelfile"
# Earlier installer defaults: replaced by the new default; anything else is a user choice and kept.
$LegacyDefaultModels = @("qwen2.5:14b-instruct-q4_K_M", "qwen3:14b")
$LogRoot = "C:\DAYANERA-INSTALL"
$LogFile = Join-Path $LogRoot ("install_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$CredFile = Join-Path $LogRoot "INITIAL_ADMIN_CREDENTIALS.txt"

function Write-Step {
    param([string]$Message)
    Write-Host ("`n[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

# Runs a native command, streams stdout+stderr to the console/transcript and
# returns the exit code. stderr lines must not abort the installer (PS 5.1
# turns them into ErrorRecords under ErrorActionPreference=Stop).
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-RepoScript {
    param([string]$Script, [string[]]$Arguments = @())
    $all = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallRoot $Script)) + $Arguments
    Push-Location $InstallRoot
    try { return (Invoke-Native -FilePath "powershell.exe" -Arguments $all) } finally { Pop-Location }
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$FriendlyName
    )
    Write-Step "Checking $FriendlyName..."
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $list = winget list --id $Id --exact --accept-source-agreements 2>$null | Out-String
        $listed = ($LASTEXITCODE -eq 0 -and $list -match [regex]::Escape($Id))
    } finally { $ErrorActionPreference = $prev }
    if ($listed) {
        Write-Info "$FriendlyName already installed."
        return
    }

    Write-Step "Installing $FriendlyName..."
    $rc = Invoke-Native -FilePath "winget" -Arguments @("install", "--id", $Id, "--exact", "--silent",
        "--accept-package-agreements", "--accept-source-agreements")
    # -1978335189 = APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE / already installed
    if ($rc -ne 0 -and $rc -ne -1978335189) {
        throw "winget installation failed: $FriendlyName ($Id), exit code $rc"
    }
    Refresh-Path
}

function Get-EnvValue {
    param([string]$File, [string]$Key)
    foreach ($line in [System.IO.File]::ReadAllLines($File)) {
        if ($line -match ("^\s*" + [regex]::Escape($Key) + "=(.*)$")) { return $Matches[1].Trim() }
    }
    return $null
}

# Sets KEY=VALUE only when the value differs; writes UTF-8 without BOM
# (same encoding as scripts\bootstrap.ps1). Other lines are left untouched.
function Set-EnvLine {
    param([string]$File, [string]$Key, [string]$Value)
    $content = [System.IO.File]::ReadAllText($File)
    $pattern = "(?m)^" + [regex]::Escape($Key) + "=[^\r\n]*"
    $m = [regex]::Match($content, $pattern)
    if ($m.Success) {
        if ($m.Value -eq "$Key=$Value") { return }
        $content = $content.Substring(0, $m.Index) + "$Key=$Value" + $content.Substring($m.Index + $m.Length)
        Write-Info "  .env: $Key updated"
    } else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) { $content += "`r`n" }
        $content += "$Key=$Value`r`n"
        Write-Info "  .env: $Key added"
    }
    [System.IO.File]::WriteAllText($File, $content, (New-Object System.Text.UTF8Encoding($false)))
}

function Test-DockerEngine {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        docker info --format "{{.ServerVersion}}" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference = $prev }
}

function Wait-Docker {
    param([int]$TimeoutSeconds = 300)
    Write-Step "Waiting for Docker engine (up to $TimeoutSeconds s)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Refresh-Path
        if ((Get-Command docker -ErrorAction SilentlyContinue) -and (Test-DockerEngine)) {
            Write-Info "Docker engine is ready."
            return $true
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Find-OllamaExe {
    Refresh-Path
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @((Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
                     (Join-Path $env:ProgramFiles "Ollama\ollama.exe"))) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Test-Ollama {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Stop-Installer {
    param([int]$Code)
    try { Stop-Transcript | Out-Null } catch {}
    exit $Code
}

# ---------------------------------------------------------------------
# 0. Elevate
# ---------------------------------------------------------------------
if (-not (Test-Admin)) {
    Write-Host "Administrator rights are required. Reopening as Administrator..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-InstallRoot", "`"$InstallRoot`"")
    if ($SkipModelPull) { $argList += "-SkipModelPull" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList ($argList -join " ")
    exit
}

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
Start-Transcript -Path $LogFile -Append | Out-Null

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "      DAYANERA.ai - HOME SERVER INSTALLER" -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "Install root : $InstallRoot"
Write-Host "Log          : $LogFile"

# ---------------------------------------------------------------------
# 1. Pre-flight
# ---------------------------------------------------------------------
Write-Step "Pre-flight checks..."
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot "scripts\bootstrap.ps1"))) {
    throw "scripts\bootstrap.ps1 not found under $InstallRoot. The project must already be extracted there."
}
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path.TrimEnd("\")

if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw "winget was not found. Install/update 'App Installer' from Microsoft Store, then rerun."
}

$freeGB = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
Write-Info "C: free space: $freeGB GB"
$qwenPresent = Test-Path -LiteralPath (Join-Path $env:USERPROFILE (".ollama\models\manifests\registry.ollama.ai\library\" + $OllamaModel.Replace(":", "\")))
if ($freeGB -lt 35 -and -not $qwenPresent) {
    throw "At least 35 GB free space on C: is required (Docker, dependencies, ~9 GB Qwen model)."
}

# ---------------------------------------------------------------------
# 2. Windows features for Docker / WSL2
# ---------------------------------------------------------------------
Write-Step "Checking Windows virtualization components required by Docker Desktop..."
$restartNeeded = $false
foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature).State
    if ($state -eq "Enabled") {
        Write-Info "$feature already enabled."
    } else {
        Write-Info "Enabling: $feature (current state: $state)"
        $result = Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart
        if ($result.RestartNeeded -or "$state" -eq "EnablePending") { $restartNeeded = $true }
    }
}

# ---------------------------------------------------------------------
# 3. Packages (skipped when already installed)
# ---------------------------------------------------------------------
Install-WingetPackage -Id "Git.Git"               -FriendlyName "Git"
Install-WingetPackage -Id "Python.Python.3.12"    -FriendlyName "Python 3.12"
Install-WingetPackage -Id "OpenJS.NodeJS.LTS"     -FriendlyName "Node.js LTS"
Install-WingetPackage -Id "Ollama.Ollama"         -FriendlyName "Ollama"
Install-WingetPackage -Id "Docker.DockerDesktop"  -FriendlyName "Docker Desktop"
Refresh-Path

if ($restartNeeded) {
    Write-Host ""
    Write-Host "===================================================================" -ForegroundColor Yellow
    Write-Host "Windows features (WSL / VirtualMachinePlatform) were just enabled." -ForegroundColor Yellow
    Write-Host "REBOOT Windows now, then RUN THIS SAME SCRIPT AGAIN." -ForegroundColor Yellow
    Write-Host "Already installed software will be skipped." -ForegroundColor Yellow
    Write-Host "===================================================================" -ForegroundColor Yellow
    Stop-Installer 3010
}

# Make sure the WSL2 kernel is current (required by Docker Desktop's WSL2 backend).
Write-Step "Updating WSL kernel (best effort)..."
$rc = Invoke-Native -FilePath "wsl.exe" -Arguments @("--update")
if ($rc -ne 0) { Write-Info "WARNING: 'wsl --update' returned $rc. Docker Desktop may prompt for it." }

# ---------------------------------------------------------------------
# 4. Validate base tools
# ---------------------------------------------------------------------
Write-Step "Validating installed tools..."
$toolChecks = [ordered]@{ git = @("--version"); py = @("-3.12", "--version"); node = @("--version");
                          npm = @("--version"); docker = @("--version") }
foreach ($name in $toolChecks.Keys) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        Invoke-Native -FilePath $name -Arguments $toolChecks[$name] | Out-Null
    } else {
        Write-Info "WARNING: $name is not visible in PATH yet."
    }
}
$ollamaExe = Find-OllamaExe
if ($ollamaExe) { Write-Info "ollama: $ollamaExe" } else { throw "ollama.exe not found after installation." }

# ---------------------------------------------------------------------
# 5. Data directories (created only if missing; never emptied)
# ---------------------------------------------------------------------
Write-Step "Ensuring DAYANERA data directories exist..."
foreach ($d in @("data", "data\logs", "data\backups", "data\models", "data\run", "iso booklets", "agent-notes")) {
    $p = Join-Path $InstallRoot $d
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p | Out-Null; Write-Info "created: $d" }
}

# ---------------------------------------------------------------------
# 6. Normalize an existing .env BEFORE bootstrap (bootstrap validates it
#    with 'app.cli check-config'). A missing .env is created by bootstrap.
# ---------------------------------------------------------------------
$envFile = Join-Path $InstallRoot ".env"

function Update-EnvForServer {
    Write-Step "Normalizing .env for this home server (localhost-only)..."
    $esc = { param($p) $p.Replace("\", "\\") }
    Set-EnvLine -File $envFile -Key "PROJECT_ROOT"      -Value (& $esc $InstallRoot)
    Set-EnvLine -File $envFile -Key "ISO_BOOKLETS_PATH" -Value (& $esc (Join-Path $InstallRoot "iso booklets"))
    Set-EnvLine -File $envFile -Key "DATA_ROOT"         -Value (& $esc (Join-Path $InstallRoot "data"))
    Set-EnvLine -File $envFile -Key "AGENT_NOTES_PATH"  -Value (& $esc (Join-Path $InstallRoot "agent-notes"))
    Set-EnvLine -File $envFile -Key "APP_HOST"              -Value "127.0.0.1"
    Set-EnvLine -File $envFile -Key "FRONTEND_HOST"         -Value "127.0.0.1"
    Set-EnvLine -File $envFile -Key "POSTGRES_HOST"         -Value "127.0.0.1"
    Set-EnvLine -File $envFile -Key "OLLAMA_BASE_URL"       -Value "http://127.0.0.1:11434"
    $current = Get-EnvValue -File $envFile -Key "OLLAMA_MODEL"
    if (-not $current -or $LegacyDefaultModels -contains $current) {
        Set-EnvLine -File $envFile -Key "OLLAMA_MODEL" -Value $OllamaModel
    } elseif ($current -ne $OllamaModel) {
        Write-Info "  .env: OLLAMA_MODEL=$current kept (user choice; default would be $OllamaModel)"
    }
    foreach ($pair in @(@("OLLAMA_FAST_MODEL", $OllamaFastModel), @("OLLAMA_HEAVY_MODEL", $OllamaHeavyModel),
                        @("OLLAMA_THINK", "false"))) {
        if (-not (Get-EnvValue -File $envFile -Key $pair[0])) { Set-EnvLine -File $envFile -Key $pair[0] -Value $pair[1] }
    }
    Set-EnvLine -File $envFile -Key "VITE_API_PROXY_TARGET" -Value "http://127.0.0.1:8000"
    Write-Info ".env normalized (secrets and other values unchanged)."
}

if (Test-Path -LiteralPath $envFile) { Update-EnvForServer }

# ---------------------------------------------------------------------
# 7. Python venv copied from another machine is unusable: rebuild it.
#    (It only contains reinstallable packages; it is renamed, not deleted.)
# ---------------------------------------------------------------------
$venvDir = Join-Path $InstallRoot "backend\.venv"
$venvCfg = Join-Path $venvDir "pyvenv.cfg"
if (Test-Path -LiteralPath $venvCfg) {
    $homeLine = (Get-Content -LiteralPath $venvCfg | Where-Object { $_ -match "^\s*home\s*=" } | Select-Object -First 1)
    $venvHome = if ($homeLine) { ($homeLine -split "=", 2)[1].Trim() } else { "" }
    if (-not $venvHome -or -not (Test-Path -LiteralPath (Join-Path $venvHome "python.exe"))) {
        $stale = "$venvDir.stale_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
        Write-Step "backend\.venv points to a missing Python ($venvHome). Moving it to $stale and rebuilding."
        Move-Item -LiteralPath $venvDir -Destination $stale
    }
}

# ---------------------------------------------------------------------
# 8. Start Docker Desktop (bootstrap requires a running engine)
# ---------------------------------------------------------------------
Write-Step "Starting Docker Desktop..."
if (-not (Test-DockerEngine)) {
    $dockerDesktop = @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
                       "$env:LOCALAPPDATA\Docker\Docker Desktop.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($dockerDesktop) {
        if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) { Start-Process $dockerDesktop }
    } else {
        Write-Info "WARNING: Docker Desktop executable was not found in the usual paths."
    }
}
if (-not (Wait-Docker -TimeoutSeconds 300)) {
    Write-Host ""
    Write-Host "===================================================================" -ForegroundColor Yellow
    Write-Host "Docker engine is not ready." -ForegroundColor Yellow
    Write-Host "- On first start, Docker Desktop shows a license / sign-in screen:" -ForegroundColor Yellow
    Write-Host "  accept it (sign-in can be skipped) and choose the WSL 2 backend." -ForegroundColor Yellow
    Write-Host "- After a fresh Docker/WSL install, sign out/in or REBOOT Windows." -ForegroundColor Yellow
    Write-Host "Then RUN THIS SAME SCRIPT AGAIN; it resumes safely." -ForegroundColor Yellow
    Write-Host "===================================================================" -ForegroundColor Yellow
    Stop-Installer 3010
}

# ---------------------------------------------------------------------
# 8b. Hyper-V/WSL (winnat) reserves random TCP ranges at boot; if one covers
#     POSTGRES_PORT, Docker cannot publish it ("access forbidden"). Add a
#     permanent administered exclusion so the port stays usable.
# ---------------------------------------------------------------------
$pgPort = 54329
if (Test-Path -LiteralPath $envFile) {
    $v = Get-EnvValue -File $envFile -Key "POSTGRES_PORT"
    if ($v -match "^\d+$") { $pgPort = [int]$v }
}
$excluded = netsh interface ipv4 show excludedportrange protocol=tcp | Out-String
if ($excluded -notmatch ("(?m)^\s*{0}\s+{0}\s+\*" -f $pgPort)) {
    Write-Step "Reserving TCP port $pgPort so Hyper-V/WSL cannot claim it..."
    Invoke-Native -FilePath "net.exe" -Arguments @("stop", "winnat") | Out-Null
    $rc = Invoke-Native -FilePath "netsh.exe" -Arguments @("int", "ipv4", "add", "excludedportrange",
        "protocol=tcp", "startport=$pgPort", "numberofports=1")
    Invoke-Native -FilePath "net.exe" -Arguments @("start", "winnat") | Out-Null
    if ($rc -ne 0) { Write-Info "WARNING: could not reserve port $pgPort (exit $rc)." }
}

# ---------------------------------------------------------------------
# 9. Bootstrap repository (creates .env if missing, venv, pip, npm ci)
# ---------------------------------------------------------------------
Write-Step "Running DAYANERA scripts\bootstrap.ps1..."
$envExisted = Test-Path -LiteralPath $envFile
$rc = Invoke-RepoScript -Script "scripts\bootstrap.ps1"
if (-not $envExisted -and (Test-Path -LiteralPath $envFile)) {
    # bootstrap adapts paths from .env.example itself; enforce the server values anyway.
    Update-EnvForServer
}
if ($rc -ne 0) { throw "DAYANERA bootstrap failed with exit code $rc (see output above)." }

# ---------------------------------------------------------------------
# 10. Replace the beta admin password "1234" - only when no database has
#     been created yet (seed runs once; changing .env later has no effect).
# ---------------------------------------------------------------------
if ((Get-EnvValue -File $envFile -Key "INITIAL_ADMIN_PASSWORD") -eq "1234") {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    docker volume inspect dayanera_pgdata *> $null
    $volumeExists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prev
    if ($volumeExists) {
        Write-Info "WARNING: INITIAL_ADMIN_PASSWORD is still 1234 but database volume 'dayanera_pgdata' already exists."
        Write-Info "         Not changing it; change the admin password inside DAYANERA instead."
    } else {
        Write-Step "Fresh database: generating a strong initial admin password..."
        $alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789_-"
        $bytes = New-Object byte[] 24
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $generated = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
        Set-EnvLine -File $envFile -Key "INITIAL_ADMIN_PASSWORD" -Value $generated
        $user = Get-EnvValue -File $envFile -Key "INITIAL_ADMIN_USERNAME"
        if (-not $user) { $user = "admin" }
        @"
DAYANERA.ai initial local admin
Username: $user
Password: $generated

Generated automatically. Keep it private; delete this file after storing the password securely.
"@ | Set-Content -Path $CredFile -Encoding UTF8
        Write-Info "Initial admin password saved to: $CredFile"
    }
}

# ---------------------------------------------------------------------
# 11. Ollama (native Windows, 127.0.0.1 only)
# ---------------------------------------------------------------------
Write-Step "Checking Ollama service..."
if (-not (Test-Ollama)) {
    $appExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"
    if (Test-Path -LiteralPath $appExe) {
        Write-Info "Starting Ollama app..."
        Start-Process -FilePath $appExe -WindowStyle Hidden
    } else {
        Write-Info "Starting 'ollama serve' on 127.0.0.1:11434..."
        $env:OLLAMA_HOST = "127.0.0.1:11434"
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
    }
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Ollama) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
}
if (-not (Test-Ollama)) { throw "Ollama is not reachable on http://127.0.0.1:11434." }
Write-Info "Ollama is running."

$machineOllamaHost = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "Machine")
$userOllamaHost = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", "User")
foreach ($h in @($machineOllamaHost, $userOllamaHost)) {
    if ($h -and $h -notmatch "^(http://)?(127\.0\.0\.1|localhost)(:\d+)?$") {
        Write-Info "WARNING: OLLAMA_HOST=$h exposes Ollama beyond localhost. Remove it for this deployment."
    }
}

# ---------------------------------------------------------------------
# 12. Qwen models: install only what is missing (never deletes models)
#     The names come from .env so a user-chosen model is installed too.
# ---------------------------------------------------------------------
function Test-OllamaModel {
    param([string]$Name)
    $full = if ($Name.Contains(":")) { $Name } else { "${Name}:latest" }
    try { $models = @((Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 10).models) } catch { return $false }
    return [bool]($models | Where-Object { $_.name -eq $Name -or $_.name -eq $full -or $_.model -eq $full })
}

function Install-OllamaModel {
    param([string]$Role, [string]$Name, [string]$Size, [bool]$Required)
    Write-Step "Model [$Role]: $Name"
    if (Test-OllamaModel $Name) { Write-Info "  already installed - skipped."; return }
    if ($SkipModelPull) { Write-Info "  MISSING (-SkipModelPull). Run later: ollama pull $Name"; return }
    Write-Info "  downloading ($Size)..."
    $rc = Invoke-Native -FilePath $ollamaExe -Arguments @("pull", $Name)
    if ($rc -eq 0) { Write-Info "  installed: $Name"; return }
    if ($Required) { throw "ollama pull $Name failed with exit code $rc" }
    Write-Info "  WARNING: ollama pull $Name failed (exit $rc). DAYANERA still runs on the default model."
}

$envDefault = Get-EnvValue -File $envFile -Key "OLLAMA_MODEL";      if (-not $envDefault) { $envDefault = $OllamaModel }
$envFast    = Get-EnvValue -File $envFile -Key "OLLAMA_FAST_MODEL"; if (-not $envFast)    { $envFast = $OllamaFastModel }
$envHeavy   = Get-EnvValue -File $envFile -Key "OLLAMA_HEAVY_MODEL"

Install-OllamaModel -Role "DEFAULT" -Name $envDefault -Size "~9 GB" -Required $true
if ($envDefault -ne $OllamaModel) { Install-OllamaModel -Role "DEFAULT (installer)" -Name $OllamaModel -Size "~9 GB" -Required $false }
Install-OllamaModel -Role "FAST" -Name $envFast -Size "~6 GB" -Required $false

# HEAVY: exact Q2_K GGUF from Hugging Face, registered under a local name via a
# Modelfile that applies Ollama's official qwen3.8 renderer/parser.
Write-Step "Model [HEAVY, experimental]: $OllamaHeavyModel"
if (Test-OllamaModel $OllamaHeavyModel) {
    Write-Info "  already installed - skipped."
} elseif ($SkipModelPull) {
    Write-Info "  MISSING (-SkipModelPull)."
} else {
    $ok = $true
    if (-not (Test-OllamaModel $HeavySource)) {
        Write-Info "  downloading GGUF $HeavySource (~10.1 GB)..."
        $rc = Invoke-Native -FilePath $ollamaExe -Arguments @("pull", $HeavySource)
        if ($rc -ne 0) { $ok = $false; Write-Info "  WARNING: download failed (exit $rc)." }
    } else { Write-Info "  GGUF source already downloaded." }
    if ($ok) {
        Write-Info "  creating $OllamaHeavyModel from $HeavyModelfile (no extra download)..."
        Push-Location $InstallRoot
        try { $rc = Invoke-Native -FilePath $ollamaExe -Arguments @("create", $OllamaHeavyModel, "-f", $HeavyModelfile) }
        finally { Pop-Location }
        if ($rc -eq 0) { Write-Info "  installed: $OllamaHeavyModel" }
        else { Write-Info "  WARNING: ollama create failed (exit $rc). The default model is unaffected." }
    }
}
if ($envHeavy -and $envHeavy -ne $OllamaHeavyModel -and -not (Test-OllamaModel $envHeavy)) {
    Write-Info "WARNING: OLLAMA_HEAVY_MODEL=$envHeavy (from .env) is not installed."
}
Invoke-RepoScript -Script "scripts\model-status.ps1" | Out-Null

# ---------------------------------------------------------------------
# 13. Local speech-to-text model (skips itself if model.bin exists)
# ---------------------------------------------------------------------
Write-Step "Ensuring local faster-whisper model (scripts\fetch-local-models.ps1)..."
$rc = Invoke-RepoScript -Script "scripts\fetch-local-models.ps1"
if ($rc -ne 0) { Write-Info "WARNING: speech model fetch returned exit code $rc. DAYANERA still runs; transcription will not." }

# ---------------------------------------------------------------------
# 14. Start DAYANERA (-Rebuild: dist\ may come from another machine)
# ---------------------------------------------------------------------
Write-Step "Starting DAYANERA.ai via scripts\start-local.ps1..."
$rc = Invoke-RepoScript -Script "scripts\start-local.ps1" -Arguments @("-Rebuild")
if ($rc -ne 0) { throw "start-local.ps1 failed with exit code $rc (logs: $InstallRoot\data\logs)" }

# ---------------------------------------------------------------------
# 15. Health checks
# ---------------------------------------------------------------------
Write-Step "Final health check..."
$checks = @(
    @{ Name = "Frontend";   Port = 5173;  Url = "http://127.0.0.1:5173/" },
    @{ Name = "Backend";    Port = 8000;  Url = "http://127.0.0.1:8000/api/v1/health" },
    @{ Name = "PostgreSQL"; Port = 54329; Url = $null },
    @{ Name = "Ollama";     Port = 11434; Url = "http://127.0.0.1:11434/api/version" }
)
$allGood = $true
foreach ($item in $checks) {
    $ok = $false
    if ($item.Url) {
        try { $ok = ((Invoke-WebRequest -Uri $item.Url -UseBasicParsing -TimeoutSec 5).StatusCode -lt 500) } catch {}
    } else {
        $client = New-Object System.Net.Sockets.TcpClient
        try { $ok = $client.ConnectAsync("127.0.0.1", $item.Port).Wait(3000) } catch {} finally { $client.Close() }
    }
    $addrs = @(Get-NetTCPConnection -LocalPort $item.Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty LocalAddress -Unique)
    $exposed = @($addrs | Where-Object { @("127.0.0.1", "::1") -notcontains $_ })
    if ($ok -and $exposed.Count -eq 0) {
        Write-Host ("[OK]   {0,-11} 127.0.0.1:{1}  (listening on: {2})" -f $item.Name, $item.Port, ($addrs -join ", ")) -ForegroundColor Green
    } elseif ($exposed.Count -gt 0) {
        Write-Host ("[FAIL] {0,-11} port {1} listens beyond loopback: {2}" -f $item.Name, $item.Port, ($addrs -join ", ")) -ForegroundColor Red
        $allGood = $false
    } else {
        Write-Host ("[WARN] {0,-11} 127.0.0.1:{1} not responding" -f $item.Name, $item.Port) -ForegroundColor Yellow
        $allGood = $false
    }
}

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "DAYANERA HOME SERVER INSTALLATION FINISHED" -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "Project : $InstallRoot"
Write-Host "UI      : http://127.0.0.1:5173"
Write-Host "API     : http://127.0.0.1:8000/api/v1"
Write-Host "Log     : $LogFile"
if (Test-Path -LiteralPath $CredFile) { Write-Host "Initial admin credentials: $CredFile" -ForegroundColor Yellow }
if ($allGood) { Write-Host "All DAYANERA local services are responding." -ForegroundColor Green }
else { Write-Host "One or more checks failed. See the log and $InstallRoot\data\logs." -ForegroundColor Yellow }
Write-Host "NOTE: LAN access is intentionally NOT opened; all services are localhost-only." -ForegroundColor Cyan
Write-Host ""
Stop-Installer $(if ($allGood) { 0 } else { 1 })
