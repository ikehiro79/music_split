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
from matplotlib.patches import Rectangle
from matplotlib.ticker import AutoLocator, MultipleLocator
from pydub import AudioSegment
from pydub.silence import detect_silence
import pygame


TIME_SCALE_OPTIONS: dict[str, float | None] = {
    "自動": None,
    "1秒": 1,
    "5秒": 5,
    "10秒": 10,
    "30秒": 30,
    "1分": 60,
    "5分": 300,
}


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
        self.title("MP3無音分割")
        self.geometry("1220x780")
        self.minsize(1040, 660)

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
        self.split_points_dirty = False
        self.play_cursor_line = None
        self.split_cursor_lines = []
        self.zoom_start_x: float | None = None
        self.zoom_start_px: tuple[float, float] | None = None
        self.zoom_rectangle: Rectangle | None = None
        self.did_drag_zoom = False

        self.file_var = tk.StringVar(value="MP3ファイルを選択してください")
        self.duration_var = tk.StringVar(value="-")
        self.position_var = tk.StringVar(value="00:00.000")
        self.silence_seconds_var = tk.StringVar(value="2.0")
        self.threshold_var = tk.StringVar(value="-35")
        self.keep_silence_var = tk.StringVar(value="150")
        self.output_dir_var = tk.StringVar(value=str(Path.cwd() / "output"))
        self.time_scale_var = tk.StringVar(value="自動")
        self.status_var = tk.StringVar(value="待機中")

        self._build_ui()
        self.after(100, self._poll_results)
        self.after(100, self._tick_playback)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Button(header, text="MP3を開く", command=self.open_mp3).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.file_var, anchor="w").grid(row=0, column=1, sticky="ew", padx=12)
        ttk.Label(header, textvariable=self.duration_var, anchor="e").grid(row=0, column=2, sticky="e")

        main = ttk.Frame(self, padding=(16, 8, 16, 16))
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(9, 4.8), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.axis.set_title("波形")
        self.axis.set_xlabel("時間 (秒)")
        self.axis.set_ylabel("振幅")
        self.axis.grid(True, alpha=0.25)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas.mpl_connect("button_press_event", self._on_waveform_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_waveform_drag)
        self.canvas.mpl_connect("button_release_event", self._on_waveform_release)

        controls = ttk.Frame(main, padding=(0, 12, 0, 0))
        controls.grid(row=1, column=0, sticky="ew")
        for index in range(9):
            controls.columnconfigure(index, weight=1 if index in (1, 3, 5) else 0)

        ttk.Label(controls, text="無音秒数").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.silence_seconds_var, width=10).grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(controls, text="無音しきい値 dBFS").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.threshold_var, width=10).grid(row=0, column=3, sticky="w", padx=(8, 16))
        ttk.Label(controls, text="保持する無音 ms").grid(row=0, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.keep_silence_var, width=10).grid(row=0, column=5, sticky="w", padx=(8, 16))
        ttk.Button(controls, text="分割位置を検出", command=self.detect_split_positions).grid(row=0, column=6, sticky="e", padx=(0, 8))
        ttk.Button(controls, text="保存先", command=self.choose_output_dir).grid(row=0, column=7, sticky="e", padx=(0, 8))
        ttk.Button(controls, text="分割して保存", command=self.split_audio).grid(row=0, column=8, sticky="e")

        playback = ttk.Frame(main, padding=(0, 10, 0, 0))
        playback.grid(row=2, column=0, sticky="ew")
        playback.columnconfigure(8, weight=1)
        ttk.Button(playback, text="再生", command=self.play_audio).grid(row=0, column=0, sticky="w")
        ttk.Button(playback, text="一時停止", command=self.pause_audio).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(playback, text="停止", command=self.stop_audio).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(playback, text="再生位置").grid(row=0, column=3, sticky="w", padx=(18, 8))
        ttk.Label(playback, textvariable=self.position_var, anchor="w").grid(row=0, column=4, sticky="w")
        ttk.Label(playback, text="時間目盛り").grid(row=0, column=5, sticky="w", padx=(18, 8))
        time_scale = ttk.Combobox(
            playback,
            textvariable=self.time_scale_var,
            values=list(TIME_SCALE_OPTIONS),
            width=8,
            state="readonly",
        )
        time_scale.grid(row=0, column=6, sticky="w")
        time_scale.bind("<<ComboboxSelected>>", lambda _event: self._apply_time_tick_spacing())
        ttk.Button(playback, text="拡大をリセット", command=self.reset_zoom).grid(row=0, column=7, sticky="w", padx=(8, 0))

        output_row = ttk.Frame(main, padding=(0, 10, 0, 0))
        output_row.grid(row=3, column=0, sticky="ew")
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="保存先").grid(row=0, column=0, sticky="w")
        ttk.Label(output_row, textvariable=self.output_dir_var, anchor="w").grid(row=0, column=1, sticky="ew", padx=(8, 0))

        status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(16, 0, 16, 12))
        status.grid(row=2, column=0, sticky="ew")

    def open_mp3(self) -> None:
        file_name = filedialog.askopenfilename(
            title="MP3ファイルを選択",
            filetypes=[("MP3ファイル", "*.mp3"), ("すべてのファイル", "*.*")],
        )
        if not file_name:
            return

        path = Path(file_name)
        try:
            self.status_var.set("読み込み中...")
            self.update_idletasks()
            audio = AudioSegment.from_mp3(path)
        except Exception as exc:
            messagebox.showerror("読み込みエラー", f"MP3ファイルを読み込めませんでした。\n\n{exc}")
            self.status_var.set("読み込み失敗")
            return

        self.pause_audio()
        self.audio = audio
        self.audio_path = path
        self.playback_position_ms = 0
        self.split_points_ms = []
        self.split_points_dirty = False
        self.file_var.set(path.name)
        self.duration_var.set(f"{len(audio) / 1000:.2f} 秒 / {audio.frame_rate:,} Hz")
        self.position_var.set(_format_time(0))
        self.output_dir_var.set(str(path.with_name(f"{path.stem}_split")))
        self._prepare_playback_file(path)
        self.status_var.set("波形を描画中...")
        self._draw_waveform(audio)
        self.status_var.set("読み込み完了")

    def choose_output_dir(self) -> None:
        directory = filedialog.askdirectory(title="保存先フォルダを選択")
        if directory:
            self.output_dir_var.set(directory)

    def detect_split_positions(self) -> None:
        if self.audio is None:
            messagebox.showinfo("MP3未選択", "先にMP3ファイルを選択してください。")
            return

        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc))
            return

        silent_ranges, split_points = self._detect_split_points(settings, self.audio)
        self.split_points_ms = sorted(split_points)
        self.split_points_dirty = False
        self._refresh_cursors()
        self.status_var.set(f"分割位置 {len(split_points)} 個を検出しました。検出無音: {len(silent_ranges)} 箇所")

    def split_audio(self) -> None:
        if self.audio is None or self.audio_path is None:
            messagebox.showinfo("MP3未選択", "先にMP3ファイルを選択してください。")
            return
        if self.worker and self.worker.is_alive():
            return

        try:
            settings = self._read_settings()
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc))
            return

        split_points = sorted(self.split_points_ms)
        silent_ranges: list[list[int]] = [[point, point] for point in split_points]
        if not split_points and not self.split_points_dirty:
            silent_ranges, split_points = self._detect_split_points(settings, self.audio)
            split_points = sorted(split_points)

        self.status_var.set("分割して保存中...")
        self.worker = threading.Thread(
            target=self._split_worker,
            args=(self.audio, self.audio_path, settings, split_points, silent_ranges),
            daemon=True,
        )
        self.worker.start()

    def play_audio(self) -> None:
        if self.audio is None or self.playback_file is None:
            messagebox.showinfo("MP3未選択", "先にMP3ファイルを選択してください。")
            return

        try:
            self._ensure_mixer()
            pygame.mixer.music.load(str(self.playback_file))
            self.playback_start_ms = self.playback_position_ms
            self.playback_started_at = time.perf_counter()
            pygame.mixer.music.play(start=self.playback_start_ms / 1000)
            self.is_playing = True
            self.status_var.set("再生中")
        except Exception as exc:
            self.is_playing = False
            messagebox.showerror("再生エラー", f"MP3ファイルを再生できませんでした。\n\n{exc}")

    def pause_audio(self) -> None:
        if self.is_playing:
            self._update_playback_position()
        if self.mixer_ready:
            pygame.mixer.music.stop()
        self.is_playing = False
        self.status_var.set("一時停止" if self.audio else "待機中")
        self._refresh_cursors()

    def stop_audio(self) -> None:
        if self.mixer_ready:
            pygame.mixer.music.stop()
        self.is_playing = False
        self.playback_position_ms = 0
        self.position_var.set(_format_time(0))
        self.status_var.set("停止" if self.audio else "待機中")
        self._refresh_cursors()

    def reset_zoom(self) -> None:
        if self.audio is None:
            return
        self.axis.set_xlim(0, len(self.audio) / 1000)
        self.axis.set_ylim(-1.05, 1.05)
        self._remove_zoom_rectangle()
        self._apply_time_tick_spacing()
        self.status_var.set("拡大表示を初期状態に戻しました")

    def _read_settings(self) -> SplitSettings:
        try:
            silence_seconds = float(self.silence_seconds_var.get())
            threshold = float(self.threshold_var.get())
            keep_silence = int(self.keep_silence_var.get())
        except ValueError as exc:
            raise ValueError("秒数、しきい値、保持する無音は数値で入力してください。") from exc

        if silence_seconds <= 0:
            raise ValueError("無音秒数は0より大きい値を入力してください。")
        if keep_silence < 0:
            raise ValueError("保持する無音 ms は0以上で入力してください。")

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
        self.axis.set_title(self.audio_path.name if self.audio_path else "波形")
        self.axis.set_xlabel("時間 (秒)")
        self.axis.set_ylabel("振幅")
        self.axis.set_xlim(0, len(audio) / 1000)
        self.axis.set_ylim(-1.05, 1.05)
        self.axis.grid(True, alpha=0.25)
        self.figure.tight_layout()
        self.play_cursor_line = None
        self.split_cursor_lines = []
        self._remove_zoom_rectangle()
        self._apply_time_tick_spacing(redraw=False)
        self._refresh_cursors()

    def _split_worker(
        self,
        audio: AudioSegment,
        audio_path: Path,
        settings: SplitSettings,
        split_points: list[int],
        silent_ranges: list[list[int]],
    ) -> None:
        try:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            segments = _slice_audio(audio, split_points, settings.keep_silence_ms)

            saved_files: list[Path] = []
            digits = max(2, len(str(len(segments))))
            for index, segment in enumerate(segments, start=1):
                if len(segment) == 0:
                    continue
                output_path = settings.output_dir / f"{audio_path.stem}_{index:0{digits}d}.mp3"
                segment.export(output_path, format="mp3")
                saved_files.append(output_path)

            self.result_queue.put(("success", (saved_files, silent_ranges, split_points)))
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def _poll_results(self) -> None:
        try:
            status, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_results)
            return

        if status == "success":
            saved_files, silent_ranges, split_points = payload
            self.split_points_ms = sorted(split_points)
            self.split_points_dirty = False
            self._refresh_cursors()
            self.status_var.set(f"{len(saved_files)} 個のMP3を保存しました。分割位置: {len(split_points)} 個")
            messagebox.showinfo("保存完了", f"{len(saved_files)} 個のMP3を保存しました。\n\n{self.output_dir_var.get()}")
        else:
            self.status_var.set("分割失敗")
            messagebox.showerror("分割エラー", f"MP3ファイルの分割に失敗しました。\n\n{payload}")

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

    def _on_waveform_press(self, event) -> None:
        if self.audio is None or event.inaxes != self.axis or event.xdata is None or event.button != 1:
            return

        if event.dblclick:
            deleted = self._delete_nearest_split_point(event.xdata * 1000)
            if deleted:
                self.status_var.set("分割カーソルを削除しました")
            return

        self.zoom_start_x = event.xdata
        self.zoom_start_px = (event.x, event.y)
        self.did_drag_zoom = False

    def _on_waveform_drag(self, event) -> None:
        if self.audio is None or self.zoom_start_x is None or self.zoom_start_px is None:
            return
        if event.inaxes != self.axis or event.xdata is None:
            return

        distance = math.hypot(event.x - self.zoom_start_px[0], event.y - self.zoom_start_px[1])
        if distance < 6:
            return

        self.did_drag_zoom = True
        start_x = self.zoom_start_x
        end_x = event.xdata
        left, right = sorted((start_x, end_x))
        y_min, y_max = self.axis.get_ylim()

        if self.zoom_rectangle is None or self.zoom_rectangle.axes is None:
            self.zoom_rectangle = Rectangle(
                (left, y_min),
                right - left,
                y_max - y_min,
                facecolor="#4c78a8",
                edgecolor="#1f4e79",
                alpha=0.18,
                linewidth=1.0,
            )
            self.axis.add_patch(self.zoom_rectangle)
        else:
            self.zoom_rectangle.set_xy((left, y_min))
            self.zoom_rectangle.set_width(right - left)
            self.zoom_rectangle.set_height(y_max - y_min)
        self.canvas.draw_idle()

    def _on_waveform_release(self, event) -> None:
        if self.audio is None or self.zoom_start_x is None:
            return

        start_x = self.zoom_start_x
        self.zoom_start_x = None
        self.zoom_start_px = None

        if self.did_drag_zoom and event.inaxes == self.axis and event.xdata is not None:
            left, right = sorted((start_x, event.xdata))
            if right - left >= 0.05:
                self.axis.set_xlim(left, right)
                self._apply_time_tick_spacing(redraw=False)
                self.status_var.set(f"{left:.2f} 秒から {right:.2f} 秒を拡大表示しました")
            self._remove_zoom_rectangle()
            self._refresh_cursors()
            return

        self._remove_zoom_rectangle()
        if event.inaxes == self.axis and event.xdata is not None:
            self._move_playback_cursor(event.xdata)

    def _move_playback_cursor(self, seconds: float) -> None:
        if self.audio is None:
            return
        audio_length = len(self.audio)
        self.playback_position_ms = max(0, min(audio_length, round(seconds * 1000)))
        self.position_var.set(_format_time(self.playback_position_ms))
        was_playing = self.is_playing
        if was_playing:
            self.pause_audio()
            self.play_audio()
        else:
            self._refresh_cursors()

    def _delete_nearest_split_point(self, target_ms: float) -> bool:
        if not self.split_points_ms:
            return False

        visible_left, visible_right = self.axis.get_xlim()
        tolerance_ms = max(250, (visible_right - visible_left) * 1000 * 0.015)
        nearest = min(self.split_points_ms, key=lambda point: abs(point - target_ms))
        if abs(nearest - target_ms) > tolerance_ms:
            return False

        self.split_points_ms.remove(nearest)
        self.split_points_dirty = True
        self._refresh_cursors()
        return True

    def _tick_playback(self) -> None:
        if self.is_playing:
            self._update_playback_position()
            if self.audio is not None and self.playback_position_ms >= len(self.audio):
                self.stop_audio()
            elif self.mixer_ready and not pygame.mixer.music.get_busy():
                self.is_playing = False
                self.status_var.set("再生終了")
            self._refresh_cursors()

        self.after(100, self._tick_playback)

    def _update_playback_position(self) -> None:
        elapsed_ms = round((time.perf_counter() - self.playback_started_at) * 1000)
        audio_length = len(self.audio) if self.audio is not None else 0
        self.playback_position_ms = max(0, min(audio_length, self.playback_start_ms + elapsed_ms))
        self.position_var.set(_format_time(self.playback_position_ms))

    def _apply_time_tick_spacing(self, redraw: bool = True) -> None:
        spacing = TIME_SCALE_OPTIONS[self.time_scale_var.get()]
        self.axis.xaxis.set_major_locator(AutoLocator() if spacing is None else MultipleLocator(spacing))
        self.axis.grid(True, alpha=0.25)
        if redraw:
            self.canvas.draw_idle()

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

    def _remove_zoom_rectangle(self) -> None:
        if self.zoom_rectangle is not None and self.zoom_rectangle.axes is not None:
            self.zoom_rectangle.remove()
        self.zoom_rectangle = None
        self.did_drag_zoom = False

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
