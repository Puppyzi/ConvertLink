# ConvertLink

ConvertLink is a desktop app for downloading a video link as `MP3` or `MP4`.

### Visual

<details>
  <summary>Current Design</summary>
  
  <img src="images/screenshots/1st snip.png">
</details>

## Features

- Paste a YouTube video, X/Twitter post, or Instagram reel/post link
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
