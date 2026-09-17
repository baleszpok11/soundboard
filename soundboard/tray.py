"""Tray icon and the startup options, so hotkeys keep working with the
window closed."""

import sys
import threading
from tkinter import messagebox

import customtkinter as ctk

from . import autostart
from .config import ICON_PATH, save_config
from .theme import COLOR_BG, COLOR_ORANGE, COLOR_ORANGE_HOVER, COLOR_SURFACE, COLOR_TEXT


class TrayMixin:
    """Expects `config`, `root` and `tray_icon` from Soundboard."""

    def _build_startup_controls(self, frame, row):
        ctk.CTkLabel(frame, text="Startup:", text_color=COLOR_TEXT).grid(
            row=row, column=0, sticky="w", padx=8, pady=6
        )
        box = ctk.CTkFrame(frame, fg_color=COLOR_SURFACE)
        box.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        checkbox = dict(
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_BG, text_color=COLOR_TEXT,
        )
        self.autostart_checkbox = ctk.CTkCheckBox(
            box, text="Start with the computer", command=self._on_toggle_autostart, **checkbox
        )
        self.autostart_checkbox.grid(row=0, column=0, sticky="w")
        self.tray_checkbox = ctk.CTkCheckBox(
            box, text="Keep running in the tray when closed",
            command=self._on_toggle_tray, **checkbox,
        )
        self.tray_checkbox.grid(row=0, column=1, sticky="w", padx=(12, 0))

        if autostart.SUPPORTED:
            self._refresh_autostart_checkbox()
        else:
            self.autostart_checkbox.configure(state="disabled")
        if self.config["close_to_tray"]:
            self.tray_checkbox.select()

    def _refresh_autostart_checkbox(self):
        if autostart.is_enabled():
            self.autostart_checkbox.select()
        else:
            self.autostart_checkbox.deselect()

    def _on_toggle_autostart(self):
        try:
            autostart.set_enabled(bool(self.autostart_checkbox.get()))
        except OSError as e:
            messagebox.showerror("Startup setting", f"Could not change the startup setting: {e}")
            self._refresh_autostart_checkbox()

    def _on_toggle_tray(self):
        enabled = bool(self.tray_checkbox.get())
        if enabled and not (self._start_tray() and self._confirm_tray()):
            self.tray_checkbox.deselect()
            enabled = False
        if not enabled:
            self._stop_tray()
        self.config["close_to_tray"] = enabled
        save_config(self.config)

    @staticmethod
    def _confirm_tray():
        """Desktops without a tray (GNOME without an extension, say) take
        the icon and show nothing, so ask instead of hiding the window
        somewhere the user can't get it back from."""
        return messagebox.askyesno(
            "Keep running in the tray",
            "Soundboard put an icon in your tray or menu bar.\n\n"
            "Can you see it? If you can't, leave this off: closing the window "
            "would hide Soundboard with no way back to it.",
        )

    # -- the icon -------------------------------------------------------

    def _start_tray(self):
        """True once an icon is running. Warns and returns False when the
        platform has no usable tray."""
        if self.tray_icon is not None:
            return True
        try:
            import pystray
            from PIL import Image

            icon = pystray.Icon(
                "soundboard",
                Image.open(ICON_PATH),
                "Soundboard",
                pystray.Menu(
                    pystray.MenuItem("Show Soundboard", self._on_tray_show, default=True),
                    pystray.MenuItem("Quit", self._on_tray_quit),
                ),
            )
            if sys.platform == "darwin":
                # macOS only runs a menu bar icon on the main thread, which
                # Tk already occupies, so hand it to Tk's event loop.
                icon.run_detached()
            else:
                threading.Thread(target=icon.run, daemon=True).start()
        except Exception as e:
            messagebox.showwarning(
                "Tray unavailable",
                f"Soundboard could not add itself to the tray: {e}\n\n"
                "Closing the window will quit as usual.",
            )
            return False
        self.tray_icon = icon
        return True

    def _stop_tray(self):
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None

    # Tray callbacks run on the icon's own thread.
    def _on_tray_show(self):
        self.root.after(0, self._show_window)

    def _on_tray_quit(self):
        self.root.after(0, self._quit)

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self):
        """Used by the launch-at-login entry, which passes --hidden."""
        if self.config["close_to_tray"] and self._start_tray():
            self.root.withdraw()
        else:
            self.root.iconify()  # no tray to hide in, so stay reachable

    def _on_close(self):
        if self.config["close_to_tray"] and self._start_tray():
            self.root.withdraw()
            return
        self._quit()
