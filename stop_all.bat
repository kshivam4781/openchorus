@echo off
rem Stop OpenChorus and ACE-Step and the AI service (whatever listens on ports 7777, 8001 and 8002).
for %%P in (7777 8001 8002) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /r /c:":%%P .*LISTENING"') do taskkill /PID %%I /T /F >nul 2>&1
)
echo Stopped.
