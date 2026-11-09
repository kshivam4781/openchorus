@echo off
rem Clone ACE-Step 1.5 next to OpenChorus and install it. Start everything with start_all.bat
cd /d "%~dp0\.."
where git >nul 2>nul || ( echo git is required: winget install Git.Git & pause & exit /b 1 )
where uv >nul 2>nul || powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if not exist ACE-Step-1.5 git clone https://github.com/ace-step/ACE-Step-1.5 || ( pause & exit /b 1 )
cd ACE-Step-1.5
uv sync || ( echo uv sync failed. & pause & exit /b 1 )
echo.
echo ACE-Step is installed. Start everything with start_all.bat in the openchorus folder.
echo The first start downloads the models (about 10 GB).
