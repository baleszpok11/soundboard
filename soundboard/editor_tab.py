"""The Sound Editor tab: trim a clip, adjust its bass, save it as a new sound."""

import os
import tkinter as tk
from tkinter import filedialog

from .dialogs import error, info
import customtkinter as ctk
import numpy as np
import soundfile as sf

from .audio_engine import _resample
from .dsp import (
    apply_bass,
    apply_drive,
    apply_echo,
    apply_mid,
    apply_robot,
    apply_stutter,
    apply_telephone,
    apply_treble,
    apply_width,
    pitch_shift,
    reverse,
    time_stretch,
)
from .config import (
    SOUNDS_DIR,
    ensure_sounds_dir,
    resolve_sound_path,
    sanitize_filename,
    sound_paths,
    unique_path,
)
from .theme import (
    COLOR_BG,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_ERROR_HOVER,
    COLOR_ON_ACCENT,
    COLOR_ON_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    font,
    on_appearance_change,
    register_tk,
    resolve,
)

MIN_CLIP_S = 0.05  # shortest clip the editor can trim to
SAVE_PEAK = 0.99  # edited clips louder than full scale are scaled down to this
EDITOR_PLACEHOLDER = "Choose a sound..."
EDITOR_PREVIEW_KEY = "editor-preview"
# Ranges are deliberately past the point of good taste: ruining a clip is
# half of what a soundboard is for.
BASS_RANGE_DB = 36
GAIN_RANGE_DB = 40
PITCH_RANGE_ST = 24  # two octaves either way
SPEED_RANGE_OCTAVES = 2  # the speed slider is in octaves, so 0.25x to 4x
MIN_FADE_MAX_S = 0.01  # a slider whose ends meet divides by zero
PLAYHEAD_POLL_MS = 50
UNDO_SETTLE_MS = 400  # sliders fire continuously; one undo step per burst

TONE_RANGE_DB = 36
DRIVE_MAX_DB = 36
ROBOT_MAX_HZ = 400
ECHO_MAX = 0.85  # any more and the repeats outlast anyone's patience
STUTTER_MAX_S = 0.3
WIDTH_MAX = 2.0


def _db_text(value):
    return f"{value:+.0f} dB"


def _drive_text(value):
    return "off" if value <= 0 else f"{value:.0f} dB"


def _hz_text(value):
    return "off" if value <= 0 else f"{value:.0f} Hz"


def _amount_text(value):
    return "off" if value <= 0 else f"{value * 100:.0f}%"


def _ms_text(value):
    return "off" if value <= 0 else f"{value * 1000:.0f} ms"


def _width_text(value):
    return "mono" if value <= 0 else f"{value:.2f}x"


# key, label, range, default, how the value reads. Kept as data because
# every one of them has to be reset, saved for undo and restored again.
EFFECT_SLIDERS = (
    ("treble", "Treble:", -TONE_RANGE_DB, TONE_RANGE_DB, 0.0, _db_text),
    ("mid", "Mid:", -TONE_RANGE_DB, TONE_RANGE_DB, 0.0, _db_text),
    ("drive", "Drive:", 0.0, DRIVE_MAX_DB, 0.0, _drive_text),
    ("robot", "Robot:", 0.0, ROBOT_MAX_HZ, 0.0, _hz_text),
    ("echo", "Echo:", 0.0, ECHO_MAX, 0.0, _amount_text),
    ("stutter", "Stutter:", 0.0, STUTTER_MAX_S, 0.0, _ms_text),
    ("width", "Width:", 0.0, WIDTH_MAX, 1.0, _width_text),
)
EFFECT_TOGGLES = (("telephone", "Telephone"), ("reverse", "Reverse"))

# What to do when the clip ends up louder than full scale.
CLIP_CLEAN = "Keep clean"
CLIP_HARD = "Let it clip"
CLIP_SOFT = "Soft clip"
CLIP_MODES = (CLIP_CLEAN, CLIP_HARD, CLIP_SOFT)


def speed_for_semitones(semitones):
    """Playback rate that shifts a clip by this many semitones. Resampling
    moves pitch and speed together, which is the effect a soundboard
    wants; separating them would need real time-stretching."""
    return 2 ** (semitones / 12)


