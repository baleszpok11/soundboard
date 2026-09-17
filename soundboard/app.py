"""The main window: builds the tabs and holds the state the UI mixins share."""

import os
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from .audio_engine import AudioEngine
from .config import (
    CONFIG_PATH,
    active_profile,
    profile_sounds,
    ensure_sounds_dir,
    load_config,
    resolve_sound_path,
    save_config,
    unique_path,
)
from .audio_engine import sd
from .devices import DeviceMixin
from .dialogs import ReportDialog, UpdateDialog, handle_exception
from .download_tab import DownloadMixin
from .editor_tab import EditorMixin
from .mic_hotkeys import MicHotkeyMixin
from .share_tab import ShareMixin
from .sound_list import SoundListMixin
from .theme import COLOR_BG, COLOR_ERROR, COLOR_ORANGE, COLOR_ORANGE_HOVER, COLOR_ROW, COLOR_SURFACE, COLOR_TEXT
from . import updater
from .tray import TrayMixin
from .tutorial import TutorialWindow


class Soundboard(DeviceMixin, MicHotkeyMixin, SoundListMixin, DownloadMixin, EditorMixin,
                 ShareMixin, TrayMixin):
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
        self.tray_icon = None
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
        self._playing_poll = None
        self._meter_poll = None

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

        # From here on an error can be reported with the config and devices.
        self.root.report_callback_exception = self._on_ui_error

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

        # The ask was the window's own title bar, which Tk cannot reach: it
        # is the non-client area, and the only way in is to drop the native
        # one and rebuild snap, tiling and the caption buttons by hand. The
        # tab strip's row is the next row down, and is where this goes.
        self.help_button = ctk.CTkButton(
            self.root, text="?", width=28, height=28, command=self.open_tutorial,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.help_button.place(in_=self.tabview, relx=1.0, x=-10, y=6, anchor="ne")
        self._tutorial = None

        # Same row as the ? - it is where anything global lives. Hidden
        # until a check finds something, so it is never a dead control.
        self.update_button = ctk.CTkButton(
            self.root, text="", height=28, command=self.open_update,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        )
        self._update_release = None
        self._update_dialog = None
        self._start_update_check()

        board_tab = self.tabview.add("Soundboard")
        download_tab = self.tabview.add("Download")
        editor_tab = self.tabview.add("Sound Editor")
        share_tab = self.tabview.add("Import / Export")
        for tab in (board_tab, download_tab, editor_tab, share_tab):
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
        self._build_share_tab(share_tab)

        self._restart_audio_engine()
        self._poll_meters()
        self._apply_hotkeys()
        self.audio_engine.preload(
            resolve_sound_path(s["path"]) for s in self.sounds if s.get("enabled", True)
        )
        if self.config["close_to_tray"]:
            self._start_tray()

    @property
    def profile(self):
        """The profile whose board is on screen."""
        return active_profile(self.config)

    @property
    def sounds(self):
        """The active profile's sounds. Everything else in the config is
        shared across profiles."""
        return profile_sounds(self.config)

    def _start_update_check(self):
        """Ask GitHub what the latest release is, in the background. A
        failed or slow check costs nothing: the button simply never
        appears."""
        updater.clear_old_binary()
        if not self.config.get("check_for_updates", True):
            return
        updater.check_async(self._post_update_found)

    def _post_update_found(self, release):
        """Called on the check thread, where the window may already be
        gone: quitting inside the check timeout destroys the root."""
        try:
            self.root.after(0, self._on_update_found, release)
        except tk.TclError:
            pass

    def _on_update_found(self, release):
        if release is None or release.version == self.config.get("skipped_version"):
            return
        self._update_release = release
        self.update_button.configure(text=f"Update to {release.version}")
        self.update_button.place(in_=self.tabview, relx=1.0, x=-46, y=6, anchor="ne")

    def open_update(self):
        """One window, reused: a second dialog would download and install
        alongside the first, both replacing the same file."""
        if self._update_release is None:
            return
        if self._update_dialog is not None and self._update_dialog.winfo_exists():
            self._update_dialog.deiconify()
            self._update_dialog.lift()
            self._update_dialog.focus_force()
            return self._update_dialog
        self._update_dialog = UpdateDialog(
            self.root, self._update_release, config=self.config,
            on_skip=self._on_update_skipped,
        )
        return self._update_dialog

    def _on_update_skipped(self):
        save_config(self.config)
        self._update_release = None
        self.update_button.place_forget()

    def open_tutorial(self, page=None):
        """One window, reused: pressing ? again raises the open one rather
        than stacking another copy on top of it."""
        if self._tutorial is not None and self._tutorial.winfo_exists():
            self._tutorial.deiconify()
            self._tutorial.lift()
            self._tutorial.focus_force()
            return self._tutorial
        self._tutorial = TutorialWindow(self.root, page)
        return self._tutorial

    def _on_ui_error(self, exc_type, exc_value, exc_tb):
        handle_exception(self.root, exc_type, exc_value, exc_tb, self.config, self._host_api_name())

    def _host_api_name(self):
        if self.hostapi is None:
            return None
        try:
            return sd.query_hostapis(self.hostapi)["name"]
        except Exception:
            return None

    def report_bug(self):
        ReportDialog(self.root, config=self.config, host_api=self._host_api_name()).wait_window()

    def _save_config_soon(self):
        # Sliders fire continuously while dragged; write once they settle.
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
        self._pending_save = self.root.after(500, self._flush_config)

    def _flush_config(self):
        self._pending_save = None
        save_config(self.config)

    def _quit(self):
        if self._pending_save is not None:
            self.root.after_cancel(self._pending_save)
            self._flush_config()
        if self._playing_poll is not None:
            self.root.after_cancel(self._playing_poll)
            self._playing_poll = None
        if self._meter_poll is not None:
            self.root.after_cancel(self._meter_poll)
            self._meter_poll = None
        self.audio_engine.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self._stop_tray()
        self.root.destroy()
