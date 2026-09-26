<#
.SYNOPSIS
  DAYANERA.ai - Git preflight: verifies that no private runtime data or secrets
  are tracked or staged. Run before every commit (never use 'git add .' blindly).

.DESCRIPTION
  Fails (exit 1) when:
    * a tracked or staged path matches a forbidden pattern (.env, data/, agent-notes/,
      IMPLEMENTATION_REPORT.md, logs, database dumps, iso booklets/, node_modules, .venv ...)
    * a tracked/staged text file contains the real local secrets from .env
      (PostgreSQL password, API/Supabase keys) or common secret formats
    * required .gitignore rules are missing

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\git-check-private-data.ps1
#>
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$ErrorActionPreference = 'Continue'
$root = Get-RepoRoot
Set-Location $root
$fail = @()

git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) { Write-Fail 'Bu klasor bir Git deposu degil (git init).'; exit 1 }

Write-Step 'Zorunlu .gitignore kurallari'
$mustIgnore = @('.env', '.env.local', 'data/x.bin', 'data/documents/a.pdf', 'agent-notes/2026-01-01_x.md',
    'IMPLEMENTATION_REPORT.md', 'x.log', 'postgres-data/x', 'pgdata/x', 'backend/__pycache__/x.pyc',
    '.pytest_cache/x', 'backend/.venv/x', 'frontend/node_modules/x', 'frontend/dist/index.html', 'coverage/x',
    'iso booklets/x.pdf')
foreach ($p in $mustIgnore) {
    git check-ignore -q --no-index -- "$p"
    if ($LASTEXITCODE -eq 0) { Write-Ok "yok sayiliyor: $p" } else { Write-Fail "YOK SAYILMIYOR: $p"; $fail += "gitignore: $p" }
}
git check-ignore -q --no-index -- '.env.example'
if ($LASTEXITCODE -eq 0) { Write-Fail '.env.example yok sayilmamali'; $fail += '.env.example ignored' }

Write-Step 'Izlenen ve hazirlanan (staged) dosyalar'
$tracked = @(git ls-files) + @(git diff --cached --name-only --diff-filter=ACMR)
$tracked = $tracked | Where-Object { $_ } | Sort-Object -Unique
$forbidden = @('^\.env$', '^\.env\.(?!example$)', '(^|/)data/', '^agent-notes/', 'IMPLEMENTATION_REPORT\.md$', '\.log$',
    '(^|/)(postgres-data|pgdata)/', '\.(dump|backup)$', '^iso booklets/', '(^|/)node_modules/', '(^|/)\.venv/',
    '(^|/)__pycache__/', '(^|/)dist/', '\.(pem|key|pfx|p12)$')
foreach ($f in $tracked) {
    foreach ($rx in $forbidden) {
        if ($f -match $rx) { Write-Fail "ozel/uretilmis dosya izleniyor: $f"; $fail += "tracked: $f"; break }
    }
}
Write-Ok "$($tracked.Count) dosya kontrol edildi"

Write-Step 'Gizli bilgi taramasi'
$envVars = Read-DotEnv (Join-Path $root '.env')
$secretValues = @()
foreach ($k in @('POSTGRES_PASSWORD', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'SUPABASE_SECRET_KEY', 'SUPABASE_PUBLISHABLE_KEY')) {
    $v = $envVars[$k]
    if ($v -and $v.Length -ge 8 -and $v -ne 'change-local-password') { $secretValues += $v }
}
$patterns = @('sk-[A-Za-z0-9_\-]{20,}', 'sk-ant-[A-Za-z0-9_\-]{20,}', 'sb_secret_[A-Za-z0-9_\-]{10,}',
    'eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.', '-----BEGIN [A-Z ]*PRIVATE KEY-----', 'AKIA[0-9A-Z]{16}')
$textExt = '\.(py|ts|tsx|js|json|md|txt|ps1|sh|yml|yaml|toml|ini|sql|css|html|cfg|mako|example)$'
foreach ($f in $tracked) {
    if (-not ($f -match $textExt -or $f -match '(^|/)\.(gitignore|gitattributes)$')) { continue }
    $full = Join-Path $root $f
    if (-not (Test-Path -LiteralPath $full)) { continue }
    $content = [System.IO.File]::ReadAllText($full)
    foreach ($s in $secretValues) {
        if ($content.Contains($s)) { Write-Fail "gercek gizli deger bulundu: $f"; $fail += "secret: $f" }
    }
    foreach ($rx in $patterns) {
        if ($content -match $rx -and $f -notmatch 'git-check-private-data') { Write-Fail "gizli anahtar bicimi: $f ($rx)"; $fail += "pattern: $f" }
    }
}
Write-Ok 'Tarama tamamlandi'

if ($fail.Count -gt 0) {
    Write-Host ''
    Write-Fail "BASARISIZ: $($fail.Count) sorun. Commit etmeyin."
    exit 1
}
Write-Host ''
Write-Ok 'GECTI: ozel veri veya gizli bilgi izlenmiyor/hazirlanmamis.'
exit 0
