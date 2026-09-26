<#
.SYNOPSIS
  Adds the DAYANERA.ai bin folder to the current user's PATH so `dayanera`
  works from any new terminal. Idempotent; needs no admin rights.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-cli.ps1
#>
$bin = (Resolve-Path (Join-Path $PSScriptRoot '..\bin')).Path
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($userPath -split ';' | Where-Object { $_ })
if ($parts -contains $bin) {
    Write-Host "Zaten PATH'te: $bin"
} else {
    [Environment]::SetEnvironmentVariable('Path', (($parts + $bin) -join ';'), 'User')
    Write-Host "PATH'e eklendi: $bin"
}
Write-Host "Yeni bir terminal acip 'dayanera' yazin."
