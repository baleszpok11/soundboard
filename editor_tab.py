"""The Sound Editor tab: trim a clip, adjust its bass, save it as a new sound."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import soundfile as sf

from audio_engine import apply_bass
from config import (
    SOUNDS_DIR,
    ensure_sounds_dir,
    resolve_sound_path,
    sanitize_filename,
    unique_path,
)
from theme import (
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
        self._draw_waveform()

    def _set_editor_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        accent = COLOR_ORANGE if enabled else COLOR_TEXT_DIM
        for slider in (self.editor_start_slider, self.editor_end_slider, self.editor_bass_slider):
            slider.configure(state=state, button_color=accent, progress_color=accent)
        self.editor_preview_button.configure(state=state, fg_color=COLOR_ORANGE if enabled else COLOR_ROW)
        self.editor_stop_button.configure(state=state, fg_color=COLOR_ERROR if enabled else COLOR_ROW)
        self.editor_save_button.configure(state=state, border_color=COLOR_ORANGE if enabled else COLOR_TEXT_DIM)

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
        self._update_selection()

    def _on_bass_change(self, value):
        self.editor_bass_label.configure(text=f"{value:+.0f} dB")

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
        gain_db = self.editor_bass_slider.get()
        processed = apply_bass(trimmed, gain_db, self.editor_samplerate)
        # WAV files hard-clip anything past full scale, so scale down instead.
        peak = float(np.abs(processed).max())
        if peak > 1.0:
            processed = processed * (SAVE_PEAK / peak)
        return processed

    def _on_editor_preview(self):
        if self.editor_data is None:
            return
        try:
            processed = self._get_editor_processed_data()
            self.audio_engine.play_data(processed, self.editor_samplerate, key=EDITOR_PREVIEW_KEY)
        except Exception as e:
            messagebox.showerror("Preview error", str(e))

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
