@echo off
setlocal

echo 🎵 Enhanced Deforum Music Generator 🎥
echo ==================================

REM Activate venv if present
if exist "venv\Scripts\activate.bat" (
    echo 📦 Activating virtual environment...
    call "venv\Scripts\activate.bat"
)

REM Load .env key=value lines (best-effort)
if exist ".env" (
    for /f "usebackq delims=" %%A in (".env") do (
        set "%%A"
    )
)

REM Ensure folders
if not exist "data" mkdir data
if not exist "data\models" mkdir data\models
if not exist "data\cache" mkdir data\cache
if not exist "data\logs" mkdir data\logs
if not exist "output" mkdir output
if not exist "output\packages" mkdir output\packages
if not exist "output\analysis" mkdir output\analysis
if not exist "output\previews" mkdir output\previews

echo 🚀 Starting Gradio UI...
python -m enhanced_deforum_music_generator ui --port 7860

echo 👋 Application stopped.
pause
