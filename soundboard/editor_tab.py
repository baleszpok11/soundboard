"""The Sound Editor tab: trim a clip, adjust its bass, save it as a new sound."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import soundfile as sf

from .audio_engine import _resample, apply_bass
from .config import (
    SOUNDS_DIR,
    ensure_sounds_dir,
    resolve_sound_path,
    sanitize_filename,
    unique_path,
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

MIN_CLIP_S = 0.05  # shortest clip the editor can trim to
SAVE_PEAK = 0.99  # edited clips louder than full scale are scaled down to this
EDITOR_PLACEHOLDER = "Choose a sound..."
EDITOR_PREVIEW_KEY = "editor-preview"
GAIN_RANGE_DB = 24
FADE_MAX_S = 5.0
PLAYHEAD_POLL_MS = 50
UNDO_SETTLE_MS = 400  # sliders fire continuously; one undo step per burst
PITCH_RANGE_ST = 12  # semitones either way, so the ends are exactly an octave


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
            button_color=COLOR_ORANGE,
            button_hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_ROW,
        )
        self.editor_sound_menu.grid(row=0, column=1, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(
            top, text="Browse...", width=90, command=self._on_editor_browse,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).grid(row=0, column=2, sticky="e", padx=8, pady=8)

        self.editor_canvas = tk.Canvas(parent, height=140, bg=COLOR_ROW, highlightthickness=0)
        self.editor_canvas.pack(fill="x", padx=4, pady=4)
        self.editor_canvas.bind("<Configure>", lambda e: self._draw_waveform())

        trim_frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        trim_frame.pack(fill="x", padx=4, pady=4)
        trim_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(trim_frame, text="Start:", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.editor_start_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=lambda v: self._on_trim_change(moved="start"),
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_start_slider.set(0)
        self.editor_start_slider.grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        self.editor_start_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_start_label.grid(row=0, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="End:", text_color=COLOR_TEXT).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.editor_end_slider = ctk.CTkSlider(
            trim_frame, from_=0, to=1, command=lambda v: self._on_trim_change(moved="end"),
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_end_slider.set(1)
        self.editor_end_slider.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        self.editor_end_label = ctk.CTkLabel(trim_frame, text="0.00s", text_color=COLOR_TEXT, width=60)
        self.editor_end_label.grid(row=1, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Bass:", text_color=COLOR_TEXT).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.editor_bass_slider = ctk.CTkSlider(
            trim_frame, from_=-12, to=12, command=self._on_bass_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_bass_slider.set(0)
        self.editor_bass_slider.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        self.editor_bass_label = ctk.CTkLabel(trim_frame, text="+0 dB", text_color=COLOR_TEXT, width=60)
        self.editor_bass_label.grid(row=2, column=2, padx=8, pady=6)

        ctk.CTkLabel(trim_frame, text="Volume:", text_color=COLOR_TEXT).grid(row=3, column=0, sticky="w", padx=8, pady=6)
        self.editor_gain_slider = ctk.CTkSlider(
            trim_frame, from_=-GAIN_RANGE_DB, to=GAIN_RANGE_DB, command=self._on_gain_change,
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_gain_slider.set(0)
        self.editor_gain_slider.grid(row=3, column=1, sticky="ew", padx=8, pady=6)
        self.editor_gain_label = ctk.CTkLabel(trim_frame, text="+0 dB", text_color=COLOR_TEXT, width=60)
        self.editor_gain_label.grid(row=3, column=2, padx=8, pady=6)
        self.editor_normalize_checkbox = ctk.CTkCheckBox(
            trim_frame, text="Normalize", command=self._on_normalize_change,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_BG, text_color=COLOR_TEXT,
        )
        self.editor_normalize_checkbox.grid(row=3, column=3, sticky="w", padx=(0, 8), pady=6)

        fades = []
        for row, text in ((4, "Fade in:"), (5, "Fade out:")):
            ctk.CTkLabel(trim_frame, text=text, text_color=COLOR_TEXT).grid(
                row=row, column=0, sticky="w", padx=8, pady=6
            )
            slider = ctk.CTkSlider(
                trim_frame, from_=0, to=FADE_MAX_S, command=lambda v: self._on_fade_change(),
                fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
                button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
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
            fg_color=COLOR_ROW, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        self.editor_pitch_slider.set(0)
        self.editor_pitch_slider.grid(row=6, column=1, sticky="ew", padx=8, pady=6)
        self.editor_pitch_label = ctk.CTkLabel(
            trim_frame, text=self._pitch_text(0), text_color=COLOR_TEXT, width=60,
        )
        self.editor_pitch_label.grid(row=6, column=2, padx=8, pady=6)

        buttons = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        buttons.pack(fill="x", padx=4, pady=(4, 8))
        self.editor_preview_button = ctk.CTkButton(
            buttons, text="Preview", command=self._on_editor_preview,
            hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG, text_color_disabled=COLOR_TEXT_DIM,
        )
        self.editor_preview_button.pack(side="left", padx=(4, 4))
        self.editor_stop_button = ctk.CTkButton(
            buttons, text="Stop", command=lambda: self.audio_engine.stop_key(EDITOR_PREVIEW_KEY),
            hover_color="#cc4444", text_color=COLOR_BG, text_color_disabled=COLOR_TEXT_DIM,
        )
        self.editor_stop_button.pack(side="left", padx=(4, 4))
        self.editor_undo_button = ctk.CTkButton(
            buttons, text="Undo", command=self._on_editor_undo,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            text_color_disabled=COLOR_TEXT_DIM, border_width=1,
        )
        self.editor_undo_button.pack(side="left", padx=(4, 4))
        self.editor_save_button = ctk.CTkButton(
            buttons, text="Save as new sound", command=self._on_editor_save,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            text_color_disabled=COLOR_TEXT_DIM, border_width=1,
        )
        self.editor_save_button.pack(side="left", padx=(4, 4))
        self._set_editor_enabled(False)

        self._refresh_editor_sound_list()

    def _refresh_editor_sound_list(self):
        self._editor_sound_paths = {}
        names = []
        for sound in self.config["sounds"]:
            label = sound["name"]
            while label in self._editor_sound_paths:
                label = f"{label} ({sound['path']})"
            self._editor_sound_paths[label] = resolve_sound_path(sound["path"])
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
            messagebox.showerror("Could not load sound", str(e))
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
        for slider, label in ((self.editor_fade_in_slider, self.editor_fade_in_label),
                              (self.editor_fade_out_slider, self.editor_fade_out_label)):
            slider.set(0)
            label.configure(text="0.00s")
        self.editor_pitch_slider.set(0)
        self.editor_pitch_label.configure(text=self._pitch_text(0))
        self._editor_undo = []
        self._editor_committed = self._editor_state()
        self._update_undo_button()
        self._draw_waveform()

    def _set_editor_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        accent = COLOR_ORANGE if enabled else COLOR_TEXT_DIM
        for slider in (self.editor_start_slider, self.editor_end_slider, self.editor_bass_slider,
                       self.editor_fade_in_slider, self.editor_fade_out_slider,
                       self.editor_pitch_slider):
            slider.configure(state=state, button_color=accent, progress_color=accent)
        self.editor_normalize_checkbox.configure(state=state)
        self._set_gain_enabled(not self.editor_normalize_checkbox.get())
        self.editor_preview_button.configure(state=state, fg_color=COLOR_ORANGE if enabled else COLOR_ROW)
        self.editor_stop_button.configure(state=state, fg_color=COLOR_ERROR if enabled else COLOR_ROW)
        self.editor_save_button.configure(state=state, border_color=COLOR_ORANGE if enabled else COLOR_TEXT_DIM)
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
            border_color=COLOR_ORANGE if can_undo else COLOR_TEXT_DIM,
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

    def _on_pitch_change(self, value):
        self.editor_pitch_label.configure(text=self._pitch_text(value))
        self._record_editor_change()

    def _on_normalize_change(self):
        self._set_gain_enabled(not self.editor_normalize_checkbox.get())
        self._record_editor_change()

    def _on_fade_change(self):
        self._clamp_fades()
        self._record_editor_change()

    def _clamp_fades(self):
        """Both fades share the selection, so neither can take more than half."""
        limit = max(0.0, (self.editor_end_slider.get() - self.editor_start_slider.get()) / 2)
        for slider, label in ((self.editor_fade_in_slider, self.editor_fade_in_label),
                              (self.editor_fade_out_slider, self.editor_fade_out_label)):
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
        self.editor_start_label.configure(text=f"{state['start']:.2f}s")
        self.editor_end_label.configure(text=f"{state['end']:.2f}s")
        self.editor_bass_label.configure(text=f"{state['bass']:+.0f} dB")
        self.editor_gain_label.configure(text=f"{state['gain']:+.0f} dB")
        self.editor_fade_in_label.configure(text=f"{state['fade_in']:.2f}s")
        self.editor_fade_out_label.configure(text=f"{state['fade_out']:.2f}s")
        self.editor_pitch_label.configure(text=self._pitch_text(state["pitch"]))
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
                width / 2, height / 2, fill=COLOR_TEXT_DIM,
                text="Choose a sound above, or click Browse... to open any audio file.",
            )
            return
        canvas.create_rectangle(0, 0, 0, height, fill=COLOR_SURFACE, outline="", tags="selection")

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
            canvas.create_line(0, points[1], 0, points[3], fill=COLOR_ORANGE)
        else:
            canvas.create_polygon(points, fill=COLOR_ORANGE, outline=COLOR_ORANGE)

        canvas.create_line(0, 0, 0, height, fill=COLOR_TEXT, tags="start_marker")
        canvas.create_line(0, 0, 0, height, fill=COLOR_TEXT, tags="end_marker")
        # Off-canvas until a preview moves it.
        canvas.create_line(-1, 0, -1, height, fill=COLOR_ERROR, width=2, tags="playhead")
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
        processed = apply_bass(trimmed, self.editor_bass_slider.get(), self.editor_samplerate)
        normalize = bool(self.editor_normalize_checkbox.get())
        if not normalize:
            processed = processed * (10 ** (self.editor_gain_slider.get() / 20))
        processed = self._apply_fades(processed)
        peak = float(np.abs(processed).max())
        # Normalize lifts the clip to SAVE_PEAK; otherwise only bring it down,
        # since WAV files hard-clip anything past full scale.
        if peak > 0 and (normalize or peak > 1.0):
            processed = processed * (SAVE_PEAK / peak)
        # Pitch goes last, so the fades stay the same share of the clip and
        # the level set above survives: interpolating between two samples
        # can never exceed the larger of them, so the peak only falls.
        semitones = self.editor_pitch_slider.get()
        if semitones:
            speed = speed_for_semitones(semitones)
            processed = _resample(processed, self.editor_samplerate * speed, self.editor_samplerate)
            if len(processed) == 0:
                raise ValueError("The selected part is too short to pitch this far.")
        return processed

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
            messagebox.showerror("Preview error", str(e))
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
            messagebox.showerror("Save error", str(e))
            return
        self._add_sound_entry(name, os.path.basename(dest))
        messagebox.showinfo("Saved", f"Saved and added to your board as '{name}'.")
