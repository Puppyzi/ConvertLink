# ConvertLink

ConvertLink is a desktop app for downloading a video link as `MP3` or `MP4`.

## Download

Grab the latest build for your OS from the [Releases page](https://github.com/Puppyzi/ConvertLink/releases/latest):

- **macOS**: `ConvertLink-macOS.zip` — unzip and open `ConvertLink.app` (right-click → Open the first time, since the app is unsigned)
- **Windows**: `ConvertLink-Windows.zip` — unzip and run `ConvertLink.exe` (if SmartScreen appears, click "More info" → "Run anyway")

### Visual

<details>
  <summary>Current Design</summary>
  
  <img src="images/screenshots/2nd snip.png">
</details>

## Features

- Paste a YouTube video, X/Twitter post, or Instagram reel/post link
- Download as `MP3` or `MP4`
- Preview MP4 quality options and estimated file size
- Save finished files to `Downloads`
- Show progress and a desktop completion notification
- Works on macOS and Windows

## Run

macOS:

```bash
python3 -m pip install --target vendor -r requirements.txt
python3 setup_tools.py
python3 main.py
```

Windows:

```powershell
python -m pip install --target vendor -r requirements.txt
python setup_tools.py
python main.py
```

## Build

macOS:

```bash
python3 -m pip install --target vendor pyinstaller
./build_mac_app.sh
```

Windows:

```powershell
python -m pip install --target vendor pyinstaller
./build_windows_app.ps1
```
