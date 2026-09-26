<#
.SYNOPSIS
  DAYANERA.ai - one-time download of the local speech-to-text model
  (faster-whisper, CTranslate2 format) into DATA_ROOT\models.

.DESCRIPTION
  This is an explicit setup step (like 'ollama pull'). At runtime the model is
  loaded strictly offline from the local folder; no audio ever leaves the machine.
  Default size: WHISPER_MODEL_NAME from .env (small, ~480 MB).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\fetch-local-models.ps1
#>
[CmdletBinding()]
param([string]$Size = '')
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-RepoRoot
$cfg = Get-EnvConfig
$py = Get-VenvPython
if (-not $Size) { $Size = $cfg['WHISPER_MODEL_NAME']; if (-not $Size) { $Size = 'small' } }
$target = Join-Path $root "data\models\faster-whisper-$Size"
if (Test-Path -LiteralPath (Join-Path $target 'model.bin')) { Write-Ok "Model zaten mevcut: $target"; exit 0 }
Write-Step "faster-whisper-$Size indiriliyor -> $target"
$code = @"
import sys
from huggingface_hub import snapshot_download
p = snapshot_download(repo_id='Systran/faster-whisper-$Size', local_dir=sys.argv[1],
                      allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*', 'preprocessor_config.json'])
print(p)
"@
& $py -c $code $target
if ($LASTEXITCODE -ne 0) { Write-Fail 'Model indirilemedi.'; exit 1 }
Write-Ok "Model hazir: $target (calisma aninda cevrimdisi yuklenir)"
