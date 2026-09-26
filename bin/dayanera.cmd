@echo off
rem DAYANERA.ai terminal command. Put this folder on PATH with scripts\install-cli.ps1.
setlocal
set "PYTHONPATH=%~dp0..\backend"
"%~dp0..\backend\.venv\Scripts\python.exe" -m app.terminal %*