class EditorMixin:
    """Sound Editor tab. Expects `config` and `audio_engine` from
    Soundboard, and `_add_sound_entry` from SoundListMixin."""

    def _build_editor_tab(self, parent):
        top = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        top.pack(fill="x", padx=4, pady=4)
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Sound:", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.editor_sound_var = tk.StringVar(value=EDITOR_PLACEHOLDER)
        self.editor_sound_menu = ctk.CTkOptionMenu(
            top,
            variable=self.editor_sound_var,
            values=[EDITOR_PLACEHOLDER],
            command=self._on_editor_sound_selected,
            fg_color=COLOR_ROW,
            text_color=COLOR_TEXT,
        )
        self.editor_sound_menu.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(
            top, text="Browse...", width=90, command=self._on_editor_browse,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).grid(row=0, column=2, sticky="e", padx=8, pady=8)

        self.editor_canvas = tk.Canvas(parent, height=140, highlightthickness=0)
        # The waveform is redrawn from scratch on a mode change, so the
        # canvas only needs its background tracked.
        register_tk(self.editor_canvas, bg=COLOR_ROW)
        on_appearance_change(self._draw_waveform)
        self.editor_canvas.pack(fill="x", padx=4, pady=4)
        self.editor_canvas.bind("<Configure>", lambda e: self._draw_waveform())

        buttons = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        buttons.pack(side="bottom", fill="x", padx=4, pady=(4, 8))
        self.editor_preview_button = ctk.CTkButton(
            buttons, text="Preview", command=self._on_editor_preview,
            hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT, text_color_disabled=COLOR_TEXT_DIM,
        )
        self.editor_preview_button.pack(side="left", padx=(4, 4))
        self.editor_stop_button = ctk.CTkButton(
            buttons, text="Stop", command=lambda: self.audio_engine.stop_key(EDITOR_PREVIEW_KEY),
            hover_color=COLOR_ERROR_HOVER, text_color=COLOR_ON_ERROR, text_color_disabled=COLOR_TEXT_DIM,
        )
        self.editor_stop_button.pack(side="left", padx=(4, 4))
        self.editor_undo_button = ctk.CTkButton(
            buttons, text="Undo", command=self._on_editor_undo,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            text_color_disabled=COLOR_TEXT_DIM, border_width=1,
        )
        self.editor_undo_button.pack(side="left", padx=(4, 4))
        self.editor_save_button = ctk.CTkButton(
            buttons, text="Save as new sound", command=self._on_editor_save,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            text_color_disabled=COLOR_TEXT_DIM, border_width=1,
        )
        self.editor_save_button.pack(side="left", padx=(4, 4))

        scroll = ctk.CTkScrollableFrame(parent, fg_color=COLOR_BG)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        trim_frame = ctk.CTkFrame(scroll, fg_color=COLOR_SURFACE)
        trim_frame.pack(fill="x", padx=4, pady=4)
        trim_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(trim_frame, text="Start:", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.editor_start_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=lambda v: self._on_trim_change(moved="start"),
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_start_slider.set(0)
        self.editor_start_slider.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        self.editor_start_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_start_label.grid(row=0, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="End:", text_color=COLOR_TEXT).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.editor_end_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=lambda v: self._on_trim_change(moved="end"),
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_end_slider.set(1)
        self.editor_end_slider.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        self.editor_end_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_end_label.grid(row=1, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Bass:", text_color=COLOR_TEXT).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.editor_bass_slider = ctk.CTkSlider(
            trim_frame, from_=-BASS_RANGE_DB, to=BASS_RANGE_DB, command=self._on_bass_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_bass_slider.set(0)
        self.editor_bass_slider.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        self.editor_bass_label = ctk.CTkLabel(trim_frame, text="+0 dB", text_color=COLOR_TEXT, width=60)
        self.editor_bass_label.grid(row=2, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Volume:", text_color=COLOR_TEXT).grid(row=3, column=0, sticky="w", padx=8, pady=6)
        self.editor_gain_slider = ctk.CTkSlider(
            trim_frame, from_=-GAIN_RANGE_DB, to=GAIN_RANGE_DB, command=self._on_gain_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_gain_slider.set(0)
        self.editor_gain_slider.grid(row=3, column=1, sticky="ew", padx=8, pady=6)
        self.editor_gain_label = ctk.CTkLabel(trim_frame, text="+0 dB", text_color=COLOR_TEXT, width=60)
        self.editor_gain_label.grid(row=3, column=2, padx=8, pady=6)
        self.editor_normalize_checkbox = ctk.CTkCheckBox(
            trim_frame, text="Normalize", command=self._on_normalize_change,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT, text_color=COLOR_TEXT,
        )
        self.editor_normalize_checkbox.grid(row=3, column=3, sticky="w", padx=(0, 8), pady=6)

        fades = []
        for row, text in ((4, "Fade in:"), (5, "Fade out:")):
            ctk.CTkLabel(trim_frame, text=text, text_color=COLOR_TEXT).grid(
                row=row, column=0, sticky="w", padx=8, pady=6
            )
            slider = ctk.CTkSlider(
                trim_frame, from_=0, to=MIN_FADE_MAX_S, command=lambda v: self._on_fade_change(),
                fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
            )
            slider.set(0)
            slider.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
            label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
            label.grid(row=row, column=2, padx=8, pady=6)
            fades.append((slider, label))
        (self.editor_fade_in_slider, self.editor_fade_in_label), \
            (self.editor_fade_out_slider, self.editor_fade_out_label) = fades

        ctk.CTkLabel(trim_frame, text="Pitch:", text_color=COLOR_TEXT).grid(
            row=6, column=0, sticky="w", padx=8, pady=6
        )
        self.editor_pitch_slider = ctk.CTkSlider(
            trim_frame, from_=-PITCH_RANGE_ST, to=PITCH_RANGE_ST,
            number_of_steps=PITCH_RANGE_ST * 4, command=self._on_pitch_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_pitch_slider.set(0)
        self.editor_pitch_slider.grid(row=6, column=1, sticky="ew", padx=8, pady=6)
        self.editor_pitch_label = ctk.CTkLabel(
            trim_frame, text=self._pitch_text(0), text_color=COLOR_TEXT, width=60,
        )
        self.editor_pitch_label.grid(row=6, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Speed:", text_color=COLOR_TEXT).grid(
            row=7, column=0, sticky="w", padx=8, pady=6
        )
        self.editor_speed_slider = ctk.CTkSlider(
            trim_frame, from_=-SPEED_RANGE_OCTAVES, to=SPEED_RANGE_OCTAVES,
            command=self._on_speed_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_speed_slider.set(0)
        self.editor_speed_slider.grid(row=7, column=1, sticky="ew", padx=8, pady=6)
        self.editor_speed_label = ctk.CTkLabel(
            trim_frame, text=self._speed_text(0), text_color=COLOR_TEXT, width=60,
        )
        self.editor_speed_label.grid(row=7, column=2, padx=8, pady=6)
        self.editor_link_checkbox = ctk.CTkCheckBox(
            trim_frame, text="Tape", command=self._on_link_change,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT, text_color=COLOR_TEXT,
        )
        self.editor_link_checkbox.grid(row=7, column=3, sticky="w", padx=(0, 8), pady=6)

        ctk.CTkLabel(trim_frame, text="Too loud:", text_color=COLOR_TEXT).grid(
            row=8, column=0, sticky="w", padx=8, pady=6
        )
        self.editor_clip_mode = ctk.CTkSegmentedButton(
            trim_frame, values=list(CLIP_MODES),
            command=lambda _v: self._record_editor_change(), selected_hover_color=COLOR_ORANGE_HOVER, unselected_hover_color=COLOR_SURFACE, text_color=COLOR_TEXT,
        )
        self.editor_clip_mode.set(CLIP_CLEAN)
        self.editor_clip_mode.grid(row=8, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        effects = ctk.CTkFrame(scroll, fg_color=COLOR_SURFACE)
        effects.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(
            effects, text="Effects", text_color=COLOR_TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, columnspan=6, sticky="w", padx=8, pady=(6, 0))
        for column in (1, 4):
            effects.grid_columnconfigure(column, weight=1)

        # Two columns, so seven sliders cost four rows instead of seven.
        self.editor_effect_sliders = {}
        for index, (key, text, low, high, default, fmt) in enumerate(EFFECT_SLIDERS):
            row = 1 + index // 2
            column = (index % 2) * 3
            ctk.CTkLabel(effects, text=text, text_color=COLOR_TEXT).grid(
                row=row, column=column, sticky="w", padx=(8, 4), pady=6
            )
            label = ctk.CTkLabel(effects, text=fmt(default), text_color=COLOR_TEXT, width=60)
            slider = ctk.CTkSlider(
                effects, from_=low, to=high,
                command=lambda value, k=key: self._on_effect_change(k, value),
                fg_color=COLOR_ROW, progress_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
            )
            slider.set(default)
            slider.grid(row=row, column=column + 1, sticky="ew", padx=4, pady=6)
            label.grid(row=row, column=column + 2, padx=(4, 8), pady=6)
            self.editor_effect_sliders[key] = (slider, label, fmt, default)

        self.editor_effect_toggles = {}
        # An odd number of sliders leaves half a row free; put the toggles
        # there rather than starting another one.
        spare = len(EFFECT_SLIDERS) % 2
        last_row = 1 + (len(EFFECT_SLIDERS) - 1) // 2
        row = last_row if spare else last_row + 1
        column = 3 if spare else 0
        for index, (key, text) in enumerate(EFFECT_TOGGLES):
            box = ctk.CTkCheckBox(
                effects, text=text, command=self._record_editor_change,
                fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
                checkmark_color=COLOR_ON_ACCENT, text_color=COLOR_TEXT,
            )
            box.grid(row=row, column=column + index, sticky="w", padx=8, pady=6)
            self.editor_effect_toggles[key] = box

        self._set_editor_enabled(False)

        self._refresh_editor_sound_list()

    def _refresh_editor_sound_list(self):
        self._editor_sound_paths = {}
        names = []
        for sound in self.sounds:
            # Every clip of a random group is editable on its own, so the
            # one take that came out too quiet can be fixed.
            paths = sound_paths(sound)
            for path in paths:
                label = sound["name"] if len(paths) == 1 else f"{sound['name']} ({path})"
                while label in self._editor_sound_paths:
                    label = f"{label} ({path})"
                self._editor_sound_paths[label] = resolve_sound_path(path)
                names.append(label)
        self.editor_sound_menu.configure(values=names or [EDITOR_PLACEHOLDER])
        # Keep showing what's loaded (it may be a browsed file that isn't on
        # the board, or a sound that was just removed).
        if self.editor_data is None:
            self.editor_sound_var.set(EDITOR_PLACEHOLDER)

    def _on_editor_sound_selected(self, label):
        path = self._editor_sound_paths.get(label)
        if path:
            self._load_editor_file(path, label)

    def _on_editor_browse(self):
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if path:
            self._load_editor_file(path, os.path.basename(path))

    def _load_editor_file(self, path, label):
        try:
            data, samplerate = sf.read(path, dtype="float32", always_2d=True)
            if len(data) == 0:
                raise ValueError("The file contains no audio.")
        except Exception as e:
            error(self.root, "Could not load sound", str(e))
            self.editor_sound_var.set(self._editor_label if self.editor_data is not None else EDITOR_PLACEHOLDER)
            return
        self._editor_label = label
        self.editor_sound_var.set(label)
        self._set_editor_enabled(True)
        self.editor_path = path
        self.editor_data = data
        self.editor_samplerate = samplerate
        self._editor_mono = data.mean(axis=1)
        duration = len(data) / samplerate
        self.editor_start_slider.configure(from_=0, to=duration)
        self.editor_end_slider.configure(from_=0, to=duration)
        self.editor_start_slider.set(0)
        self.editor_end_slider.set(duration)
        self.editor_start_label.configure(text="0.00s")
        self.editor_end_label.configure(text=f"{duration:.2f}s")
        self.editor_bass_slider.set(0)
        self.editor_bass_label.configure(text="+0 dB")
        self.editor_gain_slider.set(0)
        self.editor_gain_label.configure(text="+0 dB")
        self.editor_normalize_checkbox.deselect()
        self._set_gain_enabled(True)
        for slider in (self.editor_fade_in_slider, self.editor_fade_out_slider):
            slider.set(0)
        self._clamp_fades()
        self.editor_pitch_slider.set(0)
        self.editor_pitch_label.configure(text=self._pitch_text(0))
        self.editor_speed_slider.set(0)
        self.editor_speed_label.configure(text=self._speed_text(0))
        self.editor_link_checkbox.deselect()
        self.editor_clip_mode.set(CLIP_CLEAN)
        for slider, label, fmt, default in self.editor_effect_sliders.values():
            slider.set(default)
            label.configure(text=fmt(default))
        for box in self.editor_effect_toggles.values():
            box.deselect()
        self._editor_undo = []
        self._editor_committed = self._editor_state()
        self._update_undo_button()
        self._draw_waveform()

    def _set_editor_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        accent = COLOR_ORANGE if enabled else COLOR_TEXT_DIM
        for slider in (self.editor_start_slider, self.editor_end_slider, self.editor_bass_slider,
                       self.editor_fade_in_slider, self.editor_fade_out_slider,
                       self.editor_pitch_slider, self.editor_speed_slider):
            slider.configure(state=state, button_color=accent, progress_color=accent)
        self.editor_normalize_checkbox.configure(state=state)
        self.editor_link_checkbox.configure(state=state)
        for slider, _label, _fmt, _default in self.editor_effect_sliders.values():
            slider.configure(state=state, button_color=accent, progress_color=accent)
        for box in self.editor_effect_toggles.values():
            box.configure(state=state)
        self.editor_clip_mode.configure(state=state)
        self._set_gain_enabled(not self.editor_normalize_checkbox.get())
        self.editor_preview_button.configure(state=state, fg_color=COLOR_ORANGE if enabled else COLOR_ROW)
        self.editor_stop_button.configure(state=state, fg_color=COLOR_ERROR if enabled else COLOR_ROW)
        self.editor_save_button.configure(state=state, border_color=COLOR_BORDER if enabled else COLOR_TEXT_DIM)
        self._update_undo_button()

    def _set_gain_enabled(self, enabled):
        """The gain slider has no effect while Normalize decides the level."""
        enabled = enabled and self.editor_data is not None
        accent = COLOR_ORANGE if enabled else COLOR_TEXT_DIM
        self.editor_gain_slider.configure(
            state="normal" if enabled else "disabled", button_color=accent, progress_color=accent,
        )

    def _update_undo_button(self):
        can_undo = bool(self._editor_undo) and self.editor_data is not None
        self.editor_undo_button.configure(
            state="normal" if can_undo else "disabled",
            border_color=COLOR_BORDER if can_undo else COLOR_TEXT_DIM,
        )

    def _on_trim_change(self, moved):
        """Keep at least MIN_CLIP_S between the markers by pushing the
        other marker, or holding the dragged one back at the file edge."""
        if self.editor_data is None:
            return
        duration = len(self.editor_data) / self.editor_samplerate
        gap = min(MIN_CLIP_S, duration)
        start = self.editor_start_slider.get()
        end = self.editor_end_slider.get()
        if end - start < gap:
            if moved == "start":
                start = min(start, duration - gap)
                end = start + gap
            else:
                end = max(end, gap)
                start = end - gap
            self.editor_start_slider.set(start)
            self.editor_end_slider.set(end)
        self.editor_start_label.configure(text=f"{start:.2f}s")
        self.editor_end_label.configure(text=f"{end:.2f}s")
        self._clamp_fades()
        self._update_selection()
        self._record_editor_change()

    def _on_bass_change(self, value):
        self.editor_bass_label.configure(text=f"{value:+.0f} dB")
        self._record_editor_change()

    def _on_gain_change(self, value):
        self.editor_gain_label.configure(text=f"{value:+.0f} dB")
        self._record_editor_change()

    @staticmethod
    def _pitch_text(semitones):
        return f"{semitones:+.0f} st"

    @staticmethod
    def _speed_text(octaves):
        return f"{2 ** octaves:.2f}x"

    def _on_pitch_change(self, value):
        self.editor_pitch_label.configure(text=self._pitch_text(value))
        if self.editor_link_checkbox.get():
            self._set_speed(value / 12)
        self._record_editor_change()

    def _on_speed_change(self, value):
        self.editor_speed_label.configure(text=self._speed_text(value))
        if self.editor_link_checkbox.get():
            self._set_pitch(value * 12)
        self._record_editor_change()

    def _set_speed(self, octaves):
        octaves = max(-SPEED_RANGE_OCTAVES, min(SPEED_RANGE_OCTAVES, octaves))
        self.editor_speed_slider.set(octaves)
        self.editor_speed_label.configure(text=self._speed_text(octaves))

    def _set_pitch(self, semitones):
        semitones = max(-PITCH_RANGE_ST, min(PITCH_RANGE_ST, semitones))
        self.editor_pitch_slider.set(semitones)
        self.editor_pitch_label.configure(text=self._pitch_text(semitones))

    def _on_effect_change(self, key, value):
        _slider, label, fmt, _default = self.editor_effect_sliders[key]
        label.configure(text=fmt(value))
        self._record_editor_change()

    def _effect(self, key):
        return self.editor_effect_sliders[key][0].get()

    def _on_link_change(self):
        """Tape mode ties the two together, the way a tape machine does.
        Pitch is the one that was here first, so speed follows it."""
        if self.editor_link_checkbox.get():
            self._set_speed(self.editor_pitch_slider.get() / 12)
        self._record_editor_change()

    def _on_normalize_change(self):
        self._set_gain_enabled(not self.editor_normalize_checkbox.get())
        self._record_editor_change()

    def _on_fade_change(self):
        self._clamp_fades()
        self._record_editor_change()

    def _clamp_fades(self):
        """Both fades share the selection, so neither can take more than
        half of it. That half is also the slider's range, so a long clip
        can fade for as long as it likes."""
        limit = max(MIN_FADE_MAX_S, (self.editor_end_slider.get() - self.editor_start_slider.get()) / 2)
        for slider, label in ((self.editor_fade_in_slider, self.editor_fade_in_label),
                              (self.editor_fade_out_slider, self.editor_fade_out_label)):
            slider.configure(to=limit)
            if slider.get() > limit:
                slider.set(limit)
            label.configure(text=f"{slider.get():.2f}s")

    # -- undo -----------------------------------------------------------

    def _editor_state(self):
        return {
            "start": self.editor_start_slider.get(),
            "end": self.editor_end_slider.get(),
            "bass": self.editor_bass_slider.get(),
            "gain": self.editor_gain_slider.get(),
            "normalize": bool(self.editor_normalize_checkbox.get()),
            "fade_in": self.editor_fade_in_slider.get(),
            "fade_out": self.editor_fade_out_slider.get(),
            "pitch": self.editor_pitch_slider.get(),
            "speed": self.editor_speed_slider.get(),
            "link": bool(self.editor_link_checkbox.get()),
            "clip": self.editor_clip_mode.get(),
            "effects": {key: slider.get()
                        for key, (slider, _l, _f, _d) in self.editor_effect_sliders.items()},
            "toggles": {key: bool(box.get()) for key, box in self.editor_effect_toggles.items()},
        }

    def _apply_editor_state(self, state):
        self.editor_start_slider.set(state["start"])
        self.editor_end_slider.set(state["end"])
        self.editor_bass_slider.set(state["bass"])
        self.editor_gain_slider.set(state["gain"])
        if state["normalize"]:
            self.editor_normalize_checkbox.select()
        else:
            self.editor_normalize_checkbox.deselect()
        self.editor_fade_in_slider.set(state["fade_in"])
        self.editor_fade_out_slider.set(state["fade_out"])
        self.editor_pitch_slider.set(state["pitch"])
        self._set_speed(state["speed"])
        if state["link"]:
            self.editor_link_checkbox.select()
        else:
            self.editor_link_checkbox.deselect()
        self.editor_clip_mode.set(state["clip"])
        for key, value in state["effects"].items():
            slider, label, fmt, _default = self.editor_effect_sliders[key]
            slider.set(value)
            label.configure(text=fmt(value))
        for key, value in state["toggles"].items():
            box = self.editor_effect_toggles[key]
            box.select() if value else box.deselect()
        self.editor_start_label.configure(text=f"{state['start']:.2f}s")
        self.editor_end_label.configure(text=f"{state['end']:.2f}s")
        self.editor_bass_label.configure(text=f"{state['bass']:+.0f} dB")
        self.editor_gain_label.configure(text=f"{state['gain']:+.0f} dB")
        self.editor_fade_in_label.configure(text=f"{state['fade_in']:.2f}s")
        self.editor_fade_out_label.configure(text=f"{state['fade_out']:.2f}s")
        self.editor_pitch_label.configure(text=self._pitch_text(state["pitch"]))
        self.editor_speed_label.configure(text=self._speed_text(state["speed"]))
        self._set_gain_enabled(not state["normalize"])
        self._update_selection()

    def _record_editor_change(self):
        if self._editor_commit is not None:
            self.root.after_cancel(self._editor_commit)
        self._editor_commit = self.root.after(UNDO_SETTLE_MS, self._commit_editor_change)

    def _commit_editor_change(self):
        """One undo step per settled burst of slider moves."""
        self._editor_commit = None
        state = self._editor_state()
        if self._editor_committed is not None and state != self._editor_committed:
            self._editor_undo.append(self._editor_committed)
            self._update_undo_button()
        self._editor_committed = state

    def _on_editor_undo(self):
        if self._editor_commit is not None:
            self.root.after_cancel(self._editor_commit)
            self._commit_editor_change()  # the change being undone may not be recorded yet
        if not self._editor_undo:
            return
        state = self._editor_undo.pop()
        self._apply_editor_state(state)
        self._editor_committed = state
        self._update_undo_button()

    def _draw_waveform(self):
        """Full redraw, only on load and resize. Slider moves use
        _update_selection, which just moves the markers."""
        canvas = self.editor_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            width, height = 480, 140
        if self.editor_data is None:
            canvas.create_text(
                width / 2, height / 2, fill=resolve(COLOR_TEXT_DIM), font=font("body"),
                text="Choose a sound above, or click Browse... to open any audio file.",
            )
            return
        canvas.create_rectangle(0, 0, 0, height, fill=resolve(COLOR_ROW_HOVER), outline="", tags="selection")

        mono = self._editor_mono
        columns = min(width, len(mono))
        edges = np.linspace(0, len(mono), columns + 1).astype(int)[:-1]
        highs = np.maximum.reduceat(mono, edges)
        lows = np.minimum.reduceat(mono, edges)
        mid = height / 2
        xs = np.linspace(0, width - 1, columns)
        # One polygon: the top edge left to right, then the bottom edge back.
        top = np.column_stack((xs, mid - np.clip(highs, -1, 1) * mid))
        bottom = np.column_stack((xs[::-1], mid - np.clip(lows[::-1], -1, 1) * mid))
        points = np.concatenate((top, bottom)).ravel().tolist()
        if columns == 1:
            canvas.create_line(0, points[1], 0, points[3], fill=resolve(COLOR_ORANGE))
        else:
            canvas.create_polygon(points, fill=resolve(COLOR_ORANGE), outline=resolve(COLOR_ORANGE))

        canvas.create_line(0, 0, 0, height, fill=resolve(COLOR_TEXT), tags="start_marker")
        canvas.create_line(0, 0, 0, height, fill=resolve(COLOR_TEXT), tags="end_marker")
        # Off-canvas until a preview moves it.
        canvas.create_line(-1, 0, -1, height, fill=resolve(COLOR_ERROR), width=2, tags="playhead")
        self._update_selection()

    def _update_selection(self):
        canvas = self.editor_canvas
        if self.editor_data is None or not canvas.find_withtag("selection"):
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            width, height = 480, 140
        duration = len(self.editor_data) / self.editor_samplerate
        start_x = self.editor_start_slider.get() / duration * width if duration else 0
        end_x = self.editor_end_slider.get() / duration * width if duration else width
        canvas.coords("selection", start_x, 0, end_x, height)
        canvas.coords("start_marker", start_x, 0, start_x, height)
        canvas.coords("end_marker", end_x, 0, end_x, height)

    def _get_editor_processed_data(self):
        start = self.editor_start_slider.get()
        end = self.editor_end_slider.get()
        start_sample = int(start * self.editor_samplerate)
        end_sample = int(end * self.editor_samplerate)
        trimmed = self.editor_data[start_sample:end_sample]
        if len(trimmed) == 0:
            raise ValueError("The selected part is empty. Move the Start and End sliders apart.")
        if self.editor_effect_toggles["reverse"].get():
            trimmed = reverse(trimmed)
        processed = apply_bass(trimmed, self.editor_bass_slider.get(), self.editor_samplerate)
        processed = self._apply_effects(processed)
        normalize = bool(self.editor_normalize_checkbox.get())
        if not normalize:
            processed = processed * (10 ** (self.editor_gain_slider.get() / 20))
        processed = self._apply_clipping(processed)
        processed = self._apply_fades(processed)
        # Echo comes after the fades, so the repeats are of the finished
        # clip and the tail dies away instead of being faded out mid-ring.
        processed = apply_echo(processed, self._effect("echo"), self.editor_samplerate)
        peak = float(np.abs(processed).max())
        # Normalize lifts the clip to SAVE_PEAK. Otherwise only "Keep clean"
        # brings it down; the other modes have already dealt with the
        # overshoot themselves, and undoing that would be the point missed.
        if peak > 0 and (normalize or peak > 1.0):
            processed = processed * (SAVE_PEAK / peak)
        # Pitch and speed go last, so the fades stay the same share of the
        # clip and the level set above survives.
        processed = self._apply_pitch_and_speed(processed)
        if len(processed) == 0:
            raise ValueError("The selected part is too short to stretch this far.")
        return processed

    def _apply_pitch_and_speed(self, data):
        semitones = self.editor_pitch_slider.get()
        octaves = self.editor_speed_slider.get()
        if self.editor_link_checkbox.get():
            # Tape mode: one resample moves pitch and length together, which
            # is both the effect asked for and the better-sounding way to
            # get it - no vocoder in the path at all.
            speed = speed_for_semitones(semitones)
            if speed == 1.0:
                return data
            return _resample(data, self.editor_samplerate * speed, self.editor_samplerate)
        # Apart: stretch first, then shift what came out. Resampling can only
        # bring the peak down, and a stretch rebuilds the same partials, so
        # neither undoes the level set above.
        data = time_stretch(data, 2 ** octaves)
        return pitch_shift(data, semitones, self.editor_samplerate)

    def _apply_effects(self, data):
        """Tone first, then the things that reshape the waveform, then the
        ones that rearrange it. Echo is the exception and runs at the end
        of the whole chain, once the fades are in."""
        rate = self.editor_samplerate
        data = apply_treble(data, self._effect("treble"), rate)
        data = apply_mid(data, self._effect("mid"), rate)
        if self.editor_effect_toggles["telephone"].get():
            data = apply_telephone(data, rate)
        data = apply_drive(data, self._effect("drive"))
        data = apply_robot(data, self._effect("robot"), rate)
        data = apply_width(data, self._effect("width"))
        return apply_stutter(data, self._effect("stutter"), rate)

    def _apply_clipping(self, data):
        """What to do with anything past full scale. "Keep clean" leaves it
        for the peak guard below to scale down; the other two shape it here,
        before the fades, so a fade-out lowers the distorted clip rather
        than being distorted itself."""
        mode = self.editor_clip_mode.get()
        if mode == CLIP_HARD:
            return np.clip(data, -1.0, 1.0)
        if mode == CLIP_SOFT:
            # tanh is what a distortion pedal does: it bends the loud parts
            # over instead of shearing them off, so it stays musical.
            return np.tanh(data).astype(np.float32)
        return data

    def _apply_fades(self, data):
        fade_in = min(int(self.editor_fade_in_slider.get() * self.editor_samplerate), len(data))
        fade_out = min(int(self.editor_fade_out_slider.get() * self.editor_samplerate), len(data))
        if not fade_in and not fade_out:
            return data
        faded = data.copy()
        if fade_in:
            faded[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
        if fade_out:
            faded[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)[:, None]
        return faded

    def _on_editor_preview(self):
        if self.editor_data is None:
            return
        try:
            processed = self._get_editor_processed_data()
            self.audio_engine.play_data(processed, self.editor_samplerate, key=EDITOR_PREVIEW_KEY)
        except Exception as e:
            error(self.root, "Preview error", str(e))
            return
        self._track_playhead()

    def _track_playhead(self):
        """Follow the preview across the selection until it stops."""
        if self._playhead_poll is not None:
            self.root.after_cancel(self._playhead_poll)
            self._playhead_poll = None
        canvas = self.editor_canvas
        if not canvas.find_withtag("playhead"):
            return
        progress = self.audio_engine.playback_progress(EDITOR_PREVIEW_KEY)
        if progress is None or self.editor_data is None:
            canvas.coords("playhead", -1, 0, -1, 0)
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            width, height = 480, 140
        duration = len(self.editor_data) / self.editor_samplerate
        start_x = self.editor_start_slider.get() / duration * width if duration else 0
        end_x = self.editor_end_slider.get() / duration * width if duration else width
        x = start_x + (end_x - start_x) * progress
        canvas.coords("playhead", x, 0, x, height)
        self._playhead_poll = self.root.after(PLAYHEAD_POLL_MS, self._track_playhead)

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
            error(self.root, "Save error", str(e))
            return
        self._add_sound_entry(name, os.path.basename(dest))
        info(self.root, "Saved", f"Saved and added to your board as '{name}'.")
