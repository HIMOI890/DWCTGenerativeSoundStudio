param(
  [string]$PythonExe = "python",
  [string]$NodeExe = "node",
  [string]$NpmExe = "npm"
)

$ErrorActionPreference = "Stop"

function Assert-Command($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Missing required command: $name"
  }
}

function Invoke-Checked($label, [scriptblock]$action) {
  & $action
  if ($LASTEXITCODE -ne 0) {
    throw ($label + " failed with exit code " + $LASTEXITCODE)
  }
}

function Resolve-BackendPackageDir($PyBackendDir) {
  $candidates = @(
    (Join-Path $PyBackendDir "edmg_studio_backend"),
    (Join-Path $PyBackendDir "src\edmg_studio_backend")
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  $checked = $candidates -join ", "
  throw "Backend package folder not found. Checked: $checked"
}

function Get-BundledFfmpegPath($StudioDir) {
  return Join-Path $StudioDir "electron-resources\bin\ffmpeg.exe"
}

function Ensure-BundledFfmpeg($RepoRoot, $StudioDir) {
  $bundled = Get-BundledFfmpegPath $StudioDir
  if (Test-Path $bundled) {
    Write-Host ("[info] Bundled FFmpeg ready: " + $bundled) -ForegroundColor Cyan
    return $bundled
  }

  $script = Join-Path $RepoRoot "packaging\windows\get_ffmpeg.ps1"
  if (-not (Test-Path $script)) {
    throw "Missing FFmpeg staging script: $script"
  }

  Write-Host "[info] Bundled FFmpeg missing; downloading/staging it now..." -ForegroundColor Yellow
  Invoke-Checked "stage bundled FFmpeg" {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $script -OutDir "./studio/edmg-studio/electron-resources/bin"
  }

  if (-not (Test-Path $bundled)) {
    throw "Bundled FFmpeg staging failed: $bundled"
  }

  Write-Host ("[info] Bundled FFmpeg staged: " + $bundled) -ForegroundColor Green
  return $bundled
}

function Check-Port($port, $label) {
  Write-Host ("Port " + $port + " (" + $label + "):") -NoNewline
  $found = $false

  if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    try {
      $conns = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
      if ($conns) {
        Write-Host " LISTENING" -ForegroundColor Yellow
        foreach ($c in $conns) {
          $pid = $c.OwningProcess
          $pname = ""
          try { $pname = (Get-Process -Id $pid -ErrorAction SilentlyContinue).ProcessName } catch {}
          Write-Host ("  " + $c.LocalAddress + ":" + $c.LocalPort + "  pid=" + $pid + "  " + $pname)
        }
        $found = $true
      }
    } catch {}
  }

  if (-not $found) {
    try {
      $lines = & netstat -ano | Select-String (":$port\s")
      if ($lines) {
        Write-Host " IN USE" -ForegroundColor Yellow
        foreach ($l in $lines) {
          $parts = ($l.ToString() -split "\s+") | Where-Object { $_ -ne "" }
          $pid = $parts[-1]
          $pname = ""
          try { $pname = (Get-Process -Id $pid -ErrorAction SilentlyContinue).ProcessName } catch {}
          Write-Host ("  " + $l.ToString().Trim() + "  proc=" + $pname)
        }
        $found = $true
      }
    } catch {}
  }

  if (-not $found) {
    Write-Host " free" -ForegroundColor Green
  }
}

function Doctor($RepoRoot, $StudioDir, $PyBackendDir, $BackendPkgDir, $BundledFfmpegPath) {
  Write-Host "== Preflight Doctor ==" -ForegroundColor Cyan
  $repoPath = $RepoRoot.Path
  Write-Host ("RepoRoot: " + $repoPath)
  Write-Host ("Path length: " + $repoPath.Length)
  if ($repoPath.Length -gt 160) {
    Write-Host "[warn] Repo path is long. Consider moving to C:\EDMG\ to avoid Windows path issues." -ForegroundColor Yellow
  }

  try {
    $pyv = & $PythonExe -c "import sys; print(sys.version)"
    Write-Host ("Python: " + $pyv.Trim())
  } catch {
    Write-Host "[fail] Python not runnable." -ForegroundColor Red
  }

  try {
    $pipv = & $PythonExe -m pip --version
    Write-Host ("pip: " + $pipv.Trim())
  } catch {}

  try {
    $nv = & $NodeExe --version
    Write-Host ("Node: " + $nv.Trim())
  } catch {
    Write-Host "[warn] node not runnable (UI build will fail)." -ForegroundColor Yellow
  }

  try {
    $npmv = & $NpmExe --version
    Write-Host ("npm: " + $npmv.Trim())
  } catch {}

  $ff = $env:EDMG_FFMPEG_PATH
  if (-not $ff -and (Test-Path $BundledFfmpegPath)) {
    $ff = $BundledFfmpegPath
  }
  if (-not $ff) { $ff = "ffmpeg" }
  try {
    $ffv = & $ff -version
    Write-Host ("FFmpeg: " + ($ffv | Select-Object -First 1))
  } catch {
    Write-Host "[warn] FFmpeg not found. Internal rendering will rely on PATH or a bundled binary." -ForegroundColor Yellow
  }

  try {
    $driveLetter = $repoPath.Substring(0,1)
    $drive = Get-PSDrive -Name $driveLetter
    $gb = [math]::Round($drive.Free / 1GB, 2)
    Write-Host ("Disk free on " + $driveLetter + ": " + $gb + " GB")
    if ($gb -lt 20) {
      Write-Host "[warn] Low disk space. Video renders + node_modules can be large." -ForegroundColor Yellow
    }
  } catch {}

  Write-Host "== Port checks ==" -ForegroundColor Cyan
  Check-Port 7863 "Studio backend"
  Check-Port 8188 "ComfyUI"
  Check-Port 11434 "Ollama"
  Write-Host "================" -ForegroundColor Cyan
  Write-Host ("Backend package: " + $BackendPkgDir) -ForegroundColor Cyan
}

