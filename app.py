"""The main window: builds the tabs and holds the state the UI mixins share."""

import os
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from audio_engine import AudioEngine
from config import (
    CONFIG_PATH,
    ensure_sounds_dir,
    load_config,
    resolve_sound_path,
    save_config,
    unique_path,
)
from devices import DeviceMixin
from download_tab import DownloadMixin
from editor_tab import EditorMixin
from mic_hotkeys import MicHotkeyMixin
from sound_list import SoundListMixin
from theme import COLOR_BG, COLOR_ERROR, COLOR_ORANGE, COLOR_ORANGE_HOVER, COLOR_ROW, COLOR_SURFACE, COLOR_TEXT


class Soundboard(DeviceMixin, MicHotkeyMixin, SoundListMixin, DownloadMixin, EditorMixin):
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
        self._editor_label = None
        self.editor_samplerate = None
        self._editor_sound_paths = {}
        self._editor_undo = []
        self._editor_committed = None
        self._editor_commit = None
        self._playhead_poll = None

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
        self._build_permission_warning(warning_holder)
        self._build_sound_list(board_tab)
        self._build_controls(board_tab)

        self._build_download_tab(download_tab)
        self._build_editor_tab(editor_tab)

        self._restart_audio_engine()
        self._apply_hotkeys()
        self.audio_engine.preload(
            resolve_sound_path(s["path"]) for s in self.config["sounds"] if s.get("enabled", True)
        )

    def _save_config_soon(self):
        # Sliders fire continuously while dragged; write once they settle.
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
        self._pending_save = self.root.after(500, self._flush_config)

    def _flush_config(self):
        self._pending_save = None
        save_config(self.config)

    def _on_close(self):
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
            self._flush_config()
        self.audio_engine.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self.root.destroy()
