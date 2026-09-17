"""Small modal dialogs: a one-line text prompt, the hotkey recorder, the
error dialog and the bug report window."""

import threading
import tkinter as tk
import traceback
import webbrowser

import customtkinter as ctk

import bug_report
from config import write_error_log
from hotkeys import MODIFIER_ORDER, hotkey_part_for_key, modifier_for_keysym
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


class ErrorDialog(ctk.CTkToplevel):
    """Shows an error and offers to report it."""

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.title(title)
        self.configure(fg_color=COLOR_SURFACE)
        self.report = False
        ctk.CTkLabel(
            self, text=message, text_color=COLOR_TEXT, justify="left", anchor="w", wraplength=460,
        ).pack(anchor="w", padx=16, pady=(16, 8))
        buttons = ctk.CTkFrame(self, fg_color=COLOR_SURFACE)
        buttons.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(
            buttons, text="Close", width=90, command=self.destroy,
            fg_color=COLOR_ROW, hover_color=COLOR_BG, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Report bug", width=110, command=self._report,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="right", padx=(0, 6))
        self.transient(parent)
        self.after(50, self._focus)

    def _focus(self):
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            self.after(50, self._focus)

    def _report(self):
        self.report = True
        self.destroy()

    def wants_report(self):
        self.wait_window()
        return self.report


def show_error(parent, title, message, config=None, host_api=None):
    """Error dialog that can carry straight on into a bug report."""
    if ErrorDialog(parent, title, message).wants_report():
        ReportDialog(parent, config=config, host_api=host_api).wait_window()


def handle_exception(parent, exc_type, exc_value, exc_tb, config=None, host_api=None):
    """Windowed builds have no console, so show UI errors instead of
    losing them. Used as Tk's report_callback_exception."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log_path = write_error_log(text)
    details = f"\n\nDetails were saved to:\n{log_path}" if log_path else ""
    show_error(parent, "Unexpected error", f"{exc_value}{details}", config, host_api)


class ReportDialog(ctk.CTkToplevel):
    """Collects a description and shows the whole report before anything
    is sent."""

    def __init__(self, parent, config=None, host_api=None):
        super().__init__(parent)
        self.title("Report a bug")
        self.configure(fg_color=COLOR_SURFACE)
        self.geometry("620x620")
        self._error_text = bug_report.last_error()
        self._environment = bug_report.environment(config, host_api)
        self._sending = False

        ctk.CTkLabel(
            self, text="What were you doing when it went wrong?", text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=16, pady=(16, 4))
        self.description = ctk.CTkTextbox(
            self, height=90, fg_color=COLOR_ROW, text_color=COLOR_TEXT,
            border_color=COLOR_ORANGE, border_width=1,
        )
        self.description.pack(fill="x", padx=16)
        self.description.bind("<KeyRelease>", lambda e: self._refresh_preview())

        ctk.CTkLabel(
            self, text="Contact (optional, only if you want a reply):", text_color=COLOR_TEXT,
        ).pack(anchor="w", padx=16, pady=(10, 4))
        self.contact = ctk.CTkEntry(
            self, fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_ORANGE,
        )
        self.contact.pack(fill="x", padx=16)
        self.contact.bind("<KeyRelease>", lambda e: self._refresh_preview())

        ctk.CTkLabel(
            self,
            text=(
                "This is everything that would be sent. Nothing leaves your computer "
                "until you press Send. Your folder names are replaced with ~ and no "
                "sound file names are included."
            ),
            text_color=COLOR_TEXT_DIM, justify="left", anchor="w", wraplength=580,
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self.preview = ctk.CTkTextbox(
            self, fg_color=COLOR_ROW, text_color=COLOR_TEXT_DIM,
            border_color=COLOR_ROW, border_width=1,
        )
        self.preview.pack(fill="both", expand=True, padx=16)

        self.status = ctk.CTkLabel(self, text="", text_color=COLOR_TEXT, anchor="w", wraplength=580)
        self.status.pack(fill="x", padx=16, pady=(6, 0))

        buttons = ctk.CTkFrame(self, fg_color=COLOR_SURFACE)
        buttons.pack(fill="x", padx=16, pady=(6, 16))
        secondary = dict(
            fg_color=COLOR_ROW, hover_color=COLOR_BG, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        ctk.CTkButton(buttons, text="Cancel", width=90, command=self.destroy, **secondary).pack(side="right")
        self.send_button = ctk.CTkButton(
            buttons, text="Send", width=110, command=self._send,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        )
        self.send_button.pack(side="right", padx=(0, 6))
        ctk.CTkButton(buttons, text="Copy report", width=110, command=self._copy, **secondary).pack(side="left")

        self._refresh_preview()
        self.transient(parent)
        self.after(50, self._focus)

    def _focus(self):
        self.description.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            self.after(50, self._focus)

    def _body(self):
        return bug_report.build(
            self.description.get("1.0", "end"), self.contact.get(), self._error_text, self._environment
        )

    def _refresh_preview(self):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", self._body())
        self.preview.configure(state="disabled")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._body())
        self.status.configure(text="Report copied to your clipboard.", text_color=COLOR_ORANGE)

    def _send(self):
        if not self.description.get("1.0", "end").strip():
            self.status.configure(
                text="Please say what you were doing first.", text_color=COLOR_ERROR
            )
            return
        title = bug_report.title_for(self.description.get("1.0", "end"), self._error_text)
        body = self._body()
        if not bug_report.sending_available():
            self._open_in_browser(title, body)
            return
        self._sending = True
        self.send_button.configure(state="disabled")
        self.status.configure(text="Sending...", text_color=COLOR_TEXT)
        threading.Thread(target=self._send_worker, args=(title, body), daemon=True).start()

    def _send_worker(self, title, body):
        try:
            url = bug_report.send(title, body, self._error_text)
        except Exception as e:
            message = str(e)
            self.after(0, lambda: self._send_failed(title, body, message))
            return
        self.after(0, lambda: self._sent(url))

    def _sent(self, url):
        self._sending = False
        self.status.configure(text=f"Thank you. Your report is at {url}", text_color=COLOR_ORANGE)
        self.send_button.configure(state="disabled", text="Sent")
        if url:
            webbrowser.open(url)

    def _send_failed(self, title, body, error):
        self._sending = False
        self.send_button.configure(state="normal")
        self.status.configure(
            text=f"Could not send the report ({error}). Opening GitHub instead.",
            text_color=COLOR_ERROR,
        )
        self._open_in_browser(title, body)

    def _open_in_browser(self, title, body):
        """Without a relay, the report goes through GitHub's new issue page,
        which needs an account, so the full text goes to the clipboard too."""
        self._copy()
        webbrowser.open(bug_report.browser_url(title, body))
        self.status.configure(
            text=(
                "Opened GitHub's new issue page in your browser, with the report filled in. "
                "It is on your clipboard as well, in case anything is missing."
            ),
            text_color=COLOR_ORANGE,
        )
