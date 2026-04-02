import platform
import subprocess
from pathlib import Path


def downloads_directory() -> Path:
    return Path.home() / "Downloads"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def notify(title: str, message: str) -> None:
    system = platform.system()

    if system == "Darwin":
        script = (
            'display notification "{}" with title "{}"'.format(
                message.replace('"', '\\"'),
                title.replace('"', '\\"'),
            )
        )
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )


def reveal_in_file_manager(target: Path) -> None:
    system = platform.system()

    if system == "Darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
        return

    if system == "Windows":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return

    subprocess.run(["xdg-open", str(target.parent)], check=False)


def open_media_file(target: Path) -> None:
    system = platform.system()

    if system == "Darwin":
        if target.suffix.lower() == ".mp3":
            result = subprocess.run(
                ["open", "-a", "QuickTime Player", str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return

        subprocess.run(["open", str(target)], check=False)
        return

    if system == "Windows":
        subprocess.run(["cmd", "/c", "start", "", str(target)], check=False)
        return

    subprocess.run(["xdg-open", str(target)], check=False)
