"""
Soundboard - continuously mixes microphone input with triggered sound
clips and writes the result to a chosen output device (e.g. a virtual
audio cable), so sounds and voice reach Discord/games together as one
microphone. Includes a media downloader tab (yt-dlp) that saves clips
into the local Sounds/ folder, and a Sound Editor tab to trim clips and
adjust bass.

This file only starts the app; the UI lives in app.py and its mixins
(devices, sound_list, mic_hotkeys, download_tab, editor_tab), with
audio_engine.py, hotkeys.py, dialogs.py, downloader.py and config.py
underneath.
"""

import sys
import tkinter as tk
import traceback
from tkinter import messagebox

import customtkinter as ctk

from app import Soundboard
from audio_engine import PORTAUDIO_ERROR, sd
from config import ICON_PATH, write_error_log


def _report_callback_exception(exc_type, exc_value, exc_tb):
    """Windowed builds have no console, so show UI errors instead of
    losing them."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log_path = write_error_log(text)
    details = f"\n\nDetails were saved to:\n{log_path}" if log_path else ""
    messagebox.showerror("Unexpected error", f"{exc_value}{details}")


def _show_startup_error(root):
    text = traceback.format_exc()
    log_path = write_error_log(text)
    error = sys.exc_info()[1]
    message = f"Soundboard could not start:\n\n{error}"
    if isinstance(error, PermissionError):
        message += (
            "\n\nSoundboard saves its settings and sounds next to the app. "
            "Move it to a folder you can write to, such as Documents."
        )
    if log_path:
        message += f"\n\nDetails were saved to:\n{log_path}"
    try:
        if root is None:
            root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Soundboard", message, parent=root)
        root.destroy()
    except Exception:
        pass


def _fit_to_screen(root):
    try:
        if sys.platform.startswith("linux"):
            root.attributes("-zoomed", True)
        else:
            root.state("zoomed")
    except tk.TclError:
        root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")


def main():
    root = None
    try:
        if sd is None:
            raise RuntimeError(
                f"The PortAudio library is missing ({PORTAUDIO_ERROR}).\n\n"
                "Install it and start Soundboard again:\n"
                "Debian/Ubuntu: sudo apt install libportaudio2\n"
                "Fedora: sudo dnf install portaudio\n"
                "Arch: sudo pacman -S portaudio"
            )
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        root = ctk.CTk()
        root.report_callback_exception = _report_callback_exception
        root.geometry("900x700")
        root.minsize(640, 480)
        try:
            root.iconphoto(True, tk.PhotoImage(file=ICON_PATH))
        except tk.TclError:
            pass
        Soundboard(root)
        # CustomTkinter applies its own geometry after startup, so maximize afterwards.
        root.after(100, lambda: _fit_to_screen(root))
    except Exception:
        _show_startup_error(root)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
