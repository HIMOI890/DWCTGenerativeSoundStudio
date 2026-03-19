@echo off
setlocal
cd /d %~dp0
REM Prefer py launcher on Windows if present
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 tools\launcher_gui.py
) else (
  python tools\launcher_gui.py
)
endlocal
