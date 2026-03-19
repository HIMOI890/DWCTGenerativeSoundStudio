param(
  [string]$OutDir = "./studio/edmg-studio/electron-resources/bin"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")
$OutDirAbs = Resolve-Path (Join-Path $RepoRoot $OutDir) -ErrorAction SilentlyContinue
if (-not $OutDirAbs) {
  $OutDirAbs = Join-Path $RepoRoot $OutDir
  New-Item -ItemType Directory -Force -Path $OutDirAbs | Out-Null
}

$zipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$tmpZip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"

Write-Host "Downloading FFmpeg essentials from gyan.dev..."
Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip

$tmpDir = Join-Path $env:TEMP "ffmpeg_essentials_extract"
if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

Write-Host "Extracting..."
Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

$ffmpegExe = Get-ChildItem -Path $tmpDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
if (-not $ffmpegExe) { throw "ffmpeg.exe not found after extraction" }

Copy-Item -Force $ffmpegExe.FullName (Join-Path $OutDirAbs "ffmpeg.exe")
Write-Host "OK: staged ffmpeg.exe into $OutDirAbs" -ForegroundColor Green
