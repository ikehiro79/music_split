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

1. Click `MP3を開く` and select a music file.
2. The waveform is displayed in the main area.
3. Use `再生`, `一時停止`, and `停止` to preview the track.
4. Click the waveform to move the green playback cursor.
5. Drag a rectangle on the waveform to zoom into that time range.
6. Use `時間目盛り` to choose the graph's time tick spacing.
7. Click `拡大をリセット` to return to the full waveform.
8. Enter the required silence length in `無音秒数`.
9. Click `分割位置を検出` to show red dashed split cursors.
10. Double-click a red split cursor to delete it.
11. Adjust `無音しきい値 dBFS` and `保持する無音 ms` if needed.
12. Click `分割して保存`; numbered MP3 files are written to the output folder.

Lower threshold values are stricter. `-35` dBFS is a practical starting point for many tracks.
