#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT_DIR/.pyinstaller" "$ROOT_DIR/.pycache"

PYTHONPATH="$ROOT_DIR/vendor${PYTHONPATH:+:$PYTHONPATH}" \
PYINSTALLER_CONFIG_DIR="$ROOT_DIR/.pyinstaller" \
PYTHONPYCACHEPREFIX="$ROOT_DIR/.pycache" \
python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name ConvertLink \
  --paths "$ROOT_DIR/vendor" \
  --add-binary "$ROOT_DIR/tools/yt-dlp:tools" \
  --add-binary "$ROOT_DIR/tools/deno:tools" \
  --exclude-module tkinter \
  --hidden-import imageio_ffmpeg \
  "$ROOT_DIR/main.py"
