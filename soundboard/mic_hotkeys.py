"""Mic mute / push-to-talk controls and everything that assigns and
registers global hotkeys."""

import subprocess

import customtkinter as ctk

from .audio_engine import (
    DUCK_MIN_DB,
    DUCK_MUTE_DB,
    GATE_MAX_DB,
    GATE_MIN_DB,
    MIC_EFFECTS,
    REPLAY_S,
    duck_depth,
    pitch_semitones,
)
from .config import save_config
from .dialogs import HotkeyDialog, ask_yes_no, error, warn
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
    COLOR_ACCENT_TEXT,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_ON_ACCENT,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    font,
)


class MicHotkeyMixin:
    """Mic state and hotkey registration. Expects `config`, `audio_engine`,
    `root` and `hotkey_listener` from Soundboard."""

    # -- mic controls ---------------------------------------------------

    def _build_mic_controls(self, frame, row):
        ctk.CTkLabel(frame, text="Mic:", text_color=COLOR_TEXT).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        box = ctk.CTkFrame(frame, fg_color="transparent")
        box.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        checkbox = dict(
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT, text_color=COLOR_TEXT,
        )
        button = dict(
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.mute_checkbox = ctk.CTkCheckBox(box, text="Mute mic", command=self._on_toggle_mute, **checkbox)
        self.mute_checkbox.grid(row=0, column=0, sticky="w")
        self.mute_hotkey_button = ctk.CTkButton(box, text="", command=self.set_mute_hotkey, **button)
        self.mute_hotkey_button.grid(row=0, column=1, sticky="w", padx=8)
        self.ptt_checkbox = ctk.CTkCheckBox(box, text="Push to talk", command=self._on_toggle_ptt, **checkbox)
        self.ptt_checkbox.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ptt_hotkey_button = ctk.CTkButton(box, text="", command=self.set_ptt_hotkey, **button)
        self.ptt_hotkey_button.grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        self.replay_checkbox = ctk.CTkCheckBox(
            box, text=f"Keep the last {REPLAY_S} seconds", command=self._on_toggle_replay, **checkbox)
        self.replay_checkbox.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.replay_hotkey_button = ctk.CTkButton(
            box, text="", command=self.set_replay_hotkey, **button)
        self.replay_hotkey_button.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        if self.config.get("replay_buffer", True):
            self.replay_checkbox.select()
        effects = ctk.CTkFrame(box, fg_color="transparent")
        effects.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ctk.CTkLabel(effects, text="Voice:", text_color=COLOR_TEXT).pack(side="left")
        self.mic_effect_var = ctk.StringVar(value=self._mic_effect_label(self.config["mic_effect"]))
        ctk.CTkOptionMenu(
            effects, variable=self.mic_effect_var, width=120,
            values=[name.capitalize() for name in MIC_EFFECTS],
            command=self._on_mic_effect_change,
            fg_color=COLOR_ROW, text_color=COLOR_TEXT,
        ).pack(side="left", padx=(6, 0))
        self.mic_effect_slider = ctk.CTkSlider(
            effects, from_=0, to=100, number_of_steps=100, width=120,
            command=self._on_mic_effect_amount,
        )
        self.mic_effect_slider.set(self.config["mic_effect_amount"])
        self.mic_effect_slider.pack(side="left", padx=(8, 0))
        self.mic_effect_label = ctk.CTkLabel(
            effects, text=f"{self.config['mic_effect_amount']}%", width=42,
            font=font("small"), text_color=COLOR_TEXT_DIM,
        )
        self.mic_effect_label.pack(side="left")
        self._update_mic_effect_label()
        duck = ctk.CTkFrame(box, fg_color="transparent")
        duck.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.duck_checkbox = ctk.CTkCheckBox(
            duck, text="Duck under clips", command=self._on_toggle_duck, **checkbox)
        if self.config.get("duck_mic"):
            self.duck_checkbox.select()
        self.duck_checkbox.pack(side="left")
        self.duck_slider = ctk.CTkSlider(
            duck, from_=DUCK_MIN_DB, to=DUCK_MUTE_DB, width=120,
            number_of_steps=DUCK_MUTE_DB - DUCK_MIN_DB,
            command=self._on_duck_amount,
        )
        self.duck_slider.set(self.config.get("duck_amount", 12))
        self.duck_slider.pack(side="left", padx=(8, 0))
        self.duck_label = ctk.CTkLabel(
            duck, text="", width=52, font=font("small"), text_color=COLOR_TEXT_DIM)
        self.duck_label.pack(side="left")
        clean = ctk.CTkFrame(box, fg_color="transparent")
        clean.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.gate_checkbox = ctk.CTkCheckBox(
            clean, text="Noise gate", command=self._on_toggle_gate, **checkbox)
        if self.config.get("noise_gate"):
            self.gate_checkbox.select()
        self.gate_checkbox.pack(side="left")
        self.gate_slider = ctk.CTkSlider(
            clean, from_=GATE_MIN_DB, to=GATE_MAX_DB, width=120,
            number_of_steps=GATE_MAX_DB - GATE_MIN_DB,
            command=self._on_gate_threshold,
        )
        self.gate_slider.set(self.config.get("gate_threshold", -45))
        self.gate_slider.pack(side="left", padx=(8, 0))
        self.gate_label = ctk.CTkLabel(
            clean, text="", width=52, font=font("small"), text_color=COLOR_TEXT_DIM)
        self.gate_label.pack(side="left")
        self.highpass_checkbox = ctk.CTkCheckBox(
            clean, text="Cut rumble", command=self._on_toggle_highpass, **checkbox)
        if self.config.get("mic_highpass", True):
            self.highpass_checkbox.select()
        self.highpass_checkbox.pack(side="left", padx=(12, 0))

        self.mic_status = ctk.CTkLabel(box, text="", text_color=COLOR_TEXT_DIM, anchor="w")
        self.mic_status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))
        if self.config["mic_muted"]:
            self.mute_checkbox.select()
        if self.config["push_to_talk"]:
            self.ptt_checkbox.select()
        self._ptt_held = False
        self._update_mic_hotkey_buttons()
        self._update_mic_state()
        self._apply_mic_effect()
        self._apply_mic_cleanup()
        self._apply_replay_setting()
        self._apply_duck()

    @staticmethod
    def _mic_effect_label(name):
        return name.capitalize() if name in MIC_EFFECTS else "None"

    def _on_mic_effect_change(self, label):
        self.config["mic_effect"] = label.lower()
        save_config(self.config)
        self._update_mic_effect_label()
        self._apply_mic_effect()

    def _on_mic_effect_amount(self, value):
        self.config["mic_effect_amount"] = int(round(value))
        self._update_mic_effect_label()
        self._apply_mic_effect()
        self._save_config_soon()

    def _update_mic_effect_label(self):
        """The slider means something different for pitch: semitones
        either way from no shift, rather than how much of the effect."""
        amount = self.config["mic_effect_amount"]
        if self.config.get("mic_effect") == "pitch":
            semitones = pitch_semitones(amount / 100)
            self.mic_effect_label.configure(text=f"{semitones:+.0f}st")
        else:
            self.mic_effect_label.configure(text=f"{amount}%")

    def _apply_mic_effect(self):
        """What the effect does reaches the output device, which is what
        other people hear. Monitoring plays the board's own clips, not
        the mic, so there is nothing to change there."""
        self.audio_engine.set_mic_effect(
            self.config["mic_effect"], self.config["mic_effect_amount"] / 100,
        )

    def _on_toggle_gate(self):
        self.config["noise_gate"] = bool(self.gate_checkbox.get())
        save_config(self.config)
        self._apply_mic_cleanup()

    def _on_gate_threshold(self, value):
        self.config["gate_threshold"] = int(round(value))
        self._apply_mic_cleanup()
        self._save_config_soon()

    def _on_toggle_highpass(self):
        self.config["mic_highpass"] = bool(self.highpass_checkbox.get())
        save_config(self.config)
        self._apply_mic_cleanup()

    def _apply_mic_cleanup(self):
        """The gate and the high-pass, which sit in front of the effect
        and reach the output device the same way it does."""
        threshold = self.config.get("gate_threshold", -45)
        self.gate_label.configure(text=f"{threshold} dB")
        self.audio_engine.set_mic_cleanup(
            self.config.get("noise_gate", False), threshold,
            self.config.get("mic_highpass", True),
        )

    def _on_toggle_duck(self):
        self.config["duck_mic"] = bool(self.duck_checkbox.get())
        save_config(self.config)
        self._apply_duck()

    def _on_duck_amount(self, value):
        self.config["duck_amount"] = int(round(value))
        self._apply_duck()
        self._save_config_soon()

    def _apply_duck(self):
        """Depth of 0 switches the ducker off entirely, so the callback
        leaves the mic block alone when this is unchecked."""
        amount = self.config.get("duck_amount", 12)
        self.duck_label.configure(
            text="Mute" if amount >= DUCK_MUTE_DB else f"-{amount} dB")
        self.audio_engine.set_duck(
            duck_depth(amount) if self.config.get("duck_mic") else 0.0)

    def _update_mic_hotkey_buttons(self):
        mute = self.config.get("mute_hotkey")
        ptt = self.config.get("ptt_hotkey")
        replay = self.config.get("replay_hotkey")
        self.mute_hotkey_button.configure(text=f"Mute hotkey: {mute}" if mute else "Set mute hotkey")
        self.ptt_hotkey_button.configure(text=f"Talk key: {ptt}" if ptt else "Set talk key")
        self.replay_hotkey_button.configure(
            text=f"Save key: {replay}" if replay else "Set save key")

    def _on_toggle_replay(self):
        self.config["replay_buffer"] = bool(self.replay_checkbox.get())
        save_config(self.config)
        self._apply_replay_setting()

    def _apply_replay_setting(self):
        """Switching it off frees the buffer at the next mic block; a
        clip cannot be saved from what was never kept."""
        self.audio_engine.replay_enabled = bool(self.config.get("replay_buffer", True))

    def set_replay_hotkey(self):
        hotkey = self._ask_hotkey(
            f"Save last {REPLAY_S}s hotkey", self.config.get("replay_hotkey"), owner="replay")
        if hotkey is None:
            return
        self.config["replay_hotkey"] = hotkey or None
        save_config(self.config)
        self._update_mic_hotkey_buttons()
        self._apply_hotkeys()

    def _save_replay_from_hotkey(self):
        # Runs on the listener thread; writing a file and touching the
        # board belong on the main thread.
        self.root.after(0, self.save_replay)

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
            status, color = "Live", COLOR_ACCENT_TEXT
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
        sound = self.sounds[index]
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
            error(self.root,
                "Invalid hotkey",
                f"'{hotkey}' is not a valid hotkey. Use a format like <ctrl>+<alt>+1.",
            )
            return None
        other = self._hotkey_owner(hotkey, skip=owner)
        if other is not None:
            error(self.root, "Hotkey in use", f"'{hotkey}' is already used by {other}.")
            return None
        elsewhere = self._hotkey_owner_elsewhere(hotkey)
        if elsewhere is not None:
            profile, label = elsewhere
            # Profiles are meant to be independent and only the active
            # one's hotkeys are registered, so this is worth knowing
            # rather than worth refusing.
            if not ask_yes_no(
                self.root,
                "Used in another profile",
                f"'{hotkey}' is already {label} in the profile '{profile}'.\n\n"
                "That only matters when you switch to it. Use this key here anyway?",
            ):
                return None
        return hotkey

    def _hotkey_owner_elsewhere(self, hotkey):
        """The first sound in an inactive profile that already uses this
        key, as (profile name, label). Only the active profile's hotkeys
        are registered, so a clash there is invisible until the profiles
        are switched."""
        keys = hotkey_keys(hotkey)
        for profile in self.config.get("profiles", []):
            if profile is self.profile:
                continue
            for sound in profile.get("sounds", []):
                other = sound.get("hotkey")
                if other and hotkey_keys(other) == keys:
                    return profile.get("name", "?"), f"used by '{sound.get('name', '?')}'"
        return None

    def _hotkey_owner(self, hotkey, skip):
        keys = hotkey_keys(hotkey)
        candidates = [
            ("stop", "Stop all", self.config.get("stop_hotkey")),
            ("mute", "Mute mic", self.config.get("mute_hotkey")),
            ("ptt", "Push to talk", self.config.get("ptt_hotkey")),
            ("replay", f"Save last {REPLAY_S}s", self.config.get("replay_hotkey")),
            ("pause", "Pause", self.config.get("pause_hotkey")),
            ("next", "Play next", self.config.get("next_hotkey")),
            ("prev", "Play previous", self.config.get("prev_hotkey")),
        ]
        candidates += [(s, f"'{s['name']}'", s.get("hotkey")) for s in self.sounds]
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
        for sound in self.sounds:
            if not sound.get("enabled", True):
                continue
            hotkey = sound.get("hotkey")
            if hotkey and is_valid_hotkey(hotkey):
                mapping[hotkey] = (lambda s=sound: self.play_sound(s))
        replay_hotkey = self.config.get("replay_hotkey")
        if replay_hotkey and is_valid_hotkey(replay_hotkey):
            mapping[replay_hotkey] = self._save_replay_from_hotkey
        stop_hotkey = self.config.get("stop_hotkey")
        if stop_hotkey and is_valid_hotkey(stop_hotkey):
            mapping[stop_hotkey] = self.audio_engine.stop_all
        pause_hotkey = self.config.get("pause_hotkey")
        if pause_hotkey and is_valid_hotkey(pause_hotkey):
            mapping[pause_hotkey] = self.toggle_pause
        next_hotkey = self.config.get("next_hotkey")
        if next_hotkey and is_valid_hotkey(next_hotkey):
            mapping[next_hotkey] = self.play_next
        prev_hotkey = self.config.get("prev_hotkey")
        if prev_hotkey and is_valid_hotkey(prev_hotkey):
            mapping[prev_hotkey] = self.play_previous
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
                warn(self.root, "Hotkey error", f"Could not register hotkeys: {e}")
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
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
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
