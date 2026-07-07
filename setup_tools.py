"""Download the yt-dlp and Deno binaries for the current platform into tools/."""

import io
import platform
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

YT_DLP_RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
DENO_RELEASE = "https://github.com/denoland/deno/releases/latest/download/{asset}"

TOOLS_DIR = Path(__file__).resolve().parent / "tools"


def _platform_assets() -> tuple[str, str, str, str]:
    """Return (yt-dlp asset, yt-dlp local name, deno zip asset, deno local name)."""
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        return "yt-dlp.exe", "yt-dlp.exe", "deno-x86_64-pc-windows-msvc.zip", "deno.exe"

    if system == "Darwin":
        deno_arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return "yt-dlp_macos", "yt-dlp", f"deno-{deno_arch}-apple-darwin.zip", "deno"

    deno_arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    return "yt-dlp", "yt-dlp", f"deno-{deno_arch}-unknown-linux-gnu.zip", "deno"


def _download(url: str) -> bytes:
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url) as response:
        return response.read()


def _make_executable(path: Path) -> None:
    if platform.system() != "Windows":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    yt_dlp_asset, yt_dlp_name, deno_asset, deno_name = _platform_assets()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    yt_dlp_path = TOOLS_DIR / yt_dlp_name
    yt_dlp_path.write_bytes(_download(YT_DLP_RELEASE.format(asset=yt_dlp_asset)))
    _make_executable(yt_dlp_path)

    deno_zip = _download(DENO_RELEASE.format(asset=deno_asset))
    with zipfile.ZipFile(io.BytesIO(deno_zip)) as archive:
        deno_path = TOOLS_DIR / deno_name
        deno_path.write_bytes(archive.read(deno_name))
        _make_executable(deno_path)

    print(f"Tools installed into {TOOLS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
