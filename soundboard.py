"""
Soundboard - continuously mixes microphone input with triggered sound
clips and writes the result to a chosen output device (e.g. a virtual
audio cable), so sounds and voice reach Discord/games together as one
microphone. Includes a media downloader tab (yt-dlp) that saves clips
into the local Sounds/ folder, and a Sound Editor tab to trim clips and
adjust bass.

Config (devices + sound/hotkey mappings) is stored in soundboard_config.json,
created next to this script (or next to the executable, when built) on
first run. Sound files live in the Sounds/ folder next to this script.
"""

import collections
import json
import os
import re
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import imageio_ffmpeg
import numpy as np
import sounddevice as sd
import soundfile as sf
import yt_dlp
from pynput import keyboard as pynkeyboard
from scipy.signal import lfilter

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "soundboard_config.json")
SOUNDS_DIR = os.path.join(APP_DIR, "Sounds")
ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 1024
MIC_BUFFER_LIMIT = 50  # chunks; caps latency/memory if the output stalls
BASS_CUTOFF_HZ = 200.0

NO_DEVICE_LABEL = "(none)"

COLOR_BG = "#121212"
COLOR_SURFACE = "#1e1e1e"
COLOR_ROW = "#262626"
COLOR_ORANGE = "#ff8c00"
COLOR_ORANGE_HOVER = "#e67600"
COLOR_TEXT = "#f2f2f2"
COLOR_TEXT_DIM = "#9a9a9a"
COLOR_ERROR = "#ff5555"


def ensure_sounds_dir():
    os.makedirs(SOUNDS_DIR, exist_ok=True)


def resolve_sound_path(path):
    """Sound paths are either an absolute path (older configs, or a file
    picked from outside Sounds/) or a filename relative to Sounds/."""
    return path if os.path.isabs(path) else os.path.join(SOUNDS_DIR, path)


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "sound"


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    else:
        config = {}
    if "device" in config and "output_device" not in config:
        config["output_device"] = config.pop("device")
    config.setdefault("output_device", None)
    config.setdefault("input_device", None)
    config.pop("monitor_device", None)
    config.pop("monitor_muted", None)
    config.setdefault("hear_self", True)
    config.setdefault("sounds", [])
    for sound in config["sounds"]:
        sound.setdefault("enabled", True)
    return config


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _resample(data, orig_sr, target_sr):
    if orig_sr == target_sr or len(data) == 0:
        return data
    target_len = int(round(data.shape[0] * target_sr / orig_sr))
    orig_idx = np.arange(data.shape[0])
    target_idx = np.linspace(0, data.shape[0] - 1, num=target_len)
    resampled = np.empty((target_len, data.shape[1]), dtype=np.float32)
    for ch in range(data.shape[1]):
        resampled[:, ch] = np.interp(target_idx, orig_idx, data[:, ch])
    return resampled


def _match_channels(data, channels):
    if data.shape[1] == channels:
        return data
    if data.shape[1] == 1:
        return np.repeat(data, channels, axis=1)
    if channels == 1:
        return data.mean(axis=1, keepdims=True).astype(np.float32)
    return data[:, :channels]


