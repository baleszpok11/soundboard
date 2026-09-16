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
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox

import customtkinter as ctk
import imageio_ffmpeg
import numpy as np
try:
    import sounddevice as sd
except OSError as e:
    # sounddevice bundles PortAudio only on Windows and macOS.
    sd = None
    PORTAUDIO_ERROR = e
import soundfile as sf
import yt_dlp
from pynput import keyboard as pynkeyboard
from scipy.signal import lfilter

def _data_dir():
    # A PyInstaller onefile build runs from a temp dir that is deleted on
    # exit, so user data must live elsewhere. macOS .app bundles may be
    # read-only (app translocation), so they use ~/Documents/Soundboard.
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Documents", "Soundboard")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(sys.executable))


APP_DIR = _data_dir()
CONFIG_PATH = os.path.join(APP_DIR, "soundboard_config.json")
SOUNDS_DIR = os.path.join(APP_DIR, "Sounds")
ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 1024
MIC_TARGET_FRAMES = SAMPLE_RATE // 10  # 100 ms cushion against uneven mic delivery
MIC_MAX_FRAMES = SAMPLE_RATE // 2  # drop older mic audio beyond 500 ms
BASS_CUTOFF_HZ = 200.0
VOLUME_MAX = 200  # percent
CLIP_CACHE_BYTES = 512 * 1024 * 1024  # decoded clips kept in memory
LIMITER_CEILING = 0.89  # about -1 dBFS
LIMITER_RELEASE_S = 0.3

