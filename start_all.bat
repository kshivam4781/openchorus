@echo off
rem Start ACE-Step 1.5 (AI engine, if installed next to this folder), the AI service and OpenChorus,
rem each in its own minimized window, then open the app in your browser.
cd /d "%~dp0"
set "ACE=%~dp0..\ACE-Step-1.5"
if exist "%ACE%\pyproject.toml" (
  echo Starting ACE-Step 1.5 on http://127.0.0.1:8001 ...
  rem Load the models at startup, and decode audio in large chunks kept on the GPU
  rem (measured on a 12 GB RTX 5070 Ti: decode 42 s -> 7 s for two 2-minute takes).
  start "ACE-Step API" /min /D "%ACE%" cmd /c "set ACESTEP_NO_INIT=false&& set ACESTEP_VAE_DECODE_CHUNK_SIZE=512&& set ACESTEP_MPS_DECODE_OFFLOAD=false&& set PYTHONUNBUFFERED=1&& uv run --no-sync acestep-api --host 127.0.0.1 --port 8001 > acestep-api.log 2>&1"
) else (
  echo ACE-Step not found at %ACE%. Run setup_acestep.bat for AI songs with vocals.
)
if exist "%~dp0ai_service\.venv\Scripts\python.exe" (
  echo Starting the AI service on http://127.0.0.1:8002 ^(quality judge, cover art, stems^) ...
  start "OpenChorus AI service" /min /D "%~dp0ai_service" cmd /c "set HF_HUB_DISABLE_SYMLINKS_WARNING=1&& set PYTHONUNBUFFERED=1&& .venv\Scripts\python.exe server.py > ai_service.log 2>&1"
) else (
  echo AI service not installed. Run setup_ai_service.bat for cover art, Max Mode and 4-stem splits.
)
if not exist .venv (
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)
echo Starting OpenChorus on http://127.0.0.1:7777 ...
start "OpenChorus" /min /D "%~dp0" cmd /c ".venv\Scripts\python.exe -m server.main > openchorus.log 2>&1"
timeout /t 5 /nobreak >nul
start "" http://127.0.0.1:7777
