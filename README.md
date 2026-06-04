# music_split

A Python GUI app that loads an MP3 file, displays its waveform, detects silence longer than the number of seconds entered on screen, and saves the split MP3 segments.

## Setup

Python 3.10 or later is recommended. MP3 decoding and encoding require `ffmpeg`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `ffmpeg` is not installed, install it with winget on Windows.

```powershell
winget install Gyan.FFmpeg
```

## Run

```powershell
python app.py
```

## Usage

1. Click `Open MP3` and select a music file.
2. The waveform is displayed in the main area.
3. Use `Play`, `Pause`, and `Stop` to preview the track.
4. Click the waveform to move the green playback cursor.
5. Enter the required silence length in `Silence seconds`.
6. Click `Detect Split Points` to show red dashed split cursors.
7. Adjust `Threshold dBFS` and `Keep silence ms` if needed.
8. Click `Split and Save`; numbered MP3 files are written to the output folder.

Lower threshold values are stricter. `-35` dBFS is a practical starting point for many tracks.
