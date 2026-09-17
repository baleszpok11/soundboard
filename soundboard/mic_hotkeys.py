"""Mic mute / push-to-talk controls and everything that assigns and
registers global hotkeys."""

import subprocess
from tkinter import messagebox

import customtkinter as ctk

from .config import save_config
from .dialogs import HotkeyDialog
from .hotkeys import (
    MACOS_INPUT_MONITORING_URL,
    HotkeyListener,
    hotkey_keys,
    hotkey_permission_granted,
    is_valid_hotkey,
    pin_macos_keyboard_layout,
    request_hotkey_permission,
)
from .theme import (
    COLOR_BG,
    COLOR_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
)


class MicHotkeyMixin:
    """Mic state and hotkey registration. Expects `config`, `audio_engine`,
    `root` and `hotkey_listener` from Soundboard."""

    # -- mic controls ---------------------------------------------------

    def _build_mic_controls(self, frame, row):
        ctk.CTkLabel(frame, text="Mic:", text_color=COLOR_TEXT).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        box = ctk.CTkFrame(frame, fg_color=COLOR_SURFACE)
        box.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        checkbox = dict(
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_BG, text_color=COLOR_TEXT,
        )
        button = dict(
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        self.mute_checkbox = ctk.CTkCheckBox(box, text="Mute mic", command=self._on_toggle_mute, **checkbox)
        self.mute_checkbox.grid(row=0, column=0, sticky="w")
        self.mute_hotkey_button = ctk.CTkButton(box, text="", command=self.set_mute_hotkey, **button)
        self.mute_hotkey_button.grid(row=0, column=1, sticky="w", padx=8)
        self.ptt_checkbox = ctk.CTkCheckBox(box, text="Push to talk", command=self._on_toggle_ptt, **checkbox)
        self.ptt_checkbox.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ptt_hotkey_button = ctk.CTkButton(box, text="", command=self.set_ptt_hotkey, **button)
        self.ptt_hotkey_button.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        self.mic_status = ctk.CTkLabel(box, text="", text_color=COLOR_TEXT_DIM, anchor="w")
        self.mic_status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if self.config["mic_muted"]:
            self.mute_checkbox.select()
        if self.config["push_to_talk"]:
            self.ptt_checkbox.select()
        self._ptt_held = False
        self._update_mic_hotkey_buttons()
        self._update_mic_state()

    def _update_mic_hotkey_buttons(self):
        mute = self.config.get("mute_hotkey")
        ptt = self.config.get("ptt_hotkey")
        self.mute_hotkey_button.configure(text=f"Mute hotkey: {mute}" if mute else "Set mute hotkey")
        self.ptt_hotkey_button.configure(text=f"Talk key: {ptt}" if ptt else "Set talk key")

    def _update_mic_state(self):
        """Open the mic unless it's muted, or push-to-talk is on and its key
        isn't held."""
        live = False
        ptt = self.config["push_to_talk"]
        if self.config["mic_muted"]:
            status, color = "Muted", COLOR_ERROR
        elif ptt and not self.config.get("ptt_hotkey"):
            status, color = "Set a talk key to use push to talk", COLOR_ERROR
        elif ptt and not self._ptt_held:
            status, color = "Hold the talk key to speak", COLOR_TEXT_DIM
        else:
            live = True
            status, color = "Live", COLOR_ORANGE
        self.audio_engine.mic_enabled = live
        self.mic_status.configure(text=status, text_color=color)

    def _on_toggle_mute(self):
        self.config["mic_muted"] = bool(self.mute_checkbox.get())
        save_config(self.config)
        self._update_mic_state()

    def _toggle_mute_from_hotkey(self):
        # Runs on the listener thread; Tk must be touched from the main thread.
        def toggle():
            if self.mute_checkbox.get():
                self.mute_checkbox.deselect()
            else:
                self.mute_checkbox.select()
            self._on_toggle_mute()
        self.root.after(0, toggle)

    def _on_toggle_ptt(self):
        self.config["push_to_talk"] = bool(self.ptt_checkbox.get())
        save_config(self.config)
        self._apply_hotkeys()
        self._update_mic_state()

    def _on_ptt_hold(self, held):
        # Runs on the listener thread: switch the mic right away, then
        # update the label on the main thread.
        self._ptt_held = held
        if self.config["push_to_talk"] and not self.config["mic_muted"]:
            self.audio_engine.mic_enabled = held
        self.root.after(0, self._update_mic_state)

    # -- assigning hotkeys ----------------------------------------------

    def set_mute_hotkey(self):
        hotkey = self._ask_hotkey("Mute mic hotkey", self.config.get("mute_hotkey"), owner="mute")
        if hotkey is None:
            return
        self.config["mute_hotkey"] = hotkey or None
        save_config(self.config)
        self._update_mic_hotkey_buttons()
        self._apply_hotkeys()

    def set_ptt_hotkey(self):
        hotkey = self._ask_hotkey("Push to talk key", self.config.get("ptt_hotkey"), owner="ptt")
        if hotkey is None:
            return
        self.config["ptt_hotkey"] = hotkey or None
        save_config(self.config)
        self._update_mic_hotkey_buttons()
        self._apply_hotkeys()
        self._update_mic_state()

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
        # Pause global hotkeys so pressing an existing combo doesn't play it.
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
            self.hotkey_listener = None
        try:
            hotkey = HotkeyDialog(self.root, title, current).get()
        finally:
            self._apply_hotkeys()
        if hotkey is None:
            return None
        if not hotkey:
            return ""
        if not is_valid_hotkey(hotkey):
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
        keys = hotkey_keys(hotkey)
        candidates = [
            ("stop", "Stop all", self.config.get("stop_hotkey")),
            ("mute", "Mute mic", self.config.get("mute_hotkey")),
            ("ptt", "Push to talk", self.config.get("ptt_hotkey")),
        ]
        candidates += [(s, f"'{s['name']}'", s.get("hotkey")) for s in self.config["sounds"]]
        for obj, label, other in candidates:
            if obj is not skip and other and hotkey_keys(other) == keys:
                return label
        return None

    # -- registering hotkeys --------------------------------------------

    def _apply_hotkeys(self):
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
            self.hotkey_listener = None

        mapping = {}
        for sound in self.config["sounds"]:
            if not sound.get("enabled", True):
                continue
            hotkey = sound.get("hotkey")
            if hotkey and is_valid_hotkey(hotkey):
                mapping[hotkey] = (lambda s=sound: self.play_sound(s))
        stop_hotkey = self.config.get("stop_hotkey")
        if stop_hotkey and is_valid_hotkey(stop_hotkey):
            mapping[stop_hotkey] = self.audio_engine.stop_all
        mute_hotkey = self.config.get("mute_hotkey")
        if mute_hotkey and is_valid_hotkey(mute_hotkey):
            mapping[mute_hotkey] = self._toggle_mute_from_hotkey
        ptt_hotkey = self.config.get("ptt_hotkey")
        if not (self.config.get("push_to_talk") and ptt_hotkey and is_valid_hotkey(ptt_hotkey)):
            ptt_hotkey = None
        self._ptt_held = False

        if mapping or ptt_hotkey:
            try:
                pin_macos_keyboard_layout()
                self.hotkey_listener = HotkeyListener(mapping, ptt_hotkey, self._on_ptt_hold)
                self.hotkey_listener.start()
            except Exception as e:
                messagebox.showwarning("Hotkey error", f"Could not register hotkeys: {e}")
        if hasattr(self, "mic_status"):
            self._update_mic_state()
        if hasattr(self, "permission_warning"):
            self._update_hotkey_permission()

    # -- macOS permission -----------------------------------------------

    def _build_permission_warning(self, parent):
        self.permission_warning = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        ctk.CTkLabel(
            self.permission_warning,
            text=(
                "macOS is blocking your hotkeys. Allow Soundboard (or your terminal, when running "
                "from source) under Privacy & Security > Input Monitoring, then restart Soundboard."
            ),
            text_color=COLOR_ERROR, justify="left", anchor="w", wraplength=650,
        ).pack(side="left", padx=(8, 8))
        ctk.CTkButton(
            self.permission_warning, text="Open settings", width=110,
            command=lambda: subprocess.run(["open", MACOS_INPUT_MONITORING_URL]),
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).pack(side="left")
        self._hotkeys_allowed = True
        self._permission_requested = False

    def _update_hotkey_permission(self):
        """Show the macOS permission warning while hotkeys are set but
        blocked, and re-register them once permission is granted."""
        if self.hotkey_listener is None:
            allowed = True  # no hotkeys, nothing to warn about
        else:
            allowed = hotkey_permission_granted()
        if allowed == self._hotkeys_allowed:
            return
        self._hotkeys_allowed = allowed
        if allowed:
            self.permission_warning.pack_forget()
            self._apply_hotkeys()
            return
        self.permission_warning.pack(fill="x", pady=(4, 0))
        if not self._permission_requested:
            self._permission_requested = True
            request_hotkey_permission()
