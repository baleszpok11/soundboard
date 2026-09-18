"""Windows-only window behaviour.

Two things CustomTkinter does to every window it creates are wrong for
this app, and both are fixed here rather than in each window:

- 200 ms after a window is created it replaces the icon with its own,
  unless iconbitmap() was called first. iconphoto(), which is what Tk
  documents and what Linux and macOS want, does not set the flag it
  looks at, so the app ends up wearing CustomTkinter's icon.
- when a window lands on a monitor with a different DPI, the rescale is
  meant to ignore the resize events it causes itself, but the method
  that sets that flag assigns False instead of True. The window's
  remembered size is then overwritten mid-rescale and the window is
  resized to a size that belonged to the monitor it came from. Moving
  with Win+Shift+Arrow does the whole move in one step, which is why a
  keyboard move breaks where a slow drag usually survives.

Everything here is a no-op off Windows.
"""

import sys

import customtkinter as ctk

from .config import ICON_ICO_PATH

# CustomTkinter forces min and max size to the new scaled size and only
# puts the real ones back a second later; fitting the window to its
# monitor before that would be undone.
FIT_DELAY_MS = 1200
_WINDOW_CLASSES = (ctk.CTk, ctk.CTkToplevel)


def is_windows():
    return sys.platform.startswith("win")


def use_app_icon():
    """Point CustomTkinter's own icon call at our icon, so every window -
    the board, the dialogs, the tutorial, and any window added later -
    gets it at the moment CustomTkinter would have replaced it."""
    if not is_windows():
        return

    def set_icon(window):
        try:
            window.iconbitmap(ICON_ICO_PATH)
        except Exception:
            pass  # a missing or unreadable .ico is not worth a dialog

    for cls in _WINDOW_CLASSES:
        cls._windows_set_titlebar_icon = set_icon


def fix_dpi_rescaling():
    """Make CustomTkinter's "ignore the resizes I am about to cause"
    flag do that, instead of clearing itself."""
    if not is_windows():
        return

    def block(window):
        window._block_update_dimensions_event = True

    def unblock(window):
        window._block_update_dimensions_event = False

    for cls in _WINDOW_CLASSES:
        cls.block_update_dimensions_event = block
        cls.unblock_update_dimensions_event = unblock


def fit_to_monitor_on_move(window):
    """Fit the window to the monitor it is on once it stops moving. A
    rescale that still goes wrong then costs a resize, not a window
    too big to use."""
    if not is_windows():
        return
    pending = {"job": None}

    def on_configure(_event=None):
        if pending["job"] is not None:
            try:
                window.after_cancel(pending["job"])
            except Exception:
                pass
        pending["job"] = window.after(FIT_DELAY_MS, fit)

    def fit():
        pending["job"] = None
        try:
            _fit_to_monitor(window)
        except Exception:
            pass  # the window may be gone, or not on a monitor we can read

    window.bind("<Configure>", on_configure, add="+")


def _fit_to_monitor(window):
    if not window.winfo_exists():
        return
    state = window.state()
    if state == "iconic" or state == "withdrawn":
        return
    left, top, width, height = _work_area(window.winfo_id())
    if window.winfo_width() <= width and window.winfo_height() <= height:
        return
    if state == "zoomed":
        # Windows sizes a maximized window itself; ask it to do that
        # again now that the window is on this monitor.
        window.state("normal")
        window.state("zoomed")
        return
    # wm_ methods, not the CustomTkinter wrappers: these are real pixels
    # on this monitor, not numbers waiting to be scaled.
    min_width, min_height = window.wm_minsize()
    window.wm_minsize(min(min_width, width), min(min_height, height))
    new_width, new_height = min(window.winfo_width(), width), min(window.winfo_height(), height)
    x = min(max(window.winfo_x(), left), left + width - new_width)
    y = min(max(window.winfo_y(), top), top + height - new_height)
    window.wm_geometry(f"{new_width}x{new_height}+{x}+{y}")


def _work_area(window_id):
    """The current monitor's usable rectangle, taskbar excluded."""
    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(wintypes.HWND(window_id), 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("GetMonitorInfoW failed")
    work = info.rcWork
    return work.left, work.top, work.right - work.left, work.bottom - work.top
