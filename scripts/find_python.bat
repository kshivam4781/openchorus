@echo off
rem Called by the .bat launchers. Sets PY to a Python 3.10+ command, or prints help and sets PY to nothing.
set "PY="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=%%~C"
  )
)
if not defined PY (
  echo OpenChorus needs Python 3.10 or newer.
  echo Install it with: winget install Python.Python.3.12
  echo or from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
)
