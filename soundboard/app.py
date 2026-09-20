"""The main window: builds the tabs and holds the state the UI mixins share."""

import os
import threading
import tkinter as tk

import customtkinter as ctk

from .audio_engine import AudioEngine, scipy_signal
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
from .dialogs import ReportDialog, UpdateDialog, handle_exception, warn
from .download_tab import DownloadMixin
from .editor_tab import EditorMixin
from .mic_hotkeys import MicHotkeyMixin
from .remote import RemoteMixin
from .share_tab import ShareMixin
from .tts_tab import SpeechMixin
from .sound_list import SoundListMixin
from .theme import (
    COLOR_BG,
    COLOR_BORDER,
    COLOR_ERROR_TEXT,
    COLOR_ON_ACCENT,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    CONTROL_H,
    GAP,
    PAD,
    apply_theme,
    font,
    register_tk,
    set_appearance,
    wrap_to_width,
)
from . import backups, updater
from .tray import TrayMixin
from .tutorial import TutorialWindow

# Words, not a chevron: the tab strip and the buttons around this one are
# text, and a glyph here would be the only one in the window.
SETTINGS_SHOW = "Settings"


class Soundboard(DeviceMixin, MicHotkeyMixin, SoundListMixin, DownloadMixin, EditorMixin,
                 ShareMixin, SpeechMixin, TrayMixin, RemoteMixin):
    def __init__(self, root):
        self.root = root
        self.root.title("Soundboard")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        ensure_sounds_dir()

        try:
            self.config = load_config()
        except (ValueError, OSError) as e:
            backup = unique_path(CONFIG_PATH + ".broken")
            os.replace(CONFIG_PATH, backup)
            warn(self.root, 
                "Settings reset",
                f"Your settings file could not be read ({e}), so Soundboard "
                f"started with default settings.\n\nThe old file was kept as:\n{backup}",
            )
            self.config = load_config()
        is_first_run = not os.path.exists(CONFIG_PATH)
        # A copy of the settings file as it was when the app started,
        # before anything here can write to it. Once a day and kept for a
        # week: a board exists only inside this one file, and a profile
        # deleted by accident cannot be got back any other way.
        backups.backup_daily()

        # The look has to be in place before the first widget is built:
        # CustomTkinter reads its defaults at widget construction, so a
        # theme applied later would only reach whatever came after it.
        apply_theme(self.config.get("appearance", "system"))
        self.root.configure(fg_color=COLOR_BG)

        self.audio_engine = AudioEngine()
        self.audio_engine.on_error = self._on_playback_error
        self.audio_engine.on_finished = self._on_clip_finished
        self._transport_key = None  # the entry Next and Previous step from
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
            fg_color=COLOR_BG,
            border_width=0,
            segmented_button_fg_color=COLOR_ROW,
            segmented_button_selected_color=COLOR_ORANGE,
            segmented_button_selected_hover_color=COLOR_ORANGE_HOVER,
            segmented_button_unselected_color=COLOR_ROW,
            segmented_button_unselected_hover_color=COLOR_ROW_HOVER,
            text_color=COLOR_TEXT,
            anchor="w",
        )
        self.tabview.pack(fill="both", expand=True, padx=PAD, pady=(GAP, PAD))
        self.tabview._segmented_button.configure(font=font("body_bold"))

        # The ask was the window's own title bar, which Tk cannot reach: it
        # is the non-client area, and the only way in is to drop the native
        # one and rebuild snap, tiling and the caption buttons by hand. The
        # tab strip's row is the next row down, and is where this goes.
        self.help_button = ctk.CTkButton(
            self.root, text="?", width=CONTROL_H, height=CONTROL_H,
            command=self.open_tutorial,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
            font=font("body_bold"),
        )
        self.help_button.place(in_=self.tabview, relx=1.0, x=-8, y=6, anchor="ne")
        self._tutorial = None

        # Same row as the ? - it is where anything global lives. Hidden
        # until a check finds something, so it is never a dead control.
        self.update_button = ctk.CTkButton(
            self.root, text="", height=CONTROL_H, command=self.open_update,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_ON_ACCENT, font=font("body_bold"),
        )
        self._update_release = None
        self._update_dialog = None
        self._start_update_check()

        board_tab = self.tabview.add("Soundboard")
        download_tab = self.tabview.add("Download")
        editor_tab = self.tabview.add("Sound Editor")
        tts_tab = self.tabview.add("Speak")
        share_tab = self.tabview.add("Import / Export")
        self.settings_tab = self.tabview.add("Settings")
        for tab in (board_tab, download_tab, editor_tab, tts_tab,
                    share_tab, self.settings_tab):
            tab.configure(fg_color=COLOR_BG)
        self.tabview.configure(command=self._on_tab_change)

        # Before the settings panel, so its switch shows what is really
        # listening rather than what the config asked for: a port
        # something else already has switches the setting back off.
        remote_problem = self._start_remote()

        self._build_settings(board_tab)
        # Holder stays packed so the warning can appear above the sound
        # list, and it is what the settings panel opens above.
        warning_holder = tk.Frame(board_tab, height=1)
        register_tk(warning_holder, bg=COLOR_BG)
        warning_holder.pack(fill="x")
        self.warning_holder = warning_holder
        self.loop_warning = wrap_to_width(ctk.CTkLabel(
            warning_holder, text="", text_color=COLOR_ERROR_TEXT,
            justify="left", anchor="w",
        ))
        self._build_cable_warning(warning_holder)
        self._build_permission_warning(warning_holder)
        self._place_settings_panel()
        # The controls are packed before the list although they sit below
        # it: the list is the expanding child, and whatever is packed after
        # it gets whatever is left of the cavity, which was nothing. Pack
        # the fixed-height row against the bottom first and the list takes
        # the rest. The editor tab does the same thing for the same reason.
        self._build_controls(board_tab)
        # After the controls and before the list: both pack against the
        # bottom, so this lands between them.
        self._build_transport(board_tab)
        self._build_sound_list(board_tab)

        self._build_download_tab(download_tab)
        self._build_editor_tab(editor_tab)
        self._build_tts_tab(tts_tab)
        self._build_share_tab(share_tab)

        self._restart_audio_engine()
        self._poll_meters()
        self._apply_hotkeys()
        self.audio_engine.preload(
            resolve_sound_path(s["path"]) for s in self.sounds if s.get("enabled", True)
        )
        if self.config.get("match_levels"):
            # Anything that arrived without a measurement - a board built
            # by an older version, a file that would not decode last time
            # - would otherwise never be matched, since measuring only
            # happens when a sound is added.
            self.measure_board()
        if self.config["close_to_tray"]:
            self._start_tray()
        if remote_problem is not None:
            warn(self.root, "Remote control", remote_problem)
        self._warm_filters()

    def _warm_filters(self):
        """Load scipy on a worker thread now the window is up.

        It is half the app's import time and nothing draws the window
        with it, which is why it is no longer imported at startup - but
        the first editor effect would then stall on it, so it is fetched
        here instead, where nobody is waiting.
        """
        threading.Thread(target=scipy_signal, daemon=True).start()

    def _build_settings(self, board_tab):
        """The settings live in their own tab; this button on the board
        is the way to them from here.

        It used to open the same panel inline, above the sound list, and
        that cannot be made to work. The panel is about 620px tall and
        the default window leaves the Soundboard tab about 630, so there
        is no arrangement that shows both it and a board. Packed into the
        tab it took the height it asked for and pack answered by
        unmapping everything after it - the profile row, the search box
        and the whole list - which is what #150 was. A scrolling box
        around it does not help either: Tk clips a widget to its parent,
        and the panel's parent is the window, so it draws straight over
        the board whatever it is packed into.

        There is still one panel, not two: a Tk widget has a single
        parent and cannot be reparented, so a second copy would be a
        second set of meters, device menus and hotkey buttons, and only
        one of them would be kept up to date.
        """
        self.settings_button = ctk.CTkButton(
            board_tab, text=SETTINGS_SHOW, command=self.show_settings_tab,
            anchor="w", fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER,
            text_color=COLOR_TEXT, border_width=1, border_color=COLOR_BORDER,
        )
        self.settings_button.pack(fill="x", padx=2, pady=(2, 0))
        self.settings_panel = self._build_device_selectors(self.root)

    def show_settings_tab(self):
        """The board's Settings button, and the way back from anywhere
        else that wants to send someone to a setting."""
        self.tabview.set("Settings")
        # set() does not fire the tabview's own command, which is what
        # would otherwise finish the switch.
        self._on_tab_change()

    def _on_tab_change(self, *_):
        """Everything that has to happen when the open tab changes.

        The lift is not cosmetic. CustomTkinter reveals a tab by gridding
        it into the cell the old one already occupies and dropping the
        old one - at once when the tab strip is clicked, 100 ms later
        when set() is called - and Tk 9 on macOS does not repaint what
        that uncovers. The tab you left stays painted over the one you
        asked for: come back to the board and it is blank, correctly laid
        out and entirely undrawn, until a resize forces a redraw. Raising
        the tab draws it, which is the whole of what was missing.
        """
        self.tabview.tab(self.tabview.get()).lift()
        self._place_settings_panel()

    def _place_settings_panel(self, *_):
        """Show the panel while its tab is open, and nowhere otherwise.

        Still placed rather than simply built into the tab: the panel is
        parented to the window, because it was shown in two places once
        and the meters inside it must not be built twice.
        """
        panel = getattr(self, "settings_panel", None)
        if panel is None:
            return  # a tab change during startup, before the panel exists
        panel.pack_forget()
        if self.tabview.get() == "Settings":
            panel.pack(in_=self.settings_tab, fill="x", padx=2, pady=(2, GAP))

    def _build_appearance_controls(self, frame, row):
        """Light/dark, or follow the OS. "System" is the default and is
        what most people will leave it on; the other two are for a desktop
        whose theme the app cannot read, and for anyone who wants the app
        to disagree with it on purpose."""
        ctk.CTkLabel(
            frame, text="Appearance:", text_color=COLOR_TEXT_DIM, font=font("small_bold"),
        ).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        box = ctk.CTkFrame(frame, fg_color="transparent")
        box.grid(row=row, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        self.appearance_switch = ctk.CTkSegmentedButton(
            box, values=["System", "Light", "Dark"],
            command=self._on_appearance_change,
        )
        self.appearance_switch.set(self.config.get("appearance", "system").capitalize())
        self.appearance_switch.pack(side="left")

    def _on_appearance_change(self, choice):
        mode = choice.lower()
        self.config["appearance"] = set_appearance(mode)
        save_config(self.config)

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
        self.update_button.place(in_=self.tabview, relx=1.0, x=-(CONTROL_H + 16), y=6, anchor="ne")

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
        self._stop_remote()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        self._stop_tray()
        self.root.destroy()
