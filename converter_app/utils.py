import platform
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
)

_WINDOWS_TOAST_TEMPLATE = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast><visual><binding template="ToastGeneric"><text>{title}</text><text>{message}</text></binding></visual></toast>
'@)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""


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
        return

    if system == "Windows":
        script = _WINDOWS_TOAST_TEMPLATE.format(
            title=escape(title),
            message=escape(message),
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            creationflags=SUBPROCESS_FLAGS,
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
