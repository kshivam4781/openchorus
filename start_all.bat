@echo off
rem Start ACE-Step 1.5 (AI engine, if installed next to this folder), the AI service and OpenChorus,
rem each in its own minimized window, then open the app in your browser. Stop with stop_all.bat
setlocal
cd /d "%~dp0"
set "ACE=%~dp0..\ACE-Step-1.5"
if not defined OPENCHORUS_PORT set OPENCHORUS_PORT=7777
rem uv installs itself into %USERPROFILE%\.local\bin, which a fresh window may not have on PATH yet.
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where ffmpeg >nul 2>nul || echo Note: ffmpeg not found. Install it for MP3 output, mastering and uploads: winget install Gyan.FFmpeg
if not exist logs mkdir logs

if exist "%ACE%\pyproject.toml" (
  echo Starting ACE-Step 1.5 on http://127.0.0.1:8001 ...
  rem Decode audio in large chunks kept on the GPU when the card has 11 GB or more
  rem (measured on a 12 GB RTX 5070 Ti: decode 42 s to 7 s for two 2-minute takes).
  rem Smaller cards keep ACE-Step's own memory-safe defaults.
  set "ACE_FAST="
  for /f "usebackq delims=" %%V in (`nvidia-smi --query-gpu^=memory.total --format^=csv^,noheader^,nounits 2^>nul`) do (
    if not defined ACE_FAST if %%V GEQ 11000 set "ACE_FAST=1"
  )
  rem UTF-8 output: ACE-Step prints emoji, which crash Python on Windows when output goes to a log file.
  call :start_ace
) else (
  echo ACE-Step not found at %ACE%. Run setup_acestep.bat for AI songs with vocals.
)

if exist "%~dp0ai_service\.venv\Scripts\python.exe" (
  echo Starting the AI service on http://127.0.0.1:8002 ^(quality judge, cover art, stems^) ...
  start "OpenChorus AI service" /min /D "%~dp0ai_service" cmd /c "set HF_HUB_DISABLE_SYMLINKS_WARNING=1&& set PYTHONUNBUFFERED=1&& set PYTHONIOENCODING=utf-8&& set PYTHONUTF8=1&& .venv\Scripts\python.exe server.py > "%~dp0logs\ai_service.log" 2>&1"
) else (
  echo AI service not installed. Run setup_ai_service.bat for cover art, Max Mode and 4-stem splits.
)

if not exist .venv\Scripts\python.exe (
  call scripts\find_python.bat
  if not defined PY ( pause & exit /b 1 )
  call %%PY%% -m venv .venv || ( echo Could not create the virtual environment. & pause & exit /b 1 )
  .venv\Scripts\python.exe -m pip install -q --upgrade pip
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || ( echo Installing the requirements failed. & pause & exit /b 1 )
echo Starting OpenChorus on http://127.0.0.1:%OPENCHORUS_PORT% ...
start "OpenChorus" /min /D "%~dp0" cmd /c "set PYTHONIOENCODING=utf-8&& .venv\Scripts\python.exe -m server.main > "%~dp0logs\openchorus.log" 2>&1"
rem Wait until the app answers (up to 30 s). ping works where "timeout" fails (no console, CI).
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%OPENCHORUS_PORT%/api/status | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul && goto :ready
  ping -n 2 127.0.0.1 >nul
)
:ready
if not defined OPENCHORUS_NO_BROWSER start "" http://127.0.0.1:%OPENCHORUS_PORT%
echo Running at http://127.0.0.1:%OPENCHORUS_PORT%. Logs are in the logs folder. Stop with stop_all.bat
exit /b 0

:start_ace
if defined ACE_FAST (
  start "ACE-Step API" /min /D "%ACE%" cmd /c "set ACESTEP_NO_INIT=false&& set ACESTEP_VAE_DECODE_CHUNK_SIZE=512&& set ACESTEP_MPS_DECODE_OFFLOAD=false&& set PYTHONUNBUFFERED=1&& set PYTHONIOENCODING=utf-8&& set PYTHONUTF8=1&& uv run --no-sync acestep-api --host 127.0.0.1 --port 8001 > "%~dp0logs\acestep.log" 2>&1"
) else (
  start "ACE-Step API" /min /D "%ACE%" cmd /c "set ACESTEP_NO_INIT=false&& set PYTHONUNBUFFERED=1&& set PYTHONIOENCODING=utf-8&& set PYTHONUTF8=1&& uv run --no-sync acestep-api --host 127.0.0.1 --port 8001 > "%~dp0logs\acestep.log" 2>&1"
)
exit /b 0
