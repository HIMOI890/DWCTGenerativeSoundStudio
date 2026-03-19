param(
  [ValidateSet("minimal","standard","full","dev")]
  [string]$Mode = "full",
  [ValidateSet("cpu","cu118","cu121","cu124")]
  [string]$Backend = "cpu",
  [switch]$Cuda,
  [string]$Venv = "venv",
  [string]$CacheRoot = "",
  [switch]$SkipCorpora,
  [switch]$SkipModels
)

$ErrorActionPreference = "Stop"

Write-Host "== EDMG installer ==" -ForegroundColor Cyan
if ($Cuda -and $Backend -eq "cpu") {
  $Backend = "cu121"
}

Write-Host "Mode: $Mode  Backend: $Backend  Venv: $Venv" -ForegroundColor Cyan
if ($CacheRoot) {
  Write-Host "CacheRoot: $CacheRoot" -ForegroundColor Cyan
}

$argsList = @(
  "scripts\edmg_installer.py",
  "install",
  "--mode", $Mode,
  "--backend", $Backend,
  "--venv", $Venv
)

if ($CacheRoot) {
  $argsList += @("--cache-root", $CacheRoot)
}
if ($SkipCorpora) {
  $argsList += "--skip-corpora"
}
if ($SkipModels) {
  $argsList += "--skip-models"
}

python @argsList

Write-Host "`nDone. To run:" -ForegroundColor Green
if ([System.IO.Path]::IsPathRooted($Venv)) {
  Write-Host "  $Venv\Scripts\activate" -ForegroundColor Green
} else {
  Write-Host "  .\$Venv\Scripts\activate" -ForegroundColor Green
}
Write-Host "  python -m enhanced_deforum_music_generator ui --port 7860" -ForegroundColor Green
