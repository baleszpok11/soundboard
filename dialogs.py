"""Small modal dialogs: a one-line text prompt and the hotkey recorder."""

import tkinter as tk

import customtkinter as ctk

from hotkeys import MODIFIER_ORDER, hotkey_part_for_key, modifier_for_keysym
from theme import (
    COLOR_BG,
    COLOR_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_SURFACE,
    COLOR_TEXT,
)


class TextDialog(ctk.CTkToplevel):
    """Asks for one line of text, prefilled. `get()` returns the stripped
    text, or None when cancelled."""

    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent)
        self.title(title)
        self.configure(fg_color=COLOR_SURFACE)
        self.resizable(False, False)
        self.result = None
        ctk.CTkLabel(self, text=prompt, text_color=COLOR_TEXT).pack(anchor="w", padx=16, pady=(16, 6))
        self.entry = ctk.CTkEntry(
            self, width=360, fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_ORANGE,
        )
        self.entry.pack(fill="x", padx=16)
        self.entry.insert(0, initial)
        self.entry.select_range(0, "end")
        buttons = ctk.CTkFrame(self, fg_color=COLOR_SURFACE)
        buttons.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(
            buttons, text="Cancel", width=90, command=self.destroy,
            fg_color=COLOR_ROW, hover_color=COLOR_BG, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="OK", width=90, command=self._ok,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="right", padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(parent)
        self.after(50, self._focus)

    def _focus(self):
        self.entry.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            self.after(50, self._focus)

    def _ok(self):
        self.result = self.entry.get().strip()
        self.destroy()

    def get(self):
        self.wait_window()
        return self.result


class HotkeyDialog(ctk.CTkToplevel):
    """Records a hotkey by pressing it, or lets it be typed. `result` is the
    hotkey text, "" to clear, or None when cancelled."""

    def __init__(self, parent, title, current):
        super().__init__(parent)
        self.title(title)
        self.configure(fg_color=COLOR_SURFACE)
        self.resizable(False, False)
        self.result = None
        self._held = set()
        self._recording = False

        self.prompt = ctk.CTkLabel(self, text="", text_color=COLOR_TEXT, justify="left")
        self.prompt.pack(anchor="w", padx=16, pady=(16, 6))
        self.entry = ctk.CTkEntry(
            self, width=320, fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_ORANGE,
        )
        self.entry.pack(fill="x", padx=16)
        if current:
            self.entry.insert(0, current)
        self.error = ctk.CTkLabel(self, text="", text_color=COLOR_ERROR, justify="left", wraplength=320)
        self.error.pack(anchor="w", padx=16, pady=(4, 0))

        buttons = ctk.CTkFrame(self, fg_color=COLOR_SURFACE)
        buttons.pack(fill="x", padx=16, pady=(8, 16))
        secondary = dict(
            fg_color=COLOR_ROW, hover_color=COLOR_BG, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE, width=90,
        )
        self.record_button = ctk.CTkButton(buttons, text="Record again", command=self._start_recording, **secondary)
        self.record_button.pack(side="left")
        ctk.CTkButton(buttons, text="Clear", command=self._clear, **secondary).pack(side="left", padx=(6, 0))
        ctk.CTkButton(buttons, text="Cancel", command=self.destroy, **secondary).pack(side="right")
        ctk.CTkButton(
            buttons, text="Save", width=90, command=self._save,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="right", padx=(0, 6))

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<KeyRelease>", self._on_key_release)
        self.entry.bind("<FocusIn>", lambda e: self._stop_recording(restore=True))
        self.entry.bind("<Return>", lambda e: self._save())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.transient(parent)
        self.after(50, self._start_recording)

    def _start_recording(self):
        if not self._recording:
            self._before = self.entry.get()
        self._recording = True
        self._held.clear()
        self.error.configure(text="")
        self.prompt.configure(
            text="Press the key combination now (e.g. Ctrl+Alt+1).\nEsc stops recording; you can also type it below."
        )
        self.record_button.configure(state="disabled")
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            self.after(50, self._start_recording)  # not visible yet

    def _stop_recording(self, restore=False):
        if restore and self._recording:
            self._show(self._before)  # drop a half-pressed combo
        self._recording = False
        self._held.clear()
        self.prompt.configure(text="Hotkey (e.g. <ctrl>+<alt>+1). Leave empty to clear.")
        self.record_button.configure(state="normal")

    def _on_key_press(self, event):
        if not self._recording:
            return None
        if event.keysym == "Escape":
            self._stop_recording(restore=True)
            return "break"
        modifier = modifier_for_keysym(event.keysym)
        if modifier:
            self._held.add(modifier)
            self._show(self._combo())
            return "break"
        # Shift and Control state bits are the same on every platform.
        if event.state & 0x1:
            self._held.add("shift")
        if event.state & 0x4:
            self._held.add("ctrl")
        part = hotkey_part_for_key(event.keysym, event.keycode)
        if part is None:
            self.error.configure(text="That key can't be used in a hotkey. Try another one.")
            return "break"
        if not self._held and not part.startswith("<"):
            self.error.configure(text="Add a modifier (Ctrl, Alt, ...) so the key doesn't fire while you type.")
            return "break"
        self._show(self._combo(part))
        self.error.configure(text="")
        self._stop_recording()
        return "break"

    def _on_key_release(self, event):
        modifier = modifier_for_keysym(event.keysym)
        if self._recording and modifier:
            self._held.discard(modifier)
            return "break"
        return None

    def _combo(self, key=None):
        parts = [f"<{m}>" for m in MODIFIER_ORDER if m in self._held]
        if key:
            parts.append(key)
        return "+".join(parts)

    def _show(self, text):
        self.entry.delete(0, "end")
        self.entry.insert(0, text)

    def _clear(self):
        self._recording = False
        self._stop_recording()
        self._show("")

    def _save(self):
        self.result = self.entry.get().strip()
        self.destroy()

    def get(self):
        self.wait_window()
        return self.result
