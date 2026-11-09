@echo off
rem Clone ACE-Step 1.5 next to OpenChorus and start its REST API on http://127.0.0.1:8001
cd /d "%~dp0\.."
where uv >nul 2>nul || powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if not exist ACE-Step-1.5 git clone https://github.com/ace-step/ACE-Step-1.5
cd ACE-Step-1.5
uv sync
echo Starting ACE-Step API. The first run downloads the models (several GB).
uv run acestep-api
