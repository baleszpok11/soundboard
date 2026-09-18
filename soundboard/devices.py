"""Audio device listing, the device selector UI, and keeping the streams
connected to the devices the user picked."""

import sys
import time
import tkinter as tk
import webbrowser

from .dialogs import error
import customtkinter as ctk

from .audio_engine import SAMPLE_RATE, sd, test_tone
from .config import save_config
from .theme import (
    CARD_BORDER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_ERROR_TEXT,
    COLOR_ON_ACCENT,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    GAP,
    VOLUME_MAX,
    font,
)

NO_DEVICE_LABEL = "(none)"
METER_POLL_MS = 50
METER_DECAY = 0.08  # how far a meter falls per tick when the signal drops
METER_HOT = 0.95  # at or above this the meter turns red
TEST_TONE_KEY = "test-tone"
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
# Where to get a virtual cable, for the platforms where one is installed
# as a driver. Linux is left out on purpose: its cable is a PulseAudio
# null sink, which PortAudio never lists by name, so a missing one can't
# be told apart from a working one.
CABLE_INSTALL = {
    "win32": ("VB-CABLE", "https://vb-audio.com/Cable/"),
    "darwin": ("BlackHole", "https://github.com/ExistentialAudio/BlackHole"),
}


class DeviceMixin:
    """Device selection and stream health. Expects `config`, `audio_engine`
    and `root` from Soundboard."""

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

    def _missing_cable_install(self):
        """(name, url) of the virtual cable to install when none of the
        output devices looks like one, else None.

        Without a cable the app still mixes and plays, so nothing looks
        broken: the meters move and the test tone is audible. What is
        missing is the one thing it is for - no other app can select the
        mix as its microphone - so it is worth saying rather than leaving
        someone to work out why nobody hears the clips.
        """
        install = CABLE_INSTALL.get(sys.platform)
        if install is None:
            return None
        for index, name in self.output_devices:
            if index is not None and any(hint in name.lower() for hint in VIRTUAL_CABLE_HINTS):
                return None
        return install

    # -- device selection UI -------------------------------------------------

    def _build_device_selectors(self, parent):
        """Build the settings panel. It is not placed here: it has two
        homes, its own tab and a disclosure on the Soundboard tab, and
        app._place_settings_panel decides which one is showing. `parent`
        must be an ancestor of both, since a Tk widget cannot be
        reparented and this one is shown with pack(in_=...)."""
        frame = ctk.CTkFrame(
            parent, fg_color=COLOR_SURFACE,
            border_width=CARD_BORDER, border_color=COLOR_BORDER,
        )
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Microphone (input):", text_color=COLOR_TEXT_DIM, font=font("small_bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        input_names = [name for _, name in self.input_devices]
        current_input = self.config.get("input_device")
        self.input_var = tk.StringVar(value=current_input or NO_DEVICE_LABEL)
        # A cell frame, so the level meter can sit under the menu.
        input_box = ctk.CTkFrame(frame, fg_color="transparent")
        input_box.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        self.input_menu = ctk.CTkOptionMenu(
            input_box,
            variable=self.input_var,
            values=input_names,
            command=self._on_input_device_change,
            fg_color=COLOR_ROW,
            text_color=COLOR_TEXT,
        )
        self.input_menu.pack(fill="x")
        self.input_meter = self._build_meter(input_box)
        ctk.CTkButton(
            frame, text="Refresh devices", command=self.refresh_devices,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).grid(row=0, column=2, sticky="w", padx=8, pady=8)

        ctk.CTkLabel(frame, text="Virtual mic output:", text_color=COLOR_TEXT_DIM, font=font("small_bold")).grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        output_names = [name for _, name in self.output_devices]
        current_output = self.config.get("output_device")
        self.output_var = tk.StringVar(value=current_output or NO_DEVICE_LABEL)
        output_box = ctk.CTkFrame(frame, fg_color="transparent")
        output_box.grid(row=1, column=1, sticky="ew", padx=8, pady=8)
        self.output_menu = ctk.CTkOptionMenu(
            output_box,
            variable=self.output_var,
            values=output_names,
            command=self._on_output_device_change,
            fg_color=COLOR_ROW,
            text_color=COLOR_TEXT,
        )
        self.output_menu.pack(fill="x")
        self.output_meter = self._build_meter(output_box)

        output_side = ctk.CTkFrame(frame, fg_color="transparent")
        output_side.grid(row=1, column=2, sticky="w", padx=8, pady=8)
        self.hear_self_checkbox = ctk.CTkCheckBox(
            output_side,
            text="Hear soundboard",
            fg_color=COLOR_ORANGE,
            hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT,
            text_color=COLOR_TEXT,
        )
        if self.config.get("hear_self", True):
            self.hear_self_checkbox.select()
        else:
            self.hear_self_checkbox.deselect()
        self.hear_self_checkbox.configure(command=self._on_toggle_hear_self)
        self.hear_self_checkbox.pack(side="left")
        ctk.CTkButton(
            output_side, text="Test", width=60, command=self.test_output,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="left", padx=(8, 0))

        self._add_volume_row(frame, 2, "Mic volume:", "mic_volume", "mic_gain")
        self._add_volume_row(frame, 3, "Soundboard volume:", "sound_volume", "sound_gain")
        self._build_mic_controls(frame, 4)
        self._build_startup_controls(frame, 5)
        self._build_appearance_controls(frame, 6)
        return frame

    def _build_cable_warning(self, parent):
        """Hidden until _update_device_warnings finds no virtual cable.
        Its own row rather than a line in loop_warning, because the
        useful part is the button that opens the installer page."""
        self.cable_warning = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        self.cable_warning_label = ctk.CTkLabel(
            self.cable_warning, text="", text_color=COLOR_ERROR_TEXT,
            justify="left", anchor="w", wraplength=650,
        )
        self.cable_warning_label.pack(side="left", padx=(8, 8))
        self.cable_warning_button = ctk.CTkButton(
            self.cable_warning, text="", width=110,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.cable_warning_button.pack(side="left")

    def _update_cable_warning(self, install):
        frame = getattr(self, "cable_warning", None)
        if frame is None:
            return
        if install is None:
            frame.pack_forget()
            return
        name, url = install
        self.cable_warning_label.configure(text=(
            "No virtual audio cable is installed, so the mix stays on this computer: you "
            f"hear the clips, Discord and games don't. Install {name}, then click Refresh "
            "devices and pick it as the virtual mic output."
        ))
        self.cable_warning_button.configure(text=f"Get {name}", command=lambda: webbrowser.open(url))
        if not frame.winfo_manager():
            frame.pack(fill="x", padx=8, pady=(4, 0))

    # -- level meters ---------------------------------------------------

    @staticmethod
    def _build_meter(parent):
        meter = ctk.CTkProgressBar(
            parent, height=4, fg_color=COLOR_ROW_HOVER, progress_color=COLOR_ORANGE,
        )
        meter.set(0)
        meter.pack(fill="x", pady=(3, 0))
        meter.level = 0.0  # decayed value, so the bar falls instead of flickering
        return meter

    def _poll_meters(self):
        engine = self.audio_engine
        # The mic meter shows the raw signal even while muted, so "my mic
        # is dead" and "my mic is muted" don't look the same.
        live = engine.mic_enabled and not self.config["mic_muted"]
        self._update_meter(self.input_meter, engine.input_peak, engine.input_stream is not None, live)
        self._update_meter(self.output_meter, engine.output_peak, engine.output_stream is not None, True)
        self._meter_poll = self.root.after(METER_POLL_MS, self._poll_meters)

    @staticmethod
    def _update_meter(meter, peak, connected, live):
        if not connected:
            meter.level = 0.0
            meter.set(0)
            meter.configure(progress_color=COLOR_ROW)
            return
        # Rise instantly, fall gradually, so short peaks stay readable.
        meter.level = max(min(peak, 1.0), meter.level - METER_DECAY)
        meter.set(meter.level)
        if not live:
            color = COLOR_TEXT_DIM  # picking up sound, but nothing is going out
        elif meter.level >= METER_HOT:
            color = COLOR_ERROR
        else:
            color = COLOR_ORANGE
        meter.configure(progress_color=color)

    def test_output(self):
        """Play a tone through the output device, so routing can be
        checked without asking someone else if they can hear you."""
        try:
            self.audio_engine.play_data(test_tone(), SAMPLE_RATE, key=TEST_TONE_KEY)
        except RuntimeError as e:
            error(self.root, "Test output", str(e))

    def _add_volume_row(self, frame, row, text, config_key, engine_attr):
        ctk.CTkLabel(frame, text=text, text_color=COLOR_TEXT_DIM, font=font("small_bold")).grid(
            row=row, column=0, sticky="w", padx=8, pady=6)
        value_label = ctk.CTkLabel(
            frame, text=f"{self.config[config_key]}%", width=50,
            font=font("small"), text_color=COLOR_TEXT_DIM)

        def on_change(value):
            percent = int(round(value))
            self.config[config_key] = percent
            setattr(self.audio_engine, engine_attr, percent / 100)
            value_label.configure(text=f"{percent}%")
            self._save_config_soon()

        slider = ctk.CTkSlider(
            frame, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, command=on_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        slider.set(self.config[config_key])
        slider.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        value_label.grid(row=row, column=2, sticky="w", padx=8, pady=6)

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

    # -- starting and watching the streams ----------------------------------

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
        self._update_cable_warning(self._missing_cable_install())
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
                error(self.root, "Audio device error", f"Could not rescan audio devices: {e}")
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
            error(self.root, "Audio device error", str(error))
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

    def _update_dropout_label(self):
        """One-second tick: stream health, device warnings and hotkey
        permission."""
        count = self.audio_engine.dropouts
        self.dropout_label.configure(text=f"Audio dropouts: {count}" if count else "")
        self._watch_output()
        self._update_device_warnings()
        self._update_hotkey_permission()
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
