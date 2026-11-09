@echo off
rem Start OpenChorus on http://127.0.0.1:7777 (close this window or press Ctrl+C to stop).
setlocal
cd /d "%~dp0"
where ffmpeg >nul 2>nul || echo Note: ffmpeg not found. Install it for MP3 output, mastering and uploads: winget install Gyan.FFmpeg
if not exist .venv\Scripts\python.exe (
  call scripts\find_python.bat
  if not defined PY ( pause & exit /b 1 )
  call %%PY%% -m venv .venv || ( echo Could not create the virtual environment. & pause & exit /b 1 )
  .venv\Scripts\python.exe -m pip install -q --upgrade pip
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || ( echo Installing the requirements failed. & pause & exit /b 1 )
if not defined OPENCHORUS_PORT set OPENCHORUS_PORT=7777
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start "" http://127.0.0.1:%OPENCHORUS_PORT%"
.venv\Scripts\python.exe -m server.main
