"""
Soundboard - continuously mixes microphone input with triggered sound
clips and writes the result to a chosen output device (e.g. a virtual
audio cable), so sounds and voice reach Discord/games together as one
microphone.

Config (devices + sound/hotkey mappings) is stored in soundboard_config.json,
created next to this script (or next to the executable, when built) on
first run.
"""

import collections
import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import sounddevice as sd
import soundfile as sf
from pynput import keyboard as pynkeyboard

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soundboard_config.json")

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK_SIZE = 1024
MIC_BUFFER_LIMIT = 50  # chunks; caps latency/memory if the output stalls

NO_DEVICE_LABEL = "(none)"

COLOR_BG = "#121212"
COLOR_SURFACE = "#1e1e1e"
COLOR_ROW = "#262626"
COLOR_ORANGE = "#ff8c00"
COLOR_ORANGE_HOVER = "#e67600"
COLOR_TEXT = "#f2f2f2"
COLOR_ERROR = "#ff5555"


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


class _ActiveSound:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def read(self, frames):
        end = min(self.position + frames, len(self.data))
        chunk = self.data[self.position:end]
        self.position = end
        finished = self.position >= len(self.data)
        if len(chunk) < frames:
            pad = np.zeros((frames - len(chunk), CHANNELS), dtype=np.float32)
            chunk = np.vstack([chunk, pad]) if len(chunk) else pad
        return chunk, finished


class AudioEngine:
    """Continuously mixes the selected microphone with triggered sound
    clips and writes the result to the selected output device, so voice
    and soundboard clips are heard together on the virtual mic."""

    def __init__(self):
        self._lock = threading.Lock()
        self.input_stream = None
        self.output_stream = None
        self._mic_buffer = collections.deque()
        self._active_sounds = []

    def start(self, input_device, output_device):
        self.stop()
        if input_device is not None:
            self.input_stream = sd.InputStream(
                device=input_device,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._on_input,
            )
            self.input_stream.start()
        if output_device is not None:
            self.output_stream = sd.OutputStream(
                device=output_device,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                dtype="float32",
                callback=self._on_output,
            )
            self.output_stream.start()

    def stop(self):
        for attr in ("input_stream", "output_stream"):
            stream = getattr(self, attr)
            if stream is not None:
                stream.stop()
                stream.close()
                setattr(self, attr, None)
        with self._lock:
            self._mic_buffer.clear()
            self._active_sounds = []

    def _on_input(self, indata, frames, time_info, status):
        with self._lock:
            self._mic_buffer.append(indata.copy())
            while len(self._mic_buffer) > MIC_BUFFER_LIMIT:
                self._mic_buffer.popleft()

    def _on_output(self, outdata, frames, time_info, status):
        with self._lock:
            mixed = self._pull_mic_frames(frames)
            still_active = []
            for sound in self._active_sounds:
                chunk, finished = sound.read(frames)
                mixed += chunk
                if not finished:
                    still_active.append(sound)
            self._active_sounds = still_active
        np.clip(mixed, -1.0, 1.0, out=mixed)
        outdata[:] = mixed

    def _pull_mic_frames(self, frames):
        out = np.zeros((frames, CHANNELS), dtype=np.float32)
        filled = 0
        while filled < frames and self._mic_buffer:
            chunk = self._mic_buffer[0]
            take = min(frames - filled, len(chunk))
            out[filled:filled + take] = chunk[:take]
            if take < len(chunk):
                self._mic_buffer[0] = chunk[take:]
            else:
                self._mic_buffer.popleft()
            filled += take
        return out

    def play(self, path):
        if self.output_stream is None:
            raise RuntimeError("No output device selected.")
        data, samplerate = sf.read(path, dtype="float32", always_2d=True)
        data = _resample(data, samplerate, SAMPLE_RATE)
        data = _match_channels(data, CHANNELS)
        with self._lock:
            self._active_sounds.append(_ActiveSound(data))


class Soundboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Soundboard")
        self.root.configure(fg_color=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        is_first_run = not os.path.exists(CONFIG_PATH)
        self.config = load_config()
        self.audio_engine = AudioEngine()
        self.hotkey_listener = None

        self.input_devices = self._list_devices(output=False)
        self.output_devices = self._list_devices(output=True)

        if is_first_run:
            if len(self.input_devices) > 1:
                self.config["input_device"] = self.input_devices[1][0]
            if len(self.output_devices) > 1:
                self.config["output_device"] = self.output_devices[1][0]
            save_config(self.config)

        self._build_device_selectors()
        self._build_sound_list()
        self._build_controls()

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

    def _build_device_selectors(self):
        frame = ctk.CTkFrame(self.root, fg_color=COLOR_SURFACE)
        frame.pack(fill="x", padx=12, pady=(12, 6))
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

    def _on_input_device_change(self, selected_name):
        self.config["input_device"] = self._index_for_name(self.input_devices, selected_name)
        save_config(self.config)
        self._restart_audio_engine()

    def _on_output_device_change(self, selected_name):
        self.config["output_device"] = self._index_for_name(self.output_devices, selected_name)
        save_config(self.config)
        self._restart_audio_engine()

    def _restart_audio_engine(self):
        try:
            self.audio_engine.start(self.config.get("input_device"), self.config.get("output_device"))
        except Exception as e:
            messagebox.showerror("Audio device error", str(e))

    # -- sound list -----------------------------------------------------

    def _build_sound_list(self):
        self.list_frame = ctk.CTkScrollableFrame(
            self.root,
            fg_color=COLOR_SURFACE,
            label_text="Sounds",
            label_text_color=COLOR_ORANGE,
        )
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=6)
        self._refresh_sound_list()

    def _refresh_sound_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        for idx, sound in enumerate(self.config["sounds"]):
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

            missing = not os.path.exists(sound["path"])
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
                command=lambda p=sound["path"]: self.play_sound(p),
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

    def _on_toggle_sound(self, index, checkbox):
        self.config["sounds"][index]["enabled"] = bool(checkbox.get())
        save_config(self.config)
        self._apply_hotkeys()

    def _build_controls(self):
        frame = ctk.CTkFrame(self.root, fg_color=COLOR_BG)
        frame.pack(fill="x", padx=12, pady=(6, 12))
        ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left")

    # -- sound management -------------------------------------------------

    def add_sound(self):
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.config["sounds"].append({"name": name, "path": path, "hotkey": None, "enabled": True})
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
                mapping[hotkey] = (lambda p=sound["path"]: self.play_sound(p))

        if mapping:
            try:
                self.hotkey_listener = pynkeyboard.GlobalHotKeys(mapping)
                self.hotkey_listener.start()
            except Exception as e:
                messagebox.showwarning("Hotkey error", f"Could not register hotkeys: {e}")

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
    root.geometry("580x560")
    root.minsize(480, 400)
    Soundboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
