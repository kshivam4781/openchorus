@echo off
rem Start OpenChorus on http://127.0.0.1:7777
cd /d "%~dp0"
where ffmpeg >nul 2>nul || echo Note: ffmpeg not found. Install it for MP3 output and uploads: winget install Gyan.FFmpeg
if not exist .venv (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
start "" http://127.0.0.1:7777
python -m server.main
