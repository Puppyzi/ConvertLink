#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_DIR="$ROOT_DIR/tools"
mkdir -p "$TOOLS_DIR" "$TOOLS_DIR/deno-dist"

curl -L -o "$TOOLS_DIR/yt-dlp" \
  https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos

curl -L -o "$TOOLS_DIR/deno.zip" \
  https://github.com/denoland/deno/releases/latest/download/deno-aarch64-apple-darwin.zip

unzip -o "$TOOLS_DIR/deno.zip" -d "$TOOLS_DIR/deno-dist"
mv -f "$TOOLS_DIR/deno-dist/deno" "$TOOLS_DIR/deno"
chmod +x "$TOOLS_DIR/yt-dlp" "$TOOLS_DIR/deno"
rm -f "$TOOLS_DIR/deno.zip"
rmdir "$TOOLS_DIR/deno-dist" 2>/dev/null || true
