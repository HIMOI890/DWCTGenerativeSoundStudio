@echo off
setlocal
cd /d %~dp0

REM Launch the unified EDMG Studio product entrypoint.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\launcher_gui.py
  goto :eof
)

REM Fallback to python
where python >nul 2>nul
if %errorlevel%==0 (
  python tools\launcher_gui.py
  goto :eof
)

echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/
pause