def _low_shelf_coeffs(gain_db, cutoff_hz, sample_rate, slope=1.0):
    """RBJ Audio EQ Cookbook low-shelf biquad coefficients."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * cutoff_hz / sample_rate
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / 2 * np.sqrt((A + 1 / A) * (1 / slope - 1) + 2)
    sqrt_A = np.sqrt(A)

    b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * sqrt_A * alpha)
    b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
    b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * sqrt_A * alpha)
    a0 = (A + 1) + (A - 1) * cos_w0 + 2 * sqrt_A * alpha
    a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
    a2 = (A + 1) + (A - 1) * cos_w0 - 2 * sqrt_A * alpha

    b = np.array([b0, b1, b2]) / a0
    a = np.array([a0, a1, a2]) / a0
    return b, a


def apply_bass(data, gain_db, sample_rate, cutoff_hz=BASS_CUTOFF_HZ):
    if gain_db == 0 or len(data) == 0:
        return data
    b, a = _low_shelf_coeffs(gain_db, cutoff_hz, sample_rate)
    filtered = np.empty_like(data)
    for ch in range(data.shape[1]):
        filtered[:, ch] = lfilter(b, a, data[:, ch])
    return filtered.astype(np.float32)


class _ActiveSound:
    def __init__(self, data, key=None):
        self.data = data
        self.key = key
        self.position = 0

    def read(self, frames):
        end = min(self.position + frames, len(self.data))
        chunk = self.data[self.position:end]
        self.position = end
        finished = self.position >= len(self.data)
        if len(chunk) < frames:
            pad = np.zeros((frames - len(chunk), self.data.shape[1]), dtype=np.float32)
            chunk = np.vstack([chunk, pad]) if len(chunk) else pad
        return chunk, finished


class AudioEngine:
    """Continuously mixes the selected microphone with triggered sound
    clips and writes the result to the selected output device, so voice
    and soundboard clips are heard together on the virtual mic. Can also
    mirror sound clips (not the mic) to a local monitor device so you
    can hear what's playing yourself, independently mutable."""

    def __init__(self):
        self._lock = threading.Lock()
        self.input_stream = None
        self.output_stream = None
        self.monitor_stream = None
        self.monitor_muted = False
        self.input_channels = CHANNELS
        self.output_channels = CHANNELS
        self.monitor_channels = CHANNELS
        self._mic_buffer = collections.deque()
        self._active_sounds = []
        self._active_sounds_monitor = []

    @staticmethod
    def _device_channels(device, output):
        info = sd.query_devices(device)
        key = "max_output_channels" if output else "max_input_channels"
        return max(1, min(CHANNELS, info[key]))

    def start(self, input_device, output_device, monitor_device):
        self.stop()
        if input_device is not None:
            self.input_channels = self._device_channels(input_device, output=False)
            self.input_stream = sd.InputStream(
                device=input_device,
                channels=self.input_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._on_input,
            )
            self.input_stream.start()
        if output_device is not None:
            self.output_channels = self._device_channels(output_device, output=True)
            self.output_stream = sd.OutputStream(
                device=output_device,
                channels=self.output_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._on_output,
            )
            self.output_stream.start()
        if monitor_device is not None:
            self.monitor_channels = self._device_channels(monitor_device, output=True)
            self.monitor_stream = sd.OutputStream(
                device=monitor_device,
                channels=self.monitor_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._on_monitor_output,
            )
            self.monitor_stream.start()

    def stop(self):
        for attr in ("input_stream", "output_stream", "monitor_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.stop()
                stream.close()
                setattr(self, attr, None)
        with self._lock:
            self._mic_buffer.clear()
            self._active_sounds = []
            self._active_sounds_monitor = []

    def set_monitor_muted(self, muted):
        with self._lock:
            self.monitor_muted = muted
            if muted:
                self._active_sounds_monitor = []

    def _on_input(self, indata, frames, time_info, status):
        with self._lock:
            self._mic_buffer.append(indata.copy())
            while len(self._mic_buffer) > MIC_BUFFER_LIMIT:
                self._mic_buffer.popleft()

    def _on_output(self, outdata, frames, time_info, status):
        with self._lock:
            mixed = self._pull_mic_frames(frames, self.output_channels)
            still_active = []
            for sound in self._active_sounds:
                chunk, finished = sound.read(frames)
                mixed += chunk
                if not finished:
                    still_active.append(sound)
            self._active_sounds = still_active
        np.clip(mixed, -1.0, 1.0, out=mixed)
        outdata[:] = mixed

    def _on_monitor_output(self, outdata, frames, time_info, status):
        with self._lock:
            mixed = np.zeros((frames, self.monitor_channels), dtype=np.float32)
            still_active = []
            for sound in self._active_sounds_monitor:
                chunk, finished = sound.read(frames)
                mixed += chunk
                if not finished:
                    still_active.append(sound)
            self._active_sounds_monitor = still_active
        np.clip(mixed, -1.0, 1.0, out=mixed)
        outdata[:] = mixed

    def _pull_mic_frames(self, frames, channels):
        out = np.zeros((frames, channels), dtype=np.float32)
        filled = 0
        while filled < frames and self._mic_buffer:
            chunk = self._mic_buffer[0]
            take = min(frames - filled, len(chunk))
            piece = chunk[:take]
            if piece.shape[1] != channels:
                piece = _match_channels(piece, channels)
            out[filled:filled + take] = piece
            if take < len(chunk):
                self._mic_buffer[0] = chunk[take:]
            else:
                self._mic_buffer.popleft()
            filled += take
        return out

    def play_data(self, data, samplerate, key=None):
        """Queue audio for playback. A clip with the same key that is
        still playing is stopped first, so re-triggering restarts it."""
        if self.output_stream is None and self.monitor_stream is None:
            raise RuntimeError("No output device selected.")
        resampled = _resample(data, samplerate, SAMPLE_RATE)
        with self._lock:
            if key is not None:
                self._active_sounds = [s for s in self._active_sounds if s.key != key]
                self._active_sounds_monitor = [s for s in self._active_sounds_monitor if s.key != key]
            if self.output_stream is not None:
                main_data = _match_channels(resampled, self.output_channels)
                self._active_sounds.append(_ActiveSound(main_data, key))
            if self.monitor_stream is not None and not self.monitor_muted:
                monitor_data = _match_channels(resampled, self.monitor_channels)
                self._active_sounds_monitor.append(_ActiveSound(monitor_data, key))

    def play(self, path):
        data, samplerate = sf.read(path, dtype="float32", always_2d=True)
        self.play_data(data, samplerate, key=os.path.abspath(path))


class Soundboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Soundboard")
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        ensure_sounds_dir()

        is_first_run = not os.path.exists(CONFIG_PATH)
        self.config = load_config()
        self.audio_engine = AudioEngine()
        self.hotkey_listener = None

        self.editor_path = None
        self.editor_data = None
        self.editor_samplerate = None
        self._editor_sound_paths = {}

        self.input_devices = self._list_devices(output=False)
        self.output_devices = self._list_devices(output=True)

        if is_first_run:
            if len(self.input_devices) > 1:
                self.config["input_device"] = self.input_devices[1][0]
            if len(self.output_devices) > 1:
                self.config["output_device"] = self.output_devices[1][0]
            save_config(self.config)

        self.tabview = ctk.CTkTabview(
            self.root,
            fg_color=COLOR_SURFACE,
            segmented_button_fg_color=COLOR_SURFACE,
            segmented_button_selected_color=COLOR_ORANGE,
            segmented_button_selected_hover_color=COLOR_ORANGE_HOVER,
            segmented_button_unselected_color=COLOR_ROW,
            text_color=COLOR_TEXT,
        )
        self.tabview.pack(fill="both", expand=True, padx=8, pady=8)

        board_tab = self.tabview.add("Soundboard")
        download_tab = self.tabview.add("Download")
        editor_tab = self.tabview.add("Sound Editor")
        for tab in (board_tab, download_tab, editor_tab):
            tab.configure(fg_color=COLOR_BG)

        self._build_device_selectors(board_tab)
        self._build_sound_list(board_tab)
        self._build_controls(board_tab)

        self._build_download_tab(download_tab)
        self._build_editor_tab(editor_tab)

        self._restart_audio_engine()
        self._apply_hotkeys()

    # -- device listing -----------------------------------------------------

    @staticmethod
    def _list_devices(output):
        devices = sd.query_devices()
        key = "max_output_channels" if output else "max_input_channels"
        entries = [(None, NO_DEVICE_LABEL)]
        entries += [(i, d["name"]) for i, d in enumerate(devices) if d[key] > 0]
        return entries

    @staticmethod
    def _name_for_index(devices, index):
        for i, name in devices:
            if i == index:
                return name
        return devices[0][1]

    @staticmethod
    def _index_for_name(devices, name):
        for i, n in devices:
            if n == name:
                return i
        return None

    # -- device selection UI -------------------------------------------------

    def _build_device_selectors(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        frame.pack(fill="x", padx=4, pady=(4, 6))
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Microphone (input):", text_color=COLOR_TEXT).grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        input_names = [name for _, name in self.input_devices]
        current_input = self._name_for_index(self.input_devices, self.config.get("input_device"))
        self.input_var = tk.StringVar(value=current_input if current_input in input_names else input_names[0])
        self.input_menu = ctk.CTkOptionMenu(
            frame,
            variable=self.input_var,
            values=input_names,
            command=self._on_input_device_change,
            fg_color=COLOR_ROW,
            button_color=COLOR_ORANGE,
            button_hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_ROW,
        )
        self.input_menu.grid(row=0, column=1, sticky="ew", padx=8, pady=8)

        ctk.CTkLabel(frame, text="Virtual mic output:", text_color=COLOR_TEXT).grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        output_names = [name for _, name in self.output_devices]
        current_output = self._name_for_index(self.output_devices, self.config.get("output_device"))
        self.output_var = tk.StringVar(value=current_output if current_output in output_names else output_names[0])
        self.output_menu = ctk.CTkOptionMenu(
            frame,
            variable=self.output_var,
            values=output_names,
            command=self._on_output_device_change,
            fg_color=COLOR_ROW,
            button_color=COLOR_ORANGE,
            button_hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_ROW,
        )
        self.output_menu.grid(row=1, column=1, sticky="ew", padx=8, pady=8)

        self.hear_self_checkbox = ctk.CTkCheckBox(
            frame,
            text="Hear soundboard",
            fg_color=COLOR_ORANGE,
            hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_BG,
            text_color=COLOR_TEXT,
        )
        if self.config.get("hear_self", True):
            self.hear_self_checkbox.select()
        else:
            self.hear_self_checkbox.deselect()
        self.hear_self_checkbox.configure(command=self._on_toggle_hear_self)
        self.hear_self_checkbox.grid(row=1, column=2, sticky="w", padx=8, pady=8)

    def _on_input_device_change(self, selected_name):
        self.config["input_device"] = self._index_for_name(self.input_devices, selected_name)
        save_config(self.config)
        self._restart_audio_engine()

    def _on_output_device_change(self, selected_name):
        self.config["output_device"] = self._index_for_name(self.output_devices, selected_name)
        save_config(self.config)
        self._restart_audio_engine()

    def _on_toggle_hear_self(self):
        hear = bool(self.hear_self_checkbox.get())
        self.config["hear_self"] = hear
        save_config(self.config)
        self.audio_engine.set_monitor_muted(not hear)

    @staticmethod
    def _default_output_device():
        try:
            return sd.query_devices(kind="output")["index"]
        except Exception:
            return None

    def _restart_audio_engine(self):
        # Local copy goes to the system default output, unless that is
        # already the selected output (it would play twice).
        output_device = self.config.get("output_device")
        monitor_device = self._default_output_device()
        if monitor_device == output_device:
            monitor_device = None
        try:
            self.audio_engine.start(self.config.get("input_device"), output_device, monitor_device)
            self.audio_engine.set_monitor_muted(not self.config.get("hear_self", True))
        except Exception as e:
            messagebox.showerror("Audio device error", str(e))

    # -- sound list -----------------------------------------------------

    def _build_sound_list(self, parent):
        self.list_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color=COLOR_SURFACE,
            label_text="Sounds",
            label_text_color=COLOR_ORANGE,
        )
        self.list_frame.pack(fill="both", expand=True, padx=4, pady=6)
        self._refresh_sound_list()

    def _refresh_sound_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        for idx, sound in enumerate(self.config["sounds"]):
            resolved_path = resolve_sound_path(sound["path"])
            row = ctk.CTkFrame(self.list_frame, fg_color=COLOR_ROW)
            row.pack(fill="x", pady=3, padx=2)

            checkbox = ctk.CTkCheckBox(
                row,
                text="",
                width=24,
                fg_color=COLOR_ORANGE,
                hover_color=COLOR_ORANGE_HOVER,
                checkmark_color=COLOR_BG,
            )
            if sound.get("enabled", True):
                checkbox.select()
            else:
                checkbox.deselect()
            checkbox.configure(command=lambda i=idx, cb=checkbox: self._on_toggle_sound(i, cb))
            checkbox.pack(side="left", padx=(8, 4))

            missing = not os.path.exists(resolved_path)
            label_text = f"{sound['name']}  [{sound.get('hotkey') or 'no hotkey'}]"
            if missing:
                label_text += "  (file missing)"
            ctk.CTkLabel(
                row,
                text=label_text,
                anchor="w",
                text_color=COLOR_ERROR if missing else COLOR_TEXT,
            ).pack(side="left", fill="x", expand=True, padx=4)

            ctk.CTkButton(
                row, text="Play", width=60,
                command=lambda p=resolved_path: self.play_sound(p),
                fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
            ).pack(side="left", padx=3)
            ctk.CTkButton(
                row, text="Hotkey", width=70,
                command=lambda i=idx: self.set_hotkey(i),
                fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
                border_width=1, border_color=COLOR_ORANGE,
            ).pack(side="left", padx=3)
            ctk.CTkButton(
                row, text="Remove", width=70,
                command=lambda i=idx: self.remove_sound(i),
                fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ERROR,
                border_width=1, border_color=COLOR_ERROR,
            ).pack(side="left", padx=(3, 8))

        if hasattr(self, "editor_sound_menu"):
            self._refresh_editor_sound_list()

    def _on_toggle_sound(self, index, checkbox):
        self.config["sounds"][index]["enabled"] = bool(checkbox.get())
        save_config(self.config)
        self._apply_hotkeys()

    def _build_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        frame.pack(fill="x", padx=4, pady=(6, 4))
        ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left")

    # -- sound management -------------------------------------------------

    @staticmethod
    def _import_into_sounds_dir(source_path):
        """Copy an external file into Sounds/ (unless it's already there)
        and return the bare filename to store in config."""
        source_path = os.path.abspath(source_path)
        if os.path.dirname(source_path) == SOUNDS_DIR:
            return os.path.basename(source_path)
        ensure_sounds_dir()
        filename = sanitize_filename(os.path.basename(source_path))
        dest = unique_path(os.path.join(SOUNDS_DIR, filename))
        shutil.copy2(source_path, dest)
        return os.path.basename(dest)

    def add_sound(self):
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if not path:
            return
        stored_path = self._import_into_sounds_dir(path)
        name = os.path.splitext(os.path.basename(path))[0]
        self.config["sounds"].append({"name": name, "path": stored_path, "hotkey": None, "enabled": True})
        save_config(self.config)
        self._refresh_sound_list()

    def remove_sound(self, index):
        del self.config["sounds"][index]
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    def set_hotkey(self, index):
        dialog = ctk.CTkInputDialog(
            text="Enter hotkey (e.g. <ctrl>+<alt>+1), leave blank to clear:",
            title="Set hotkey",
        )
        hotkey = dialog.get_input()
        if hotkey is None:
            return
        hotkey = hotkey.strip()
        if hotkey and not self._is_valid_hotkey(hotkey):
            messagebox.showerror(
                "Invalid hotkey",
                f"'{hotkey}' is not a valid hotkey. Use a format like <ctrl>+<alt>+1.",
            )
            return
        self.config["sounds"][index]["hotkey"] = hotkey or None
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    @staticmethod
    def _is_valid_hotkey(hotkey):
        try:
            pynkeyboard.GlobalHotKeys({hotkey: lambda: None})
        except ValueError:
            return False
        return True

    # -- playback -----------------------------------------------------------

    def play_sound(self, path):
        try:
            self.audio_engine.play(path)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Playback error", str(e)))

    # -- hotkeys --------------------------------------------------------

    def _apply_hotkeys(self):
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
            self.hotkey_listener = None

        mapping = {}
        for sound in self.config["sounds"]:
            if not sound.get("enabled", True):
                continue
            hotkey = sound.get("hotkey")
            if hotkey and self._is_valid_hotkey(hotkey):
                resolved_path = resolve_sound_path(sound["path"])
                mapping[hotkey] = (lambda p=resolved_path: self.play_sound(p))

        if mapping:
            try:
                self.hotkey_listener = pynkeyboard.GlobalHotKeys(mapping)
                self.hotkey_listener.start()
            except Exception as e:
                messagebox.showwarning("Hotkey error", f"Could not register hotkeys: {e}")

    # -- download tab -------------------------------------------------------

    def _build_download_tab(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        frame.pack(fill="x", padx=4, pady=4)

        ctk.CTkLabel(
            frame, text="Video/clip URL (YouTube, TikTok, Instagram, ...):", text_color=COLOR_TEXT
        ).pack(anchor="w", padx=8, pady=(8, 2))
        self.download_url_entry = ctk.CTkEntry(
            frame,
            placeholder_text="https://...",
            fg_color=COLOR_ROW,
            text_color=COLOR_TEXT,
            border_color=COLOR_ORANGE,
        )
        self.download_url_entry.pack(fill="x", padx=8, pady=(0, 8))

        self.download_button = ctk.CTkButton(
            frame, text="Download as MP3", command=self._start_download,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        )
        self.download_button.pack(anchor="w", padx=8, pady=(0, 8))

        self.download_status = ctk.CTkLabel(frame, text="", text_color=COLOR_TEXT, anchor="w")
        self.download_status.pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkLabel(
            parent,
            text=(
                "Downloads are saved into the Sounds folder and added to your board "
                "automatically. Only download content you have the right to use - "
                "this may be against the source site's terms of service."
            ),
            text_color=COLOR_TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=480,
        ).pack(fill="x", padx=12, pady=(0, 8))

    def _start_download(self):
        url = self.download_url_entry.get().strip()
        if not url:
            return
        self.download_button.configure(state="disabled")
        self.download_status.configure(text="Downloading...", text_color=COLOR_TEXT)
        threading.Thread(target=self._download_worker, args=(url,), daemon=True).start()

    def _download_worker(self, url):
        try:
            ensure_sounds_dir()
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            outtmpl = os.path.join(SOUNDS_DIR, "%(title).100s.%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "ffmpeg_location": ffmpeg_path,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                base = ydl.prepare_filename(info)
                final_path = os.path.splitext(base)[0] + ".mp3"
                title = info.get("title") or os.path.splitext(os.path.basename(final_path))[0]
        except Exception as e:
            self.root.after(0, lambda: self._on_download_error(str(e)))
            return
        self.root.after(0, lambda: self._on_download_done(final_path, title))

    def _on_download_error(self, message):
        self.download_button.configure(state="normal")
        self.download_status.configure(text=f"Download failed: {message}", text_color=COLOR_ERROR)

    def _on_download_done(self, path, title):
        self.download_button.configure(state="normal")
        self.download_url_entry.delete(0, "end")
        self.download_status.configure(text=f"Saved: {os.path.basename(path)}", text_color=COLOR_ORANGE)
        self.config["sounds"].append({
            "name": title, "path": os.path.basename(path), "hotkey": None, "enabled": True,
        })
        save_config(self.config)
        self._refresh_sound_list()

    # -- sound editor tab -----------------------------------------------

    def _build_editor_tab(self, parent):
        top = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        top.pack(fill="x", padx=4, pady=4)
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Sound:", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.editor_sound_var = tk.StringVar(value="")
        self.editor_sound_menu = ctk.CTkOptionMenu(
            top,
            variable=self.editor_sound_var,
            values=[""],
            command=self._on_editor_sound_selected,
            fg_color=COLOR_ROW,
            button_color=COLOR_ORANGE,
            button_hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_ROW,
        )
        self.editor_sound_menu.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(
            top, text="Browse...", width=90, command=self._on_editor_browse,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).grid(row=0, column=2, sticky="e", padx=8, pady=8)

        self.editor_canvas = tk.Canvas(parent, height=140, bg=COLOR_ROW, highlightthickness=0)
        self.editor_canvas.pack(fill="x", padx=4, pady=4)
        self.editor_canvas.bind("<Configure>", lambda e: self._draw_waveform())

        trim_frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        trim_frame.pack(fill="x", padx=4, pady=4)
        trim_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(trim_frame, text="Start:", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.editor_start_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=self._on_trim_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_start_slider.set(0)
        self.editor_start_slider.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        self.editor_start_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_start_label.grid(row=0, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="End:", text_color=COLOR_TEXT).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.editor_end_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=self._on_trim_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_end_slider.set(1)
        self.editor_end_slider.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        self.editor_end_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_end_label.grid(row=1, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Bass:", text_color=COLOR_TEXT).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.editor_bass_slider = ctk.CTkSlider(
            trim_frame, from_=-12, to=12, command=self._on_bass_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_bass_slider.set(0)
        self.editor_bass_slider.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        self.editor_bass_label = ctk.CTkLabel(trim_frame, text="+0 dB", text_color=COLOR_TEXT, width=60)
        self.editor_bass_label.grid(row=2, column=2, padx=8, pady=6)

        buttons = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        buttons.pack(fill="x", padx=4, pady=(4, 8))
        ctk.CTkButton(
            buttons, text="Preview", command=self._on_editor_preview,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left", padx=(4, 4))
        ctk.CTkButton(
            buttons, text="Save as new sound", command=self._on_editor_save,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).pack(side="left", padx=(4, 4))

        self._refresh_editor_sound_list()

    def _refresh_editor_sound_list(self):
        self._editor_sound_paths = {}
        names = []
        for sound in self.config["sounds"]:
            label = sound["name"]
            while label in self._editor_sound_paths:
                label = f"{label} ({sound['path']})"
            self._editor_sound_paths[label] = resolve_sound_path(sound["path"])
            names.append(label)
        if not names:
            names = [""]
        self.editor_sound_menu.configure(values=names)
        if self.editor_sound_var.get() not in names:
            self.editor_sound_var.set(names[0])

    def _on_editor_sound_selected(self, label):
        path = self._editor_sound_paths.get(label)
        if path:
            self._load_editor_file(path)

    def _on_editor_browse(self):
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if path:
            self._load_editor_file(path)

    def _load_editor_file(self, path):
        try:
            data, samplerate = sf.read(path, dtype="float32", always_2d=True)
        except Exception as e:
            messagebox.showerror("Could not load sound", str(e))
            return
        self.editor_path = path
        self.editor_data = data
        self.editor_samplerate = samplerate
        duration = len(data) / samplerate
        self.editor_start_slider.configure(from_=0, to=duration)
        self.editor_end_slider.configure(from_=0, to=duration)
        self.editor_start_slider.set(0)
        self.editor_end_slider.set(duration)
        self.editor_start_label.configure(text="0.00s")
        self.editor_end_label.configure(text=f"{duration:.2f}s")
        self.editor_bass_slider.set(0)
        self.editor_bass_label.configure(text="+0 dB")
        self._draw_waveform()

    def _on_trim_change(self, _value=None):
        if self.editor_data is None:
            return
        duration = len(self.editor_data) / self.editor_samplerate
        start = self.editor_start_slider.get()
        end = self.editor_end_slider.get()
        if end <= start:
            end = min(start + 0.01, duration)
            self.editor_end_slider.set(end)
        self.editor_start_label.configure(text=f"{start:.2f}s")
        self.editor_end_label.configure(text=f"{end:.2f}s")
        self._draw_waveform()

    def _on_bass_change(self, value):
        self.editor_bass_label.configure(text=f"{value:+.0f} dB")

    def _draw_waveform(self):
        canvas = self.editor_canvas
        canvas.delete("all")
        if self.editor_data is None:
            return
        width = canvas.winfo_width() or 480
        height = canvas.winfo_height() or 140
        mono = self.editor_data.mean(axis=1)
        n = len(mono)
        if n == 0 or width <= 0:
            return
        duration = n / self.editor_samplerate
        start = self.editor_start_slider.get()
        end = self.editor_end_slider.get()
        start_x = (start / duration) * width if duration else 0
        end_x = (end / duration) * width if duration else width
        canvas.create_rectangle(start_x, 0, end_x, height, fill=COLOR_SURFACE, outline="")

        step = max(1, n // width)
        mid = height / 2
        for x in range(width):
            chunk = mono[x * step: x * step + step]
            if len(chunk) == 0:
                continue
            lo, hi = float(chunk.min()), float(chunk.max())
            canvas.create_line(x, mid - hi * mid, x, mid - lo * mid, fill=COLOR_ORANGE)

        canvas.create_line(start_x, 0, start_x, height, fill=COLOR_TEXT)
        canvas.create_line(end_x, 0, end_x, height, fill=COLOR_TEXT)

    def _get_editor_processed_data(self):
        start = self.editor_start_slider.get()
        end = self.editor_end_slider.get()
        start_sample = int(start * self.editor_samplerate)
        end_sample = int(end * self.editor_samplerate)
        trimmed = self.editor_data[start_sample:end_sample]
        gain_db = self.editor_bass_slider.get()
        return apply_bass(trimmed, gain_db, self.editor_samplerate)

    def _on_editor_preview(self):
        if self.editor_data is None:
            return
        try:
            processed = self._get_editor_processed_data()
            self.audio_engine.play_data(processed, self.editor_samplerate, key="editor-preview")
        except Exception as e:
            messagebox.showerror("Preview error", str(e))

    def _on_editor_save(self):
        if self.editor_data is None:
            return
        dialog = ctk.CTkInputDialog(text="Save as (filename, without extension):", title="Save edited sound")
        name = dialog.get_input()
        if not name:
            return
        name = sanitize_filename(name)
        ensure_sounds_dir()
        dest = unique_path(os.path.join(SOUNDS_DIR, name + ".wav"))
        try:
            processed = self._get_editor_processed_data()
            sf.write(dest, processed, self.editor_samplerate)
        except Exception as e:
            messagebox.showerror("Save error", str(e))
            return
        self.config["sounds"].append({
            "name": name, "path": os.path.basename(dest), "hotkey": None, "enabled": True,
        })
        save_config(self.config)
        self._refresh_sound_list()
        messagebox.showinfo("Saved", f"Saved and added to your board as '{name}'.")

    # -- lifecycle ------------------------------------------------------

    def _on_close(self):
        self.audio_engine.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self.root.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    root.geometry("640x640")
    root.minsize(520, 480)
    try:
        root.iconphoto(True, tk.PhotoImage(file=ICON_PATH))
    except tk.TclError:
        pass
    Soundboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
