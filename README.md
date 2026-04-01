# ConvertLink

ConvertLink is a desktop app for downloading a video link as `MP3` or `MP4`.

## Features

- Paste a Youtube video link or short
- Download as `MP3` or `MP4`
- Preview MP4 quality options and estimated file size
- Save finished files to `Downloads`
- Show progress and a macOS completion notification

## Run

```bash
python3 -m pip install --target vendor -r requirements.txt
./setup_tools.sh
python3 main.py
```

## Build

```bash
python3 -m pip install --target vendor pyinstaller
./build_mac_app.sh
```
