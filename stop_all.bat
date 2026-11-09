@echo off
rem Stop OpenChorus, ACE-Step and the AI service (whatever listens on their ports).
setlocal
set "PORTS=7777 8001 8002"
if defined OPENCHORUS_PORT set "PORTS=%OPENCHORUS_PORT% 8001 8002"
for %%P in (%PORTS%) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do taskkill /PID %%I /T /F >nul 2>&1
)
echo Stopped.
