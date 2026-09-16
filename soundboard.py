"""
Soundboard - plays audio files to a chosen output device (e.g. a virtual
audio cable) so sounds can be picked up as a microphone in Discord/games.

Config (device + sound/hotkey mappings) is stored in soundboard_config.json,
created next to this script on first run.
"""

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import sounddevice as sd
import soundfile as sf
from pynput import keyboard as pynkeyboard

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soundboard_config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {"device": None, "sounds": []}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


class Soundboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Soundboard")
        self.config = load_config()
        self.hotkey_listener = None

        self._build_device_selector()
        self._build_sound_list()
        self._build_controls()

        self._apply_hotkeys()

    # -- device selection --------------------------------------------------

    def _build_device_selector(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=8, pady=8)

        tk.Label(frame, text="Output device:").pack(side="left")

        devices = sd.query_devices()
        self.output_devices = [
            (i, d["name"]) for i, d in enumerate(devices) if d["max_output_channels"] > 0
        ]
        names = [name for _, name in self.output_devices]

        self.device_var = tk.StringVar()
        current_name = self._device_name_for_index(self.config.get("device"))
        self.device_var.set(current_name if current_name in names else (names[0] if names else ""))

        self.device_menu = tk.OptionMenu(frame, self.device_var, *names, command=self._on_device_change)
        self.device_menu.pack(side="left", fill="x", expand=True)

        if self.config.get("device") is None and self.output_devices:
            self.config["device"] = self.output_devices[0][0]
            save_config(self.config)

    def _device_name_for_index(self, index):
        for i, name in self.output_devices:
            if i == index:
                return name
        return None

    def _on_device_change(self, selected_name):
        for i, name in self.output_devices:
            if name == selected_name:
                self.config["device"] = i
                save_config(self.config)
                return

    # -- sound list -----------------------------------------------------

    def _build_sound_list(self):
        self.list_frame = tk.Frame(self.root)
        self.list_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self._refresh_sound_list()

    def _refresh_sound_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        for idx, sound in enumerate(self.config["sounds"]):
            row = tk.Frame(self.list_frame)
            row.pack(fill="x", pady=2)

            label = f"{sound['name']}  [{sound.get('hotkey') or 'no hotkey'}]"
            tk.Label(row, text=label, anchor="w").pack(side="left", fill="x", expand=True)

            tk.Button(row, text="Play", command=lambda p=sound["path"]: self.play_sound(p)).pack(side="left")
            tk.Button(row, text="Hotkey", command=lambda i=idx: self.set_hotkey(i)).pack(side="left")
            tk.Button(row, text="Remove", command=lambda i=idx: self.remove_sound(i)).pack(side="left")

    def _build_controls(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=8, pady=8)
        tk.Button(frame, text="Add sound", command=self.add_sound).pack(side="left")

    # -- sound management -------------------------------------------------

    def add_sound(self):
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self.config["sounds"].append({"name": name, "path": path, "hotkey": None})
        save_config(self.config)
        self._refresh_sound_list()

    def remove_sound(self, index):
        del self.config["sounds"][index]
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    def set_hotkey(self, index):
        current = self.config["sounds"][index].get("hotkey") or ""
        hotkey = simpledialog.askstring(
            "Set hotkey",
            "Enter hotkey (e.g. <ctrl>+<alt>+1), leave blank to clear:",
            initialvalue=current,
        )
        if hotkey is None:
            return
        hotkey = hotkey.strip()
        self.config["sounds"][index]["hotkey"] = hotkey or None
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    # -- playback -----------------------------------------------------------

    def play_sound(self, path):
        def _play():
            try:
                data, samplerate = sf.read(path, dtype="float32")
                device = self.config.get("device")
                sd.play(data, samplerate, device=device)
                sd.wait()
            except Exception as e:
                messagebox.showerror("Playback error", str(e))

        threading.Thread(target=_play, daemon=True).start()

    # -- hotkeys --------------------------------------------------------

    def _apply_hotkeys(self):
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
            self.hotkey_listener = None

        mapping = {}
        for sound in self.config["sounds"]:
            hotkey = sound.get("hotkey")
            if hotkey:
                mapping[hotkey] = (lambda p=sound["path"]: self.play_sound(p))

        if mapping:
            try:
                self.hotkey_listener = pynkeyboard.GlobalHotKeys(mapping)
                self.hotkey_listener.start()
            except Exception as e:
                messagebox.showwarning("Hotkey error", f"Could not register hotkeys: {e}")


def main():
    root = tk.Tk()
    root.geometry("480x400")
    Soundboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
