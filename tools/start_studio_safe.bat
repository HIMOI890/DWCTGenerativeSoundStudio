@echo off
setlocal
cd /d "%~dp0\..\studio\edmg-studio" || exit /b 1
call npm run dev
exit /b %ERRORLEVEL%
