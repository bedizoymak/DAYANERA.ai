#!/usr/bin/env bash
# DAYANERA.ai - Git preflight for Git Bash users. Delegates to the PowerShell
# implementation so that both entry points apply identical rules.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v pwsh >/dev/null 2>&1; then
  exec pwsh -NoProfile -ExecutionPolicy Bypass -File "$here/git-check-private-data.ps1"
else
  exec powershell -NoProfile -ExecutionPolicy Bypass -File "$here/git-check-private-data.ps1"
fi
