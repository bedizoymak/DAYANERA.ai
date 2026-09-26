<#
.SYNOPSIS
  DAYANERA.ai - idempotent local environment preparation (no secrets downloaded).

.DESCRIPTION
  1. Checks prerequisites: Git, Docker Desktop (daemon), Python 3.12+, Node LTS/npm, Ollama.
  2. Creates .env from .env.example if missing (random local PostgreSQL password,
     paths adapted to this checkout). An existing .env is never overwritten.
  3. Creates backend\.venv and installs locked Python dependencies.
  4. Installs frontend dependencies (npm ci).
  5. Creates runtime folders below the project root (data\*, agent-notes).
  6. Validates configuration boundaries (loopback-only, paths under PROJECT_ROOT).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipPython,
    [switch]$SkipFrontend
)
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
Set-Location $root
$problems = @()

Write-Step 'On kosullar kontrol ediliyor'
foreach ($tool in @('git', 'docker', 'node', 'npm')) {
    $c = Get-Command $tool -ErrorAction SilentlyContinue
    if ($c) { Write-Ok "$tool bulundu" } else { Write-Fail "$tool bulunamadi"; $problems += "$tool kurulu degil" }
}
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'Docker Desktop calisiyor' }
    else { Write-Warn2 'Docker daemon calismiyor: Docker Desktop uygulamasini baslatin.'; $problems += 'Docker Desktop calismiyor' }
}
if (Get-Command node -ErrorAction SilentlyContinue) {
    $nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
    if ($nodeMajor -lt 20) { Write-Warn2 "Node $nodeMajor bulundu; Node 20+ LTS onerilir."; $problems += 'Node 20+ gerekli' }
}
$pyCmd = Find-Python312
if ($pyCmd) { Write-Ok ("Python 3.12+ bulundu: " + ($pyCmd -join ' ')) } else { Write-Fail 'Python 3.12+ bulunamadi'; $problems += 'Python 3.12+ kurulu degil' }
$ollama = Find-Ollama
if ($ollama) { Write-Ok "Ollama bulundu: $ollama" } else { Write-Warn2 'Ollama bulunamadi: https://ollama.com (winget install Ollama.Ollama)'; $problems += 'Ollama kurulu degil' }

Write-Step '.env yapilandirmasi'
$envPath = Join-Path $root '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Ok '.env zaten var (degistirilmedi)'
} else {
    $example = [System.IO.File]::ReadAllText((Join-Path $root '.env.example'))
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $content = $example.Replace('change-local-password', $pw)
    $winRoot = $root.Replace('\', '\\')
    $content = $content.Replace('C:\\Users\\Bediz\\Documents\\DAYANERA.ai', $winRoot)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envPath, $content, $utf8)
    Write-Ok '.env olusturuldu (rastgele yerel PostgreSQL parolasi; Git tarafindan yok sayilir)'
}
$cfg = Read-DotEnv $envPath
Assert-Loopback $cfg
Write-Ok 'Tum baglama adresleri loopback (127.0.0.1/localhost)'

Write-Step 'Calisma klasorleri'
foreach ($d in @('data\documents', 'data\document-versions', 'data\media', 'data\indexes', 'data\exports', 'data\logs', 'data\backups', 'data\tmp', 'data\models', 'data\run', 'agent-notes')) {
    $p = Join-Path $root $d
    if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p | Out-Null; Write-Ok "olusturuldu: $d" }
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'iso booklets'))) {
    Write-Warn2 "'iso booklets' klasoru yok. Olusturup ISO PDF dosyalarinizi ekleyin (dogrulanmis korpus)."
}

if (-not $SkipPython -and $pyCmd) {
    Write-Step 'Python sanal ortami ve bagimliliklar'
    $venvPy = Join-Path $root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPy)) {
        $pyExe = $pyCmd[0]; $pyArgs = @($pyCmd | Select-Object -Skip 1) + @('-m', 'venv', (Join-Path $root 'backend\.venv'))
        & $pyExe @pyArgs
        if ($LASTEXITCODE -ne 0) { throw 'venv olusturulamadi' }
        Write-Ok 'backend\.venv olusturuldu'
    }
    & $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $root 'backend\requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install basarisiz' }
    Write-Ok 'Python bagimliliklari kurulu (requirements.lock.txt)'
    Push-Location (Join-Path $root 'backend')
    try {
        & $venvPy -m app.cli check-config
        if ($LASTEXITCODE -ne 0) { throw 'Yapilandirma dogrulamasi basarisiz' }
    } finally { Pop-Location }
}

if (-not $SkipFrontend -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Step 'Frontend bagimliliklari'
    Push-Location (Join-Path $root 'frontend')
    try {
        if (Test-Path -LiteralPath 'package-lock.json') { npm ci --no-audit --no-fund } else { npm install --no-audit --no-fund }
        if ($LASTEXITCODE -ne 0) { throw 'npm kurulumu basarisiz' }
        Write-Ok 'npm bagimliliklari kurulu'
    } finally { Pop-Location }
}

Write-Step 'Sonraki adimlar'
if ($ollama) {
    $model = $cfg['OLLAMA_MODEL']
    $found = & $ollama list 2>$null | Select-String -SimpleMatch $model
    if ($found) { Write-Ok "Model mevcut: $model" } else { Write-Host "    ollama pull $model" }
}
Write-Host '    (istege bagli, ses dokumu icin) powershell -ExecutionPolicy Bypass -File scripts\fetch-local-models.ps1'
Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1'
if ($problems.Count -gt 0) {
    Write-Warn2 ('Eksikler: ' + ($problems -join '; '))
    exit 1
}
Write-Ok 'Bootstrap tamamlandi.'
