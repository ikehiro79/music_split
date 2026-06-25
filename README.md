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

On Windows, the safest way is to run the launcher. It uses `.venv` automatically, so PowerShell execution policy and Python file associations do not matter.

```powershell
.\run_app.bat
```

You can also run the app directly through the virtual environment:

```powershell
.\.venv\Scripts\python.exe app.py
```

## Usage

1. Click `MP3を開く` and select a music file.
2. The waveform is displayed in the main area.
3. Use `再生`, `一時停止`, and `停止` to preview the track.
4. Click the waveform to move the green playback cursor.
5. Use `ドラッグ操作` to choose what waveform dragging does.
6. In `拡大` mode, drag a rectangle on the waveform to zoom into that time range.
7. In `分割範囲` mode, drag on the waveform to add split cursors at the start and end of the selected range.
8. Use the mouse wheel on the waveform to zoom in or out around the mouse position.
9. Use `時間目盛り` to choose the graph's time tick spacing.
10. Click `拡大をリセット` to return to the full waveform.
11. Enter the required silence length in `無音秒数`.
12. Click `分割位置を検出` to show red dashed split cursors.
13. Double-click a red split cursor to delete it.
14. Adjust `無音しきい値 dBFS` and `保持する無音 ms` if needed.
15. Click `分割して保存`; numbered MP3 files are written to the output folder.

Lower threshold values are stricter. `-35` dBFS is a practical starting point for many tracks.
