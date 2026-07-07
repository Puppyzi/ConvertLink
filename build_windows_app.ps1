$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -ItemType Directory -Force "$RootDir\.pyinstaller" | Out-Null
New-Item -ItemType Directory -Force "$RootDir\.pycache" | Out-Null

$env:PYTHONPATH = "$RootDir\vendor"
$env:PYINSTALLER_CONFIG_DIR = "$RootDir\.pyinstaller"
$env:PYTHONPYCACHEPREFIX = "$RootDir\.pycache"

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name ConvertLink `
  --paths "$RootDir\vendor" `
  --add-binary "$RootDir\tools\yt-dlp.exe;tools" `
  --add-binary "$RootDir\tools\deno.exe;tools" `
  --exclude-module tkinter `
  --hidden-import imageio_ffmpeg `
  "$RootDir\main.py"