function Move-ExistingFolder($SourceDir, $DestRoot, $Label) {
  if (-not (Test-Path $SourceDir)) {
    return
  }

  New-Item -ItemType Directory -Force -Path $DestRoot | Out-Null
  $ts = Get-Date -Format "yyyyMMdd_HHmmss"
  $backup = Join-Path $DestRoot ($Label + "_" + $ts)
  Move-Item -Force $SourceDir $backup
  Write-Host ("[info] Moved " + $SourceDir + " -> " + $backup) -ForegroundColor Yellow
}

function Migrate-LegacyData($RepoRoot, $StudioDir, $PyBackendDir) {
  $DestData = Join-Path $StudioDir "data"
  $MigrationsDir = Join-Path $StudioDir "_legacy_migrations"
  New-Item -ItemType Directory -Force -Path $DestData | Out-Null
  New-Item -ItemType Directory -Force -Path $MigrationsDir | Out-Null

  $LegacyBackendData = Join-Path $PyBackendDir "data"
  if (Test-Path $LegacyBackendData) {
    Write-Host "[info] Found legacy python_backend/data. Migrating into studio/data." -ForegroundColor Yellow
    Copy-Item -Recurse -Force (Join-Path $LegacyBackendData "*") $DestData -ErrorAction SilentlyContinue
    Move-ExistingFolder $LegacyBackendData $MigrationsDir "python_backend_data"
  }

  $LegacyRootData = Join-Path $RepoRoot "data"
  if (Test-Path $LegacyRootData) {
    Write-Host "[info] Found legacy repo-root data/. Migrating into studio/data." -ForegroundColor Yellow
    Copy-Item -Recurse -Force (Join-Path $LegacyRootData "*") $DestData -ErrorAction SilentlyContinue
    Move-ExistingFolder $LegacyRootData $MigrationsDir "repo_root_data"
    try {
      cmd /c "mklink /J `"$LegacyRootData`" `"$DestData`"" | Out-Null
      Write-Host "[info] Recreated repo-root data/ as a junction to studio/data." -ForegroundColor Yellow
    } catch {
      Write-Host ("[warn] Could not recreate repo-root data junction: " + $_.Exception.Message) -ForegroundColor Yellow
    }
  }
}

Assert-Command $PythonExe
Assert-Command $NpmExe

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")
$StudioDir = Join-Path $RepoRoot "studio/edmg-studio"
$PyBackendDir = Join-Path $StudioDir "python_backend"

if (-not (Test-Path $StudioDir)) {
  throw "Studio directory not found: $StudioDir"
}
if (-not (Test-Path $PyBackendDir)) {
  throw "Python backend directory not found: $PyBackendDir"
}

$BackendPkgDir = Resolve-BackendPackageDir $PyBackendDir
$BundledFfmpegPath = Get-BundledFfmpegPath $StudioDir
Doctor $RepoRoot $StudioDir $PyBackendDir $BackendPkgDir $BundledFfmpegPath
Migrate-LegacyData $RepoRoot $StudioDir $PyBackendDir
$BundledFfmpegPath = Ensure-BundledFfmpeg $RepoRoot $StudioDir

Write-Host "[1/4] Building Python backend (PyInstaller)..."
Push-Location $PyBackendDir

if (-not (Test-Path "venv")) {
  & $PythonExe -m venv venv
}

$VenvPython = Join-Path $PyBackendDir "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
  throw "Virtual environment python not found: $VenvPython"
}

Invoke-Checked "upgrade backend packaging tools" {
  & $VenvPython -m pip install -U pip wheel setuptools
}
Invoke-Checked "install backend bundle" {
  & $VenvPython -m pip install -e ".[studio_bundle]"
}
Invoke-Checked "install pyinstaller" {
  & $VenvPython -m pip install pyinstaller
}
Invoke-Checked "build backend via pyinstaller" {
  & $VenvPython -m PyInstaller .\pyinstaller.spec --clean --noconfirm
}

$BackendExe = Join-Path $PyBackendDir "dist\edmg-studio-backend\edmg-studio-backend.exe"
if (-not (Test-Path $BackendExe)) {
  $BackendExe = Join-Path $PyBackendDir "dist\edmg-studio-backend.exe"
}
if (-not (Test-Path $BackendExe)) {
  throw "Backend build failed: cannot find edmg-studio-backend.exe"
}

Pop-Location

Write-Host "[2/4] Staging backend into Electron resources..."
$BackendDstDir = Join-Path $StudioDir "electron-resources\backend"
New-Item -ItemType Directory -Force -Path $BackendDstDir | Out-Null
Copy-Item -Force $BackendExe (Join-Path $BackendDstDir "edmg-studio-backend.exe")

Write-Host "[3/4] Installing UI dependencies..."
Push-Location $StudioDir
if (Test-Path "package-lock.json") {
  try {
    Invoke-Checked "npm ci" {
      & $NpmExe ci
    }
  } catch {
    Write-Host ("[warn] npm ci failed; retrying with npm install. " + $_.Exception.Message) -ForegroundColor Yellow
    Invoke-Checked "npm install fallback" {
      & $NpmExe install
    }
  }
} else {
  Invoke-Checked "npm install" {
    & $NpmExe install
  }
}

Write-Host "[4/4] Building installer (electron-builder)..."
Invoke-Checked "npm run dist:win" {
  & $NpmExe run dist:win
}
Pop-Location

Write-Host "Done. See: studio/edmg-studio/release/" -ForegroundColor Green