NO_DEVICE_LABEL = "(none)"
STREAM_TIMEOUT_S = 2.0  # a running stream with no callbacks for this long is treated as lost
RECONNECT_INTERVAL_S = 5.0
RECONNECT_WINDOW_S = 120.0
# On Windows every device is listed once per host API; show one API only.
# WASAPI has full names and lower latency; MME is the fallback.
WINDOWS_HOSTAPIS = ("Windows WASAPI", "MME")
MME_NAME_LENGTH = 31
# Substrings that identify common virtual cables, preferred as the output.
VIRTUAL_CABLE_HINTS = ("cable input", "blackhole", "vb-audio", "soundboard")
# Inputs that carry what the PC plays rather than a real microphone.
LOOPBACK_INPUT_HINTS = ("cable output", "blackhole", "stereo mix", "what u hear", "loopback", "wave out", "monitor of")

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
    """Raises ValueError if the file exists but is not a valid config."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict) or not isinstance(config.get("sounds", []), list):
            raise ValueError("config has an unexpected structure")
    if "device" in config and "output_device" not in config:
        config["output_device"] = config.pop("device")
    config.setdefault("output_device", None)
    config.setdefault("input_device", None)
    config.pop("monitor_device", None)
    config.pop("monitor_muted", None)
    config.setdefault("hear_self", True)
    config.setdefault("mic_volume", 100)
    config.setdefault("sound_volume", 100)
    config.setdefault("stop_hotkey", None)
    config.setdefault("sounds", [])
    config["sounds"] = [s for s in config["sounds"] if isinstance(s, dict) and s.get("path")]
    for sound in config["sounds"]:
        sound.setdefault("name", os.path.splitext(os.path.basename(sound["path"]))[0])
        sound.setdefault("hotkey", None)
        sound.setdefault("enabled", True)
        sound.setdefault("volume", 100)
    return config


def save_config(config):
    # Write to a temp file and swap it in, so a crash mid-write can't
    # leave a truncated config behind.
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def write_error_log(text):
    """Append to a log next to the config, or in the temp dir if that
    folder isn't writable. Returns the path written, or None."""
    for folder in (APP_DIR, tempfile.gettempdir()):
        path = os.path.join(folder, "soundboard_error.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
            return path
        except OSError:
            continue
    return None


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
    def __init__(self, data, key=None, gain=1.0):
        self.data = data
        self.key = key
        self.gain = gain
        self.position = 0

    def read(self, frames):
        end = min(self.position + frames, len(self.data))
        chunk = self.data[self.position:end] * self.gain
        self.position = end
        finished = self.position >= len(self.data)
        if len(chunk) < frames:
            pad = np.zeros((frames - len(chunk), self.data.shape[1]), dtype=np.float32)
            chunk = np.vstack([chunk, pad]) if len(chunk) else pad
        return chunk, finished


class _Limiter:
    """Block-based peak limiter. Gain drops instantly when a block would
    exceed the ceiling and recovers smoothly afterwards, so loud mixes
    get quieter instead of distorting."""

    def __init__(self):
        self.gain = 1.0
        blocks_per_second = SAMPLE_RATE / BLOCK_SIZE
        self.release = 1.0 - np.exp(-1.0 / (LIMITER_RELEASE_S * blocks_per_second))

    def process(self, block):
        peak = float(np.abs(block).max()) if block.size else 0.0
        target = min(1.0, LIMITER_CEILING / peak) if peak > 0 else 1.0
        if target < self.gain:
            # A downward ramp would let the start of the block overshoot.
            self.gain = target
            block *= target
        else:
            new_gain = self.gain + (target - self.gain) * self.release
            block *= np.linspace(self.gain, new_gain, len(block), dtype=np.float32)[:, None]
            self.gain = new_gain
        np.clip(block, -1.0, 1.0, out=block)
        return block


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
        self._mic_frames = 0
        self._mic_ready = False
        self._active_sounds = []
        self._active_sounds_monitor = []
        self.dropouts = 0
        self.mic_gain = 1.0
        self.sound_gain = 1.0
        self._output_limiter = _Limiter()
        self._monitor_limiter = _Limiter()
        self.on_error = None
        self._started_at = 0.0
        self._last_callback = {}
        self._clip_cache = collections.OrderedDict()
        self._clip_cache_bytes = 0
        self._cache_lock = threading.Lock()
        # Decoding runs here, not in the caller: pynput's keyboard hook
        # blocks every keypress on the system until its callback returns.
        self._requests = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    @staticmethod
    def _device_channels(device, output):
        info = sd.query_devices(device)
        key = "max_output_channels" if output else "max_input_channels"
        return max(1, min(CHANNELS, info[key]))

    @staticmethod
    def _extra_settings(device):
        # WASAPI shared mode only accepts the device's own sample rate
        # unless Windows is allowed to convert.
        hostapi = sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]
        if hostapi == "Windows WASAPI":
            return sd.WasapiSettings(auto_convert=True)
        return None

    def start(self, input_device, output_device, monitor_device):
        self.stop()
        self._started_at = time.monotonic()
        self._last_callback = {}
        if input_device is not None:
            self.input_channels = self._device_channels(input_device, output=False)
            self.input_stream = sd.InputStream(
                device=input_device,
                channels=self.input_channels,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(input_device),
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
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(output_device),
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
                latency="high",
                dtype="float32",
                extra_settings=self._extra_settings(monitor_device),
                callback=self._on_monitor_output,
            )
            self.monitor_stream.start()

    def stop(self):
        for attr in ("input_stream", "output_stream", "monitor_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass  # the device may already be gone
                setattr(self, attr, None)
        with self._lock:
            self._mic_buffer.clear()
            self._mic_frames = 0
            self._mic_ready = False
            self._active_sounds = []
            self._active_sounds_monitor = []
            self.dropouts = 0
            self._output_limiter = _Limiter()
            self._monitor_limiter = _Limiter()

    def lost_streams(self):
        """Names ("input", "output") of open streams that have stopped
        or stopped calling back, e.g. because the device was unplugged."""
        now = time.monotonic()
        lost = []
        for name in ("input", "output"):
            stream = getattr(self, f"{name}_stream")
            if stream is None:
                continue
            last = self._last_callback.get(name, self._started_at)
            if not stream.active or now - last > STREAM_TIMEOUT_S:
                lost.append(name)
        return lost

    def stop_all(self):
        with self._lock:
            self._active_sounds = []
            self._active_sounds_monitor = []

    def set_monitor_muted(self, muted):
        with self._lock:
            self.monitor_muted = muted
            if muted:
                self._active_sounds_monitor = []

    def _on_input(self, indata, frames, time_info, status):
        self._last_callback["input"] = time.monotonic()
        with self._lock:
            if status:
                self.dropouts += 1
            self._mic_buffer.append(indata.copy())
            self._mic_frames += len(indata)
            while self._mic_frames > MIC_MAX_FRAMES:
                self._mic_frames -= len(self._mic_buffer.popleft())

    def _on_output(self, outdata, frames, time_info, status):
        self._last_callback["output"] = time.monotonic()
        with self._lock:
            if status:
                self.dropouts += 1
            mixed = self._pull_mic_frames(frames, self.output_channels)
            mixed *= self.mic_gain
            mixed += self._mix_sounds("_active_sounds", frames, self.output_channels)
        outdata[:] = self._output_limiter.process(mixed)

    def _on_monitor_output(self, outdata, frames, time_info, status):
        with self._lock:
            if status:
                self.dropouts += 1
            mixed = self._mix_sounds("_active_sounds_monitor", frames, self.monitor_channels)
        outdata[:] = self._monitor_limiter.process(mixed)

    def _mix_sounds(self, attr, frames, channels):
        """Sum one block from each clip in the named list, drop finished
        clips, and apply the soundboard volume. Caller holds the lock."""
        mixed = np.zeros((frames, channels), dtype=np.float32)
        still_active = []
        for sound in getattr(self, attr):
            chunk, finished = sound.read(frames)
            mixed += chunk
            if not finished:
                still_active.append(sound)
        setattr(self, attr, still_active)
        mixed *= self.sound_gain
        return mixed

    def _pull_mic_frames(self, frames, channels):
        out = np.zeros((frames, channels), dtype=np.float32)
        if not self._mic_ready:
            if self._mic_frames < MIC_TARGET_FRAMES:
                return out
            self._mic_ready = True

        # The mic and output run on separate clocks. Nudge mic playback
        # speed by ~0.2% to keep the cushion near its target instead of
        # letting drift empty it (gaps) or overfill it (skips).
        step = max(1, frames // 500)
        need = frames
        if self._mic_frames < MIC_TARGET_FRAMES:
            need -= step
        elif self._mic_frames > MIC_TARGET_FRAMES * 2:
            need += step

        raw = self._take_mic(need)
        if len(raw) < need:
            self._mic_ready = False  # ran dry; rebuild the cushion first
            out[:len(raw)] = _match_channels(raw, channels)
            return out
        return _match_channels(_resample(raw, need, frames), channels)

    def _take_mic(self, count):
        parts, taken = [], 0
        while taken < count and self._mic_buffer:
            chunk = self._mic_buffer[0]
            take = min(count - taken, len(chunk))
            parts.append(chunk[:take])
            if take < len(chunk):
                self._mic_buffer[0] = chunk[take:]
            else:
                self._mic_buffer.popleft()
            taken += take
        self._mic_frames -= taken
        if not parts:
            return np.zeros((0, self.input_channels), dtype=np.float32)
        return np.concatenate(parts)

    def play_data(self, data, samplerate, key=None, gain=1.0):
        """Queue audio for playback. A clip with the same key that is
        still playing is stopped first, so re-triggering restarts it."""
        if self.output_stream is None and self.monitor_stream is None:
            raise RuntimeError("No output device selected.")
        self._queue_clip(_resample(data, samplerate, SAMPLE_RATE), key, gain)

    def _queue_clip(self, resampled, key, gain):
        main_data = _match_channels(resampled, self.output_channels)
        monitor_data = _match_channels(resampled, self.monitor_channels)
        with self._lock:
            if key is not None:
                self._active_sounds = [s for s in self._active_sounds if s.key != key]
                self._active_sounds_monitor = [s for s in self._active_sounds_monitor if s.key != key]
            if self.output_stream is not None:
                self._active_sounds.append(_ActiveSound(main_data, key, gain))
            if self.monitor_stream is not None and not self.monitor_muted:
                self._active_sounds_monitor.append(_ActiveSound(monitor_data, key, gain))

    def play(self, path, gain=1.0):
        """Queue a file for playback without blocking the caller. Errors
        are reported through on_error."""
        self._requests.put((path, gain, True))

    def preload(self, paths):
        """Decode files into the cache in the background."""
        for path in paths:
            self._requests.put((path, 1.0, False))

    def _worker(self):
        while True:
            path, gain, play = self._requests.get()
            try:
                if play and self.output_stream is None and self.monitor_stream is None:
                    raise RuntimeError("No output device selected.")
                data = self._load_clip(path)
                if play:
                    self._queue_clip(data, os.path.abspath(path), gain)
            except FileNotFoundError:
                if play and self.on_error is not None:
                    self.on_error(f"File not found: {path}")
            except Exception as e:
                if play and self.on_error is not None:
                    self.on_error(f"{os.path.basename(path)}: {e}")

    def _load_clip(self, path):
        """Return the file resampled to SAMPLE_RATE, from the cache when
        the file is unchanged."""
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            data = self._clip_cache.get(key)
            if data is not None:
                self._clip_cache.move_to_end(key)
                return data
        raw, samplerate = sf.read(path, dtype="float32", always_2d=True)
        data = _resample(raw, samplerate, SAMPLE_RATE)
        if data.nbytes > CLIP_CACHE_BYTES:
            return data
        with self._cache_lock:
            for old_key in [k for k in self._clip_cache if k[0] == key[0]]:
                self._clip_cache_bytes -= self._clip_cache.pop(old_key).nbytes
            self._clip_cache[key] = data
            self._clip_cache_bytes += data.nbytes
            while self._clip_cache_bytes > CLIP_CACHE_BYTES:
                _, old = self._clip_cache.popitem(last=False)
                self._clip_cache_bytes -= old.nbytes
        return data


class Soundboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Soundboard")
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        ensure_sounds_dir()

        try:
            self.config = load_config()
        except (ValueError, OSError) as e:
            backup = unique_path(CONFIG_PATH + ".broken")
            os.replace(CONFIG_PATH, backup)
            messagebox.showwarning(
                "Settings reset",
                f"Your settings file could not be read ({e}), so Soundboard "
                f"started with default settings.\n\nThe old file was kept as:\n{backup}",
            )
            self.config = load_config()
        is_first_run = not os.path.exists(CONFIG_PATH)

        self.audio_engine = AudioEngine()
        self.audio_engine.on_error = self._on_playback_error
        self.hotkey_listener = None
        self._pending_save = None
        self._output_was_up = False
        self._output_down_since = None
        self._next_reconnect = 0.0

        self.editor_path = None
        self.editor_data = None
        self.editor_samplerate = None
        self._editor_sound_paths = {}

        self.hostapi = self._pick_hostapi(self.config.get("host_api"))
        self.input_devices = self._list_devices(output=False)
        self.output_devices = self._list_devices(output=True)

        # Devices are stored by name; indices shift when devices are added.
        # Older configs stored indices, so those get re-picked too.
        for key, devices, output in (
            ("input_device", self.input_devices, False),
            ("output_device", self.output_devices, True),
        ):
            stored = self.config.get(key)
            if is_first_run or isinstance(stored, int):
                self.config[key] = self._default_device_name(devices, output)
            else:
                # Keep an unplugged device's name so it's picked up again later.
                self.config[key] = self._match_device_name(devices, stored) or stored
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
        # Holder stays packed so the warning can appear above the sound list.
        warning_holder = tk.Frame(board_tab, bg=COLOR_BG, height=1)
        warning_holder.pack(fill="x")
        self.loop_warning = ctk.CTkLabel(
            warning_holder, text="", text_color=COLOR_ERROR, justify="left", anchor="w", wraplength=800,
        )
        self._build_sound_list(board_tab)
        self._build_controls(board_tab)

        self._build_download_tab(download_tab)
        self._build_editor_tab(editor_tab)

        self._restart_audio_engine()
        self._apply_hotkeys()
        self.audio_engine.preload(
            resolve_sound_path(s["path"]) for s in self.config["sounds"] if s.get("enabled", True)
        )

    # -- device listing -----------------------------------------------------

    @staticmethod
    def _pick_hostapi(preferred_name=None):
        """Index of the host API whose devices are shown, or None to show
        all (non-Windows platforms have one relevant API)."""
        if sys.platform != "win32":
            return None
        names = [api["name"] for api in sd.query_hostapis()]
        for wanted in (preferred_name,) + WINDOWS_HOSTAPIS:
            if wanted in names:
                return names.index(wanted)
        return None

    def _list_devices(self, output):
        key = "max_output_channels" if output else "max_input_channels"
        entries = [(None, NO_DEVICE_LABEL)]
        for i, d in enumerate(sd.query_devices()):
            if d[key] > 0 and (self.hostapi is None or d["hostapi"] == self.hostapi):
                entries.append((i, d["name"]))
        return entries

    def _system_default_device(self, output):
        try:
            if self.hostapi is not None:
                key = "default_output_device" if output else "default_input_device"
                index = sd.query_hostapis(self.hostapi)[key]
            else:
                index = sd.query_devices(kind="output" if output else "input")["index"]
        except Exception:
            return None
        return index if index is not None and index >= 0 else None

    def _default_device_name(self, devices, output):
        """Mic defaults to the system input. Output prefers a known virtual
        cable, then any device other than the system output."""
        names = [name for _, name in devices[1:]]
        if not names:
            return None
        default_index = self._system_default_device(output)
        default_name = next((n for i, n in devices if i is not None and i == default_index), None)
        if not output:
            return default_name or names[0]
        for name in names:
            if any(hint in name.lower() for hint in VIRTUAL_CABLE_HINTS):
                return name
        others = [n for n in names if n != default_name]
        return others[0] if others else names[0]

    @staticmethod
    def _match_device_name(devices, stored):
        """Find a stored device name in the list. Also matches across
        Windows APIs, where MME cuts names to 31 characters."""
        if stored is None:
            return None
        names = [n for _, n in devices[1:]]
        if stored in names:
            return stored
        for name in names:
            if len(stored) == MME_NAME_LENGTH and name.startswith(stored):
                return name
            if len(name) == MME_NAME_LENGTH and stored.startswith(name):
                return name
        return None

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
        current_input = self.config.get("input_device")
        self.input_var = tk.StringVar(value=current_input or NO_DEVICE_LABEL)
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
        ctk.CTkButton(
            frame, text="Refresh devices", command=self.refresh_devices,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).grid(row=0, column=2, sticky="w", padx=8, pady=8)

        ctk.CTkLabel(frame, text="Virtual mic output:", text_color=COLOR_TEXT).grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        output_names = [name for _, name in self.output_devices]
        current_output = self.config.get("output_device")
        self.output_var = tk.StringVar(value=current_output or NO_DEVICE_LABEL)
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

        self._add_volume_row(frame, 2, "Mic volume:", "mic_volume", "mic_gain")
        self._add_volume_row(frame, 3, "Soundboard volume:", "sound_volume", "sound_gain")

    def _add_volume_row(self, frame, row, text, config_key, engine_attr):
        ctk.CTkLabel(frame, text=text, text_color=COLOR_TEXT).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        value_label = ctk.CTkLabel(frame, text=f"{self.config[config_key]}%", text_color=COLOR_TEXT, width=50)

        def on_change(value):
            percent = int(round(value))
            self.config[config_key] = percent
            setattr(self.audio_engine, engine_attr, percent / 100)
            value_label.configure(text=f"{percent}%")
            self._save_config_soon()

        slider = ctk.CTkSlider(
            frame, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, command=on_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        slider.set(self.config[config_key])
        slider.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        value_label.grid(row=row, column=2, sticky="w", padx=8, pady=6)

    def _save_config_soon(self):
        # Sliders fire continuously while dragged; write once they settle.
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
        self._pending_save = self.root.after(500, self._flush_config)

    def _flush_config(self):
        self._pending_save = None
        save_config(self.config)

    def _refresh_device_menus(self):
        for menu, var, devices, key in (
            (self.input_menu, self.input_var, self.input_devices, "input_device"),
            (self.output_menu, self.output_var, self.output_devices, "output_device"),
        ):
            names = [name for _, name in devices]
            menu.configure(values=names)
            var.set(self.config.get(key) or NO_DEVICE_LABEL)

    def _on_input_device_change(self, selected_name):
        self.config["input_device"] = None if selected_name == NO_DEVICE_LABEL else selected_name
        save_config(self.config)
        self._restart_audio_engine()

    def _on_output_device_change(self, selected_name):
        self.config["output_device"] = None if selected_name == NO_DEVICE_LABEL else selected_name
        save_config(self.config)
        self._restart_audio_engine()

    def _on_toggle_hear_self(self):
        hear = bool(self.hear_self_checkbox.get())
        self.config["hear_self"] = hear
        save_config(self.config)
        self.audio_engine.set_monitor_muted(not hear)

    def _start_audio_engine(self):
        # Local copy goes to the system default output, unless that is
        # already the selected output (it would play twice).
        input_device = self._index_for_name(self.input_devices, self.config.get("input_device"))
        output_device = self._index_for_name(self.output_devices, self.config.get("output_device"))
        monitor_device = self._system_default_device(output=True)
        if monitor_device == output_device:
            monitor_device = None
        self.audio_engine.mic_gain = self.config["mic_volume"] / 100
        self.audio_engine.sound_gain = self.config["sound_volume"] / 100
        self.audio_engine.start(input_device, output_device, monitor_device)
        self.audio_engine.set_monitor_muted(not self.config.get("hear_self", True))
        self._update_device_warnings()

    def _update_device_warnings(self):
        """Show disconnected devices, and setups that send PC audio (e.g.
        other people's voices from Discord) into the virtual mic, so they
        hear themselves."""
        input_device = self._index_for_name(self.input_devices, self.config.get("input_device"))
        output_device = self._index_for_name(self.output_devices, self.config.get("output_device"))
        warnings = []
        lost = self.audio_engine.lost_streams()
        for key, label, index in (("input_device", "Microphone", input_device), ("output_device", "Virtual mic output", output_device)):
            name = self.config.get(key)
            if name and index is None:
                warnings.append(f"{label} \"{name}\" isn't connected. Plug it in and click Refresh devices, or pick another device.")
            elif key.split("_")[0] in lost:
                warnings.append(f"{label} \"{name}\" stopped responding. Click Refresh devices.")
        if self._output_down_since is not None and time.monotonic() - self._output_down_since <= RECONNECT_WINDOW_S:
            warnings.append("Trying to reconnect the virtual mic output...")
        input_name = self.config.get("input_device") or ""
        if input_device is not None and any(h in input_name.lower() for h in LOOPBACK_INPUT_HINTS):
            warnings.append(
                f"Microphone is set to \"{input_name}\", which carries PC audio, not your voice. "
                "Pick your real microphone, or others will hear their own voices back."
            )
        default_output = self._system_default_device(output=True)
        if output_device is not None and default_output is not None:
            default_name = sd.query_devices(default_output)["name"]
            if any(h in default_name.lower() for h in VIRTUAL_CABLE_HINTS):
                warnings.append(
                    f"Your system's default playback device is \"{default_name}\", so apps like "
                    "Discord play other people's voices into your virtual mic and they hear "
                    "themselves. Set your speakers/headphones as the default playback device, "
                    "and in Discord set Output Device to them too."
                )
        label = getattr(self, "loop_warning", None)
        if label is None:
            return
        text = "\n".join(warnings)
        if label.cget("text") == text:
            return
        label.configure(text=text)
        if warnings:
            label.pack(fill="x", padx=8, pady=(4, 0))
        else:
            label.pack_forget()

    def refresh_devices(self, show_errors=True):
        """Re-read the system's device list (PortAudio only scans it at
        startup) and reopen the selected devices."""
        self.audio_engine.stop()
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            if show_errors:
                messagebox.showerror("Audio device error", f"Could not rescan audio devices: {e}")
            return
        self.hostapi = self._pick_hostapi(self.config.get("host_api"))
        self.input_devices = self._list_devices(output=False)
        self.output_devices = self._list_devices(output=True)
        self._refresh_device_menus()
        self._restart_audio_engine(show_errors)

    def _restart_audio_engine(self, show_errors=True):
        try:
            self._start_audio_engine()
            return
        except Exception as e:
            error = e
        if self._fallback_to_mme():
            try:
                self._start_audio_engine()
                save_config(self.config)
                return
            except Exception as e:
                error = e
        if show_errors:
            messagebox.showerror("Audio device error", str(error))
        self._update_device_warnings()

    def _fallback_to_mme(self):
        """If a WASAPI device failed to open, switch the device lists to
        MME (older, but accepts any sample rate) and remember that."""
        if self.hostapi is None:
            return False
        names = [api["name"] for api in sd.query_hostapis()]
        if names[self.hostapi] != "Windows WASAPI" or "MME" not in names:
            return False
        self.hostapi = names.index("MME")
        self.config["host_api"] = "MME"
        self.input_devices = self._list_devices(output=False)
        self.output_devices = self._list_devices(output=True)
        for key, devices in (("input_device", self.input_devices), ("output_device", self.output_devices)):
            stored = self.config.get(key)
            self.config[key] = self._match_device_name(devices, stored) or stored
        self._refresh_device_menus()
        return True

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
            name = sound["name"]
            if len(name) > 40:  # long titles would push the buttons out of the row
                name = name[:37] + "..."
            label_text = f"{name}  [{sound.get('hotkey') or 'no hotkey'}]"
            if missing:
                label_text += "  (file missing)"
            ctk.CTkLabel(
                row,
                text=label_text,
                anchor="w",
                text_color=COLOR_ERROR if missing else COLOR_TEXT,
            ).pack(side="left", fill="x", expand=True, padx=4)

            volume_label = ctk.CTkLabel(row, text=f"{sound['volume']}%", width=42, text_color=COLOR_TEXT_DIM)
            volume_slider = ctk.CTkSlider(
                row, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, width=100,
                command=lambda value, s=sound, lbl=volume_label: self._on_sound_volume(s, lbl, value),
                fg_color=COLOR_SURFACE, progress_color=COLOR_ORANGE,
                button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
            )
            volume_slider.set(sound["volume"])
            volume_slider.pack(side="left", padx=(3, 0))
            volume_label.pack(side="left", padx=(0, 3))

            ctk.CTkButton(
                row, text="Play", width=60,
                command=lambda s=sound: self.play_sound(s),
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

    def _on_sound_volume(self, sound, label, value):
        sound["volume"] = int(round(value))
        label.configure(text=f"{sound['volume']}%")
        self._save_config_soon()

    def _build_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        frame.pack(fill="x", padx=4, pady=(6, 4))
        ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left")
        ctk.CTkButton(
            frame, text="Stop all", command=self.audio_engine.stop_all,
            fg_color=COLOR_ERROR, hover_color="#cc4444", text_color=COLOR_BG,
        ).pack(side="left", padx=(8, 0))
        self.stop_hotkey_button = ctk.CTkButton(
            frame, text="", command=self.set_stop_hotkey,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        self.stop_hotkey_button.pack(side="left", padx=(8, 0))
        self._update_stop_hotkey_button()
        self.dropout_label = ctk.CTkLabel(frame, text="", text_color=COLOR_TEXT_DIM)
        self.dropout_label.pack(side="right")
        self._update_dropout_label()

    def _update_dropout_label(self):
        count = self.audio_engine.dropouts
        self.dropout_label.configure(text=f"Audio dropouts: {count}" if count else "")
        self._watch_output()
        self._update_device_warnings()
        self.root.after(1000, self._update_dropout_label)

    def _watch_output(self):
        """Reconnect automatically when the virtual mic output is lost
        mid-session. Rescanning restarts every stream, which is harmless
        while the output is down anyway, so it's only done then."""
        engine = self.audio_engine
        down = bool(self.config.get("output_device")) and (
            engine.output_stream is None or "output" in engine.lost_streams()
        )
        now = time.monotonic()
        if not down:
            self._output_down_since = None
            self._output_was_up = engine.output_stream is not None
            return
        if self._output_down_since is None:
            if not self._output_was_up:
                return
            self._output_was_up = False
            self._output_down_since = now
            self._next_reconnect = now
        if now >= self._next_reconnect and now - self._output_down_since <= RECONNECT_WINDOW_S:
            self._next_reconnect = now + RECONNECT_INTERVAL_S
            self.refresh_devices(show_errors=False)

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
        self._add_sound_entry(name, stored_path)

    def _add_sound_entry(self, name, stored_path):
        self.config["sounds"].append(
            {"name": name, "path": stored_path, "hotkey": None, "enabled": True, "volume": 100}
        )
        save_config(self.config)
        self._refresh_sound_list()

    def remove_sound(self, index):
        del self.config["sounds"][index]
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    def set_hotkey(self, index):
        sound = self.config["sounds"][index]
        hotkey = self._ask_hotkey("Set hotkey", sound.get("hotkey"), owner=sound)
        if hotkey is None:
            return
        sound["hotkey"] = hotkey or None
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    def set_stop_hotkey(self):
        hotkey = self._ask_hotkey("Stop all hotkey", self.config.get("stop_hotkey"), owner="stop")
        if hotkey is None:
            return
        self.config["stop_hotkey"] = hotkey or None
        save_config(self.config)
        self._update_stop_hotkey_button()
        self._apply_hotkeys()

    def _update_stop_hotkey_button(self):
        hotkey = self.config.get("stop_hotkey")
        self.stop_hotkey_button.configure(text=f"Stop hotkey: {hotkey}" if hotkey else "Set stop hotkey")

    def _ask_hotkey(self, title, current, owner):
        """Returns the new hotkey, "" to clear it, or None if cancelled or
        invalid. `owner` is the sound dict or "stop" being edited."""
        dialog = ctk.CTkInputDialog(
            text=f"Enter hotkey (e.g. <ctrl>+<alt>+1), leave blank to clear.\nCurrent: {current or 'none'}",
            title=title,
        )
        hotkey = dialog.get_input()
        if hotkey is None:
            return None
        hotkey = hotkey.strip()
        if not hotkey:
            return ""
        if not self._is_valid_hotkey(hotkey):
            messagebox.showerror(
                "Invalid hotkey",
                f"'{hotkey}' is not a valid hotkey. Use a format like <ctrl>+<alt>+1.",
            )
            return None
        other = self._hotkey_owner(hotkey, skip=owner)
        if other is not None:
            messagebox.showerror("Hotkey in use", f"'{hotkey}' is already used by {other}.")
            return None
        return hotkey

    def _hotkey_owner(self, hotkey, skip):
        keys = self._hotkey_keys(hotkey)
        candidates = [("stop", "Stop all", self.config.get("stop_hotkey"))]
        candidates += [(s, f"'{s['name']}'", s.get("hotkey")) for s in self.config["sounds"]]
        for obj, label, other in candidates:
            if obj is not skip and other and self._hotkey_keys(other) == keys:
                return label
        return None

    @staticmethod
    def _hotkey_keys(hotkey):
        try:
            return frozenset(pynkeyboard.HotKey.parse(hotkey))
        except ValueError:
            return None

    @staticmethod
    def _is_valid_hotkey(hotkey):
        try:
            pynkeyboard.GlobalHotKeys({hotkey: lambda: None})
        except ValueError:
            return False
        return True

    # -- playback -----------------------------------------------------------

    def play_sound(self, sound):
        path = resolve_sound_path(sound["path"])
        self.audio_engine.play(path, gain=sound.get("volume", 100) / 100)

    def _on_playback_error(self, message):
        # Called from the audio worker thread.
        self.root.after(0, lambda: messagebox.showerror("Playback error", message))

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
                mapping[hotkey] = (lambda s=sound: self.play_sound(s))
        stop_hotkey = self.config.get("stop_hotkey")
        if stop_hotkey and self._is_valid_hotkey(stop_hotkey):
            mapping[stop_hotkey] = self.audio_engine.stop_all

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
        self._add_sound_entry(title, os.path.basename(path))

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
        self._add_sound_entry(name, os.path.basename(dest))
        messagebox.showinfo("Saved", f"Saved and added to your board as '{name}'.")

    # -- lifecycle ------------------------------------------------------

    def _on_close(self):
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
            self._flush_config()
        self.audio_engine.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self.root.destroy()


def _report_callback_exception(exc_type, exc_value, exc_tb):
    """Windowed builds have no console, so show UI errors instead of
    losing them."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log_path = write_error_log(text)
    details = f"\n\nDetails were saved to:\n{log_path}" if log_path else ""
    messagebox.showerror("Unexpected error", f"{exc_value}{details}")


def _show_startup_error(root):
    text = traceback.format_exc()
    log_path = write_error_log(text)
    error = sys.exc_info()[1]
    message = f"Soundboard could not start:\n\n{error}"
    if isinstance(error, PermissionError):
        message += (
            "\n\nSoundboard saves its settings and sounds next to the app. "
            "Move it to a folder you can write to, such as Documents."
        )
    if log_path:
        message += f"\n\nDetails were saved to:\n{log_path}"
    try:
        if root is None:
            root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Soundboard", message, parent=root)
        root.destroy()
    except Exception:
        pass


def _fit_to_screen(root):
    try:
        if sys.platform.startswith("linux"):
            root.attributes("-zoomed", True)
        else:
            root.state("zoomed")
    except tk.TclError:
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")


def main():
    root = None
    try:
        if sd is None:
            raise RuntimeError(
                f"The PortAudio library is missing ({PORTAUDIO_ERROR}).\n\n"
                "Install it and start Soundboard again:\n"
                "Debian/Ubuntu: sudo apt install libportaudio2\n"
                "Fedora: sudo dnf install portaudio\n"
                "Arch: sudo pacman -S portaudio"
            )
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        root = ctk.CTk()
        root.report_callback_exception = _report_callback_exception
        root.geometry("900x700")
        root.minsize(640, 480)
        try:
            root.iconphoto(True, tk.PhotoImage(file=ICON_PATH))
        except tk.TclError:
            pass
        Soundboard(root)
        # CustomTkinter applies its own geometry after startup, so maximize afterwards.
        root.after(100, lambda: _fit_to_screen(root))
    except Exception:
        _show_startup_error(root)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
