@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Please install 64-bit Python 3.12 from https://www.python.org/downloads/windows/
  echo Enable the Python launcher during installation, then run this file again.
  pause
  exit /b 1
)
for %%V in (3.12 3.11 3.10) do (
  py -%%V -c "import struct,sys; sys.exit(struct.calcsize('P') != 8)" >nul 2>nul
  if not errorlevel 1 (
    py -%%V tools\windows_launcher.py %*
    if errorlevel 1 (
      pause
      exit /b 1
    )
    exit /b 0
  )
)
echo No supported 64-bit Python found. Install Python 3.12 and try again.
pause
exit /b 1
