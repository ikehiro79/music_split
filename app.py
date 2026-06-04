from __future__ import annotations

import math
import os
import queue
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import matplotlib

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
matplotlib.use("TkAgg")
matplotlib.rcParams["font.family"] = [
    "Yu Gothic",
    "Meiryo",
    "MS Gothic",
    "Noto Sans CJK JP",
    "DejaVu Sans",
]

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from pydub import AudioSegment
from pydub.silence import detect_silence
import pygame


def configure_ffmpeg() -> None:
    ffmpeg_path = shutil.which("ffmpeg") or _find_winget_tool("ffmpeg.exe")
    ffprobe_path = shutil.which("ffprobe") or _find_winget_tool("ffprobe.exe")
    if ffmpeg_path:
        _prepend_path(Path(ffmpeg_path).parent)
        AudioSegment.converter = ffmpeg_path
    if ffprobe_path:
        _prepend_path(Path(ffprobe_path).parent)
        AudioSegment.ffprobe = ffprobe_path


def _prepend_path(directory: Path) -> None:
    path_value = os.environ.get("PATH", "")
    directory_text = str(directory)
    paths = path_value.split(os.pathsep) if path_value else []
    if directory_text not in paths:
        os.environ["PATH"] = directory_text + os.pathsep + path_value


def _find_winget_tool(tool_name: str) -> str | None:
    package_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if not package_root.exists():
        return None

    matches = sorted(package_root.glob(f"Gyan.FFmpeg_*/*/bin/{tool_name}"), reverse=True)
    return str(matches[0]) if matches else None


configure_ffmpeg()


@dataclass(frozen=True)
class SplitSettings:
    min_silence_ms: int
    silence_thresh_dbfs: float
    keep_silence_ms: int
    output_dir: Path


class MusicSplitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MP3 Silence Splitter")
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.audio: AudioSegment | None = None
        self.audio_path: Path | None = None
        self.worker: threading.Thread | None = None
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.playback_temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.playback_file: Path | None = None
        self.playback_position_ms = 0
        self.playback_start_ms = 0
        self.playback_started_at = 0.0
        self.is_playing = False
        self.mixer_ready = False

        self.split_points_ms: list[int] = []
        self.play_cursor_line = None
        self.split_cursor_lines = []

        self.file_var = tk.StringVar(value="Select an MP3 file")
        self.duration_var = tk.StringVar(value="-")
        self.position_var = tk.StringVar(value="00:00.000")
        self.silence_seconds_var = tk.StringVar(value="2.0")
        self.threshold_var = tk.StringVar(value="-35")
        self.keep_silence_var = tk.StringVar(value="150")
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "output"))
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.after(100, self._poll_results)
        self.after(100, self._tick_playback)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Button(header, text="Open MP3", command=self.open_mp3).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.file_var, anchor="w").grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Label(header, textvariable=self.duration_var, anchor="e").grid(row=0, column=2, sticky="e")

        main = ttk.Frame(self, padding=(16, 8, 16, 16))
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(9, 4.8), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.axis.set_title("Waveform")
        self.axis.set_xlabel("Time (s)")
        self.axis.set_ylabel("Amplitude")
        self.axis.grid(True, alpha=0.25)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas.mpl_connect("button_press_event", self._on_waveform_click)

        controls = ttk.Frame(main, padding=(0, 12, 0, 0))
        controls.grid(row=1, column=0, sticky="ew")
        for index in range(9):
            controls.columnconfigure(index, weight=1 if index in (1, 3, 5) else 0)

        ttk.Label(controls, text="Silence seconds").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.silence_seconds_var, width=10).grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(controls, text="Threshold dBFS").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.threshold_var, width=10).grid(row=0, column=3, sticky="w", padx=(8, 16))
        ttk.Label(controls, text="Keep silence ms").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.keep_silence_var, width=10).grid(row=0, column=5, sticky="w", padx=(8, 16))
        ttk.Button(controls, text="Detect Split Points", command=self.detect_split_positions).grid(row=0, column=6, sticky="e", padx=(0, 8))
        ttk.Button(controls, text="Output", command=self.choose_output_dir).grid(row=0, column=7, sticky="e", padx=(0, 8))
        ttk.Button(controls, text="Split and Save", command=self.split_audio).grid(row=0, column=8, sticky="e")

        playback = ttk.Frame(main, padding=(0, 10, 0, 0))
        playback.grid(row=2, column=0, sticky="ew")
        playback.columnconfigure(4, weight=1)
        ttk.Button(playback, text="Play", command=self.play_audio).grid(row=0, column=0, sticky="w")
        ttk.Button(playback, text="Pause", command=self.pause_audio).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(playback, text="Stop", command=self.stop_audio).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(playback, text="Position").grid(row=0, column=3, sticky="w", padx=(18, 8))
        ttk.Label(playback, textvariable=self.position_var, anchor="w").grid(row=0, column=4, sticky="ew")

        output_row = ttk.Frame(main, padding=(0, 10, 0, 0))
        output_row.grid(row=3, column=0, sticky="ew")
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="Output").grid(row=0, column=0, sticky="w")
        ttk.Label(output_row, textvariable=self.output_dir_var, anchor="w").grid(row=0, column=1, sticky="ew", padx=(8, 0))

        status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(16, 0, 16, 12))
        status.grid(row=2, column=0, sticky="ew")

    def open_mp3(self) -> None:
        file_name = filedialog.askopenfilename(
            title="Select MP3 file",
            filetypes=[("MP3 files", "*.mp3"), ("All files", "*.*")],
        )
        if not file_name:
            return

        path = Path(file_name)
        try:
            self.status_var.set("Loading...")
            self.update_idletasks()
            audio = AudioSegment.from_mp3(path)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not load the MP3 file.\n\n{exc}")
            self.status_var.set("Load failed")
            return

        self.pause_audio()
        self.audio = audio
        self.audio_path = path
        self.playback_position_ms = 0
        self.split_points_ms = []
        self.file_var.set(path.name)
        self.duration_var.set(f"{len(audio) / 1000:.2f} sec / {audio.frame_rate:,} Hz")
        self.position_var.set(_format_time(0))
        self.output_dir_var.set(str(path.with_name(f"{path.stem}_split")))
        self._prepare_playback_file(path)
        self.status_var.set("Drawing waveform...")
        self._draw_waveform(audio)
        self.status_var.set("Loaded")

    def choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(title="Select output folder")
        if directory:
            self.output_dir_var.set(directory)

    def detect_split_positions(self) -> None:
        if self.audio is None:
            messagebox.showinfo("No MP3 Selected", "Select an MP3 file first.")
            return

        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
            return

        silent_ranges, split_points = self._detect_split_points(settings, self.audio)
        self.split_points_ms = split_points
        self._refresh_cursors()
        self.status_var.set(f"Detected {len(split_points)} split points from {len(silent_ranges)} silent ranges")

    def split_audio(self) -> None:
        if self.audio is None or self.audio_path is None:
            messagebox.showinfo("No MP3 Selected", "Select an MP3 file first.")
            return
        if self.worker and self.worker.is_alive():
            return

        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
            return

        self.status_var.set("Detecting silence and splitting...")
        self.worker = threading.Thread(
            target=self._split_worker,
            args=(self.audio, self.audio_path, settings),
            daemon=True,
        )
        self.worker.start()

    def play_audio(self) -> None:
        if self.audio is None or self.playback_file is None:
            messagebox.showinfo("No MP3 Selected", "Select an MP3 file first.")
            return

        try:
            self._ensure_mixer()
            pygame.mixer.music.load(str(self.playback_file))
            self.playback_start_ms = self.playback_position_ms
            self.playback_started_at = time.perf_counter()
            pygame.mixer.music.play(start=self.playback_start_ms / 1000)
            self.is_playing = True
            self.status_var.set("Playing")
        except Exception as exc:
            self.is_playing = False
            messagebox.showerror("Playback Error", f"Could not play the MP3 file.\n\n{exc}")

    def pause_audio(self) -> None:
        if self.is_playing:
            self._update_playback_position()
        if self.mixer_ready:
            pygame.mixer.music.stop()
        self.is_playing = False
        self.status_var.set("Paused" if self.audio else "Ready")
        self._refresh_cursors()

    def stop_audio(self) -> None:
        if self.mixer_ready:
            pygame.mixer.music.stop()
        self.is_playing = False
        self.playback_position_ms = 0
        self.position_var.set(_format_time(0))
        self.status_var.set("Stopped" if self.audio else "Ready")
        self._refresh_cursors()

    def _read_settings(self) -> SplitSettings:
        try:
            silence_seconds = float(self.silence_seconds_var.get())
            threshold = float(self.threshold_var.get())
            keep_silence = int(self.keep_silence_var.get())
        except ValueError as exc:
            raise ValueError("Seconds, threshold, and keep-silence values must be numeric.") from exc

        if silence_seconds <= 0:
            raise ValueError("Silence seconds must be greater than 0.")
        if keep_silence < 0:
            raise ValueError("Keep silence ms must be 0 or greater.")

        return SplitSettings(
            min_silence_ms=round(silence_seconds * 1000),
            silence_thresh_dbfs=threshold,
            keep_silence_ms=keep_silence,
            output_dir=Path(self.output_dir_var.get()),
        )

    def _draw_waveform(self, audio: AudioSegment) -> None:
        mono = audio.set_channels(1)
        samples = mono.get_array_of_samples()
        sample_rate = mono.frame_rate
        max_points = 12000
        step = max(1, math.ceil(len(samples) / max_points))

        xs = [index / sample_rate for index in range(0, len(samples), step)]
        ys = [samples[index] for index in range(0, len(samples), step)]
        peak = max((abs(value) for value in ys), default=1)
        normalized = [value / peak for value in ys]

        self.axis.clear()
        self.axis.plot(xs, normalized, linewidth=0.7, color="#1f77b4")
        self.axis.set_title(self.audio_path.name if self.audio_path else "Waveform")
        self.axis.set_xlabel("Time (s)")
        self.axis.set_ylabel("Amplitude")
        self.axis.set_ylim(-1.05, 1.05)
        self.axis.grid(True, alpha=0.25)
        self.figure.tight_layout()
        self.play_cursor_line = None
        self.split_cursor_lines = []
        self._refresh_cursors()

    def _split_worker(self, audio: AudioSegment, audio_path: Path, settings: SplitSettings) -> None:
        try:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            silent_ranges, split_points = self._detect_split_points(settings, audio)
            segments = _slice_audio(audio, split_points, settings.keep_silence_ms)

            saved_files: list[Path] = []
            digits = max(2, len(str(len(segments))))
            for index, segment in enumerate(segments, start=1):
                if len(segment) == 0:
                    continue
                output_path = settings.output_dir / f"{audio_path.stem}_{index:0{digits}d}.mp3"
                segment.export(output_path, format="mp3")
                saved_files.append(output_path)

            self.result_queue.put(("success", (saved_files, silent_ranges)))
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def _poll_results(self) -> None:
        try:
            status, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_results)
            return

        if status == "success":
            saved_files, silent_ranges = payload
            self.split_points_ms = [round((start + end) / 2) for start, end in silent_ranges]
            self._refresh_cursors()
            self.status_var.set(f"Saved {len(saved_files)} MP3 files. Silent ranges: {len(silent_ranges)}")
            messagebox.showinfo("Done", f"Saved {len(saved_files)} MP3 files.\n\n{self.output_dir_var.get()}")
        else:
            self.status_var.set("Split failed")
            messagebox.showerror("Split Error", f"Could not split the MP3 file.\n\n{payload}")

        self.after(100, self._poll_results)

    def _detect_split_points(
        self,
        settings: SplitSettings,
        audio: AudioSegment | None = None,
    ) -> tuple[list[list[int]], list[int]]:
        target_audio = audio or self.audio
        if target_audio is None:
            return [], []

        silent_ranges = detect_silence(
            target_audio,
            min_silence_len=settings.min_silence_ms,
            silence_thresh=settings.silence_thresh_dbfs,
            seek_step=10,
        )
        split_points = [round((start + end) / 2) for start, end in silent_ranges]
        return silent_ranges, split_points

    def _ensure_mixer(self) -> None:
        if self.mixer_ready:
            return
        pygame.mixer.init()
        self.mixer_ready = True

    def _prepare_playback_file(self, source_path: Path) -> None:
        if self.playback_temp_dir is not None:
            self.playback_temp_dir.cleanup()
            self.playback_temp_dir = None

        ascii_only = all(ord(character) < 128 for character in str(source_path))
        if ascii_only:
            self.playback_file = source_path
            return

        self.playback_temp_dir = tempfile.TemporaryDirectory(prefix="music_split_")
        playback_path = Path(self.playback_temp_dir.name) / "current.mp3"
        shutil.copy2(source_path, playback_path)
        self.playback_file = playback_path

    def _on_waveform_click(self, event) -> None:
        if self.audio is None or event.inaxes != self.axis or event.xdata is None or event.button != 1:
            return

        audio_length = len(self.audio)
        self.playback_position_ms = max(0, min(audio_length, round(event.xdata * 1000)))
        self.position_var.set(_format_time(self.playback_position_ms))
        was_playing = self.is_playing
        if was_playing:
            self.pause_audio()
            self.play_audio()
        else:
            self._refresh_cursors()

    def _tick_playback(self) -> None:
        if self.is_playing:
            self._update_playback_position()
            if self.audio is not None and self.playback_position_ms >= len(self.audio):
                self.stop_audio()
            elif self.mixer_ready and not pygame.mixer.music.get_busy():
                self.is_playing = False
                self.status_var.set("Playback ended")
            self._refresh_cursors()

        self.after(100, self._tick_playback)

    def _update_playback_position(self) -> None:
        elapsed_ms = round((time.perf_counter() - self.playback_started_at) * 1000)
        audio_length = len(self.audio) if self.audio is not None else 0
        self.playback_position_ms = max(0, min(audio_length, self.playback_start_ms + elapsed_ms))
        self.position_var.set(_format_time(self.playback_position_ms))

    def _refresh_cursors(self) -> None:
        for line in self.split_cursor_lines:
            line.remove()
        self.split_cursor_lines = []

        position_seconds = self.playback_position_ms / 1000
        if self.play_cursor_line is None or self.play_cursor_line.axes is None:
            self.play_cursor_line = self.axis.axvline(position_seconds, color="#1b9e77", linewidth=1.8)
        else:
            self.play_cursor_line.set_xdata([position_seconds, position_seconds])

        for point_ms in self.split_points_ms:
            line = self.axis.axvline(point_ms / 1000, color="#d62728", linestyle="--", linewidth=1.2, alpha=0.9)
            self.split_cursor_lines.append(line)

        self.canvas.draw_idle()

    def destroy(self) -> None:
        if self.mixer_ready:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        if self.playback_temp_dir is not None:
            self.playback_temp_dir.cleanup()
        super().destroy()


def _slice_audio(audio: AudioSegment, split_points: list[int], keep_silence_ms: int) -> list[AudioSegment]:
    if not split_points:
        return [audio]

    segments: list[AudioSegment] = []
    start = 0
    audio_length = len(audio)
    for point in split_points:
        end = max(start, min(audio_length, point + keep_silence_ms))
        segment_start = max(0, start - keep_silence_ms)
        if end > segment_start:
            segments.append(audio[segment_start:end])
        start = min(audio_length, point)

    if start < audio_length:
        segments.append(audio[max(0, start - keep_silence_ms) : audio_length])

    return segments


def _format_time(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minute:02d}:{sec:02d}.{ms:03d}"
    return f"{minute:02d}:{sec:02d}.{ms:03d}"


if __name__ == "__main__":
    app = MusicSplitApp()
    app.mainloop()
