# patch_and_setup.ps1
# Full Windows + Conda + Node.js setup for enhanced-deforum-music-generator

param(
    [string]$EnvName = "edm"
)

Write-Host ">>> [1/7] Creating Conda environment: $EnvName ..." -ForegroundColor Cyan

# Prefer CUDA env if available
if (Test-Path "environment-cuda.yml") {
    conda env create -f environment-cuda.yml -n $EnvName
} elseif (Test-Path "environment.yml") {
    conda env create -f environment.yml -n $EnvName
} else {
    Write-Error "No environment.yml found!"
    exit 1
}

Write-Host ">>> [2/7] Activating environment ..." -ForegroundColor Cyan
conda activate $EnvName

Write-Host ">>> [3/7] Installing pip-only dependencies ..." -ForegroundColor Cyan
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt
}
if (Test-Path "requirements-dev.txt") {
    pip install -r requirements-dev.txt
}

Write-Host ">>> [4/7] Patching missing __init__.py files ..." -ForegroundColor Cyan
$patcher = @"
import os
root = os.getcwd()
skip = {"tests", "test", "scripts", "docs", "deployment", "data"}
for d, _, files in os.walk(root):
    if "__init__.py" not in files and any(f.endswith(".py") for f in files):
        base = os.path.basename(d).lower()
        if base not in skip:
            init_path = os.path.join(d, "__init__.py")
            with open(init_path, "w") as f:
                f.write("# auto-added by patch_and_setup.ps1\n")
            print(f"Added {init_path}")
"@
python -c $patcher

Write-Host ">>> [5/7] Setting up PYTHONPATH wrapper ..." -ForegroundColor Cyan
$wrapper = @"
`$env:PYTHONPATH = "`$PWD;`$env:PYTHONPATH"
if (`$args.Count -eq 0) {
    python --version
} else {
    & python @args
}
"@
Set-Content -Path "run_with_env.ps1" -Value $wrapper -Encoding UTF8

Write-Host ">>> [6/7] Installing Node.js dependencies ..." -ForegroundColor Cyan
if (Test-Path "package.json") {
    npm install
}

Write-Host ">>> [7/7] Building frontend ..." -ForegroundColor Cyan
if (Test-Path "package.json") {
    npm run build
}

Write-Host "`n>>> Setup complete! <<<" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "1. conda activate $EnvName"
Write-Host "2. ./run_with_env.ps1 python a1111_deforum_music.py   # (or another backend script)"
Write-Host "3. npm run dev    # start frontend UI (in another PowerShell window)"
