"""Trackpad scrolling on macOS with Tcl/Tk 9.

Tk 9 stopped reporting two-finger trackpad gestures on Aqua as
<MouseWheel> and sends the new <TouchpadScroll> event instead, which
carries a precise pixel delta per gesture step rather than one notch at
a time. CustomTkinter 6.0.0 predates it and only binds <MouseWheel>, so
a CTkScrollableFrame could only be moved by dragging its scrollbar -
the sound list, the editor's controls and the help window all sat still
under a two-finger scroll. Tk 9 does bind the new event for Text and
Listbox itself, so only the canvas a scrollable frame is built on needs
this.

The deltas arrive in pixels, so the canvas is moved by pixels here
rather than by the scroll increment a wheel notch uses; scrolling
otherwise flies off at about thirty times the speed of the gesture.

Everything here is a no-op off macOS, and on a Tk that has no
<TouchpadScroll> to bind.
"""

import sys

import customtkinter as ctk


def is_macos():
    return sys.platform == "darwin"


def fix_trackpad_scrolling():
    """Let CTkScrollableFrame answer the trackpad as well as the wheel."""
    if not is_macos():
        return
    original_init = ctk.CTkScrollableFrame.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            # add="+" so CustomTkinter's own bindings stay.
            self.bind_all("<TouchpadScroll>", lambda e: _on_scroll(self, e), add="+")
        except Exception:
            pass  # a Tk that predates the event; the wheel still works

    ctk.CTkScrollableFrame.__init__ = __init__


def _on_scroll(frame, event):
    try:
        if not frame._check_if_valid_scroll(event.widget):
            return
        canvas = frame._parent_canvas
    except Exception:
        return  # the frame went away between the gesture and the callback
    delta_x, delta_y = _deltas(canvas, event)
    # Shift turns a vertical gesture sideways, the way it does for the
    # wheel; a trackpad can also scroll sideways on its own.
    if getattr(frame, "_shift_pressed", False) and delta_y and not delta_x:
        delta_x, delta_y = delta_y, 0
    if delta_y:
        _scroll_pixels(canvas, canvas.yview, canvas.yview_moveto, 1, 3, -delta_y)
    if delta_x:
        _scroll_pixels(canvas, canvas.xview, canvas.xview_moveto, 0, 2, -delta_x)


def _deltas(widget, event):
    """The (x, y) pixel deltas packed into the event's delta field. Tk
    ships the unpacking, since the halves are signed and the sign is
    easy to get wrong by hand."""
    try:
        x, y = widget.tk.call("tk::PreciseScrollDeltas", event.delta)
        return int(x), int(y)
    except Exception:
        packed = int(getattr(event, "delta", 0))
        x = packed & 0xFFFF
        y = (packed >> 16) & 0xFFFF
        return (x - 0x10000 if x > 0x7FFF else x,
                y - 0x10000 if y > 0x7FFF else y)


def _scroll_pixels(canvas, view, move_to, low, high, pixels):
    """Move the canvas by pixels. yview_scroll works in units of the
    canvas's scroll increment, which is a wheel notch, so the fraction
    is worked out from the scroll region instead."""
    try:
        first, last = view()
        if (first, last) == (0.0, 1.0):
            return  # nothing to scroll in this direction
        box = canvas.bbox("all")
        if not box:
            return
        span = box[high] - box[low]
        if span <= 0:
            return
        move_to(min(1.0, max(0.0, first + pixels / span)))
    except Exception:
        pass  # a canvas destroyed mid-gesture is not worth an error dialog
