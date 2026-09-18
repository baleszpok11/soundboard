"""The Speak tab: type a line, hear it through the soundboard, keep it
if it is worth a hotkey.

Synthesis runs on a worker thread - it takes a moment on a long line,
and pyttsx3 blocks while it works - and the result is played through the
audio engine like any other clip, so the soundboard volume, the limiter
and "Hear soundboard" all apply to it.
"""

import os
import threading

import customtkinter as ctk
import soundfile as sf

from . import speech
from .config import (
    SOUNDS_DIR,
    ensure_sounds_dir,
    sanitize_filename,
    unique_path,
)
from .dialogs import error
from .theme import (
    COLOR_BORDER,
    COLOR_ERROR_TEXT,
    COLOR_ON_ACCENT,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    wrap_to_width,
)

TTS_KEY = "speech"  # what the engine plays spoken lines under
NAME_LENGTH = 40  # how much of a line becomes the clip's name


class SpeechMixin:
    """Speak tab UI and its worker thread. Expects `root`, `config` and
    `audio_engine` from Soundboard, and `_add_sound_entry` from
    SoundListMixin."""

    def _build_tts_tab(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        self._tts_reason = speech.unavailable_reason()
        if self._tts_reason is not None:
            wrap_to_width(ctk.CTkLabel(
                frame, text=self._tts_reason, text_color=COLOR_ERROR_TEXT,
                justify="left", anchor="w",
            )).pack(fill="x", padx=8, pady=8)
            return

        ctk.CTkLabel(
            frame, text="Type a line to say through the soundboard:", text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=8, pady=(8, 2))
        self.tts_text = ctk.CTkTextbox(
            frame, height=90, fg_color=COLOR_ROW, text_color=COLOR_TEXT,
            border_color=COLOR_BORDER, border_width=1,
        )
        self.tts_text.pack(fill="x", padx=8)

        options = ctk.CTkFrame(frame, fg_color="transparent")
        options.pack(fill="x", padx=8, pady=8)
        self._tts_voices = speech.voices()
        names = [name for _id, name in self._tts_voices] or ["Default"]
        ctk.CTkLabel(options, text="Voice:", text_color=COLOR_TEXT).pack(side="left")
        self.tts_voice_var = ctk.StringVar(value=self._saved_voice_name(names))
        ctk.CTkOptionMenu(
            options, variable=self.tts_voice_var, values=names, width=180,
            command=self._on_tts_voice_change, fg_color=COLOR_ROW, text_color=COLOR_TEXT,
        ).pack(side="left", padx=(6, 16))
        ctk.CTkLabel(options, text="Speed:", text_color=COLOR_TEXT).pack(side="left")
        self.tts_rate_label = ctk.CTkLabel(
            options, text="", width=48, text_color=COLOR_TEXT_DIM)
        self.tts_rate_slider = ctk.CTkSlider(
            options, from_=speech.RATE_MIN, to=speech.RATE_MAX, width=160,
            command=self._on_tts_rate,
        )
        self.tts_rate_slider.set(self.config.get("tts_rate") or speech.default_rate())
        self.tts_rate_slider.pack(side="left", padx=(6, 0))
        self.tts_rate_label.pack(side="left", padx=(4, 0))
        self._on_tts_rate(self.tts_rate_slider.get())

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        self.tts_speak_button = ctk.CTkButton(
            buttons, text="Speak", width=110, command=self.speak_text,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        )
        self.tts_speak_button.pack(side="left")
        self.tts_save_button = ctk.CTkButton(
            buttons, text="Save to board", width=130, command=lambda: self.speak_text(save=True),
            fg_color=COLOR_ROW, hover_color=COLOR_BORDER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.tts_save_button.pack(side="left", padx=(8, 0))
        self.tts_status = ctk.CTkLabel(buttons, text="", text_color=COLOR_TEXT_DIM, anchor="w")
        self.tts_status.pack(side="left", padx=(12, 0))

    def _saved_voice_name(self, names):
        saved = self.config.get("tts_voice")
        for voice_id, name in self._tts_voices:
            if voice_id == saved:
                return name
        return names[0]

    def _on_tts_voice_change(self, name):
        for voice_id, other in self._tts_voices:
            if other == name:
                self.config["tts_voice"] = voice_id
                break
        self._save_config_soon()

    def _on_tts_rate(self, value):
        self.config["tts_rate"] = int(round(value))
        self.tts_rate_label.configure(text=str(self.config["tts_rate"]))
        self._save_config_soon()

    def _tts_voice_id(self):
        saved = self.config.get("tts_voice")
        return saved if any(voice_id == saved for voice_id, _ in self._tts_voices) else None

    def speak_text(self, save=False):
        """Say what is in the box, and keep it as a clip when asked."""
        text = self.tts_text.get("1.0", "end").strip()
        if not text:
            self.tts_status.configure(text="Type something first.")
            return
        self._set_tts_busy(True)
        self.tts_status.configure(text="Speaking..." if not save else "Saving...")
        threading.Thread(
            target=self._tts_worker,
            args=(text, self._tts_voice_id(), self.config.get("tts_rate"), save),
            daemon=True,
        ).start()

    def _tts_worker(self, text, voice_id, rate, save):
        path = None
        try:
            path = speech.temp_wav()
            speech.synthesize(text, path, voice_id, rate or speech.DEFAULT_RATE)
            if save:
                ensure_sounds_dir()
                name = text[:NAME_LENGTH].strip() or "Speech"
                dest = unique_path(os.path.join(SOUNDS_DIR, sanitize_filename(name + ".wav")))
                os.replace(path, dest)
                path = None
                self.root.after(0, self._tts_saved, name, dest)
            else:
                # Read it here and drop the file: a spoken line that isn't
                # kept shouldn't leave anything behind in the temp folder.
                data, samplerate = sf.read(path, dtype="float32", always_2d=True)
                self.root.after(0, self._tts_play, data, samplerate)
        except Exception as e:
            message = str(e)
            self.root.after(0, self._tts_failed, message)
        finally:
            if path is not None:
                _remove(path)

    def _tts_play(self, data, samplerate):
        self._set_tts_busy(False)
        self.tts_status.configure(text="")
        try:
            self.audio_engine.play_data(data, samplerate, key=TTS_KEY)
        except RuntimeError as e:
            error(self.root, "Could not speak", str(e))

    def _tts_saved(self, name, dest):
        self._set_tts_busy(False)
        self.tts_status.configure(text=f"Added '{name}' to your board.")
        self._add_sound_entry(name, os.path.basename(dest))

    def _tts_failed(self, message):
        self._set_tts_busy(False)
        self.tts_status.configure(text="")
        error(self.root, "Could not speak", message)

    def _set_tts_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.tts_speak_button.configure(state=state)
        self.tts_save_button.configure(state=state)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
