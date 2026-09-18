"""
Soundboard - continuously mixes microphone input with triggered sound
clips and writes the result to a chosen output device (e.g. a virtual
audio cable), so sounds and voice reach Discord/games together as one
microphone. Includes a media downloader tab (yt-dlp) that saves clips
into the local Sounds/ folder, and a Sound Editor tab to trim clips and
adjust bass.

This file only starts the app. Everything else lives in the soundboard
package: app.py and its mixins (devices, sound_list, mic_hotkeys,
download_tab, editor_tab), over audio_engine.py, hotkeys.py, dialogs.py,
downloader.py and config.py.
"""

import sys
import tkinter as tk
import traceback

import customtkinter as ctk

from soundboard.app import Soundboard
from soundboard.audio_engine import PORTAUDIO_ERROR, sd
from soundboard.config import ICON_ICO_PATH, ICON_PATH, write_error_log
from soundboard.dialogs import handle_exception, show_error
from soundboard.mac_scroll import fix_trackpad_scrolling
from soundboard.win_window import (
    fit_to_monitor_on_move,
    fix_dpi_rescaling,
    is_windows,
    use_app_icon,
)


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
        show_error(root, "Soundboard", message)
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
        # These patch CustomTkinter's own classes, so they have to be in
        # place before the first window or scrollable frame is built.
        use_app_icon()
        fix_dpi_rescaling()
        fix_trackpad_scrolling()
        # The look is set in Soundboard.__init__, once the config has been
        # read: the appearance mode is a setting, and the platform tokens
        # need a window before they can ask Tk which fonts exist.
        root = ctk.CTk()
        # Soundboard swaps this for one that knows the config once it is up.
        root.report_callback_exception = lambda *exc: handle_exception(root, *exc)
        root.geometry("900x700")
        root.minsize(640, 480)
        try:
            if is_windows():
                root.iconbitmap(ICON_ICO_PATH)
            else:
                root.iconphoto(True, tk.PhotoImage(file=ICON_PATH))
        except tk.TclError:
            pass
        fit_to_monitor_on_move(root)
        board = Soundboard(root)
        if "--hidden" in sys.argv:
            board.hide_to_tray()  # started by the launch-at-login entry
        else:
            # CustomTkinter applies its own geometry after startup, so maximize afterwards.
            root.after(100, lambda: _fit_to_screen(root))
    except Exception:
        _show_startup_error(root)
        return
    root.mainloop()


if __name__ == "__main__":
    main()
