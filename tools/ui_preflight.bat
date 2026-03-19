@echo off
setlocal
cd /d "%~dp0\..\studio\edmg-studio" || exit /b 1
echo === UI PREFLIGHT: npm run typecheck ===
call npm run typecheck
exit /b %ERRORLEVEL%
