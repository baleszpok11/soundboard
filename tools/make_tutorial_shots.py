"""Regenerate the setup tutorial's screenshots.

    xvfb-run -a -s "-screen 0 1100x800x24" python tools/make_tutorial_shots.py

Every shot is taken from a real Soundboard window with the state the
tutorial describes, then cropped to the widgets being talked about - the
crop follows widget geometry rather than fixed pixels, so moving a
control moves the crop with it. Re-run this after a UI change instead of
discovering months later that the help shows an app that no longer
exists.

Nothing here touches your own config or Sounds/: the app is pointed at a
temporary directory first.
"""

import os
import sys
import tempfile

from PIL import Image, ImageGrab

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_WORK = tempfile.mkdtemp(prefix="tutorial-shots-")
from soundboard import config  # noqa: E402  (must be redirected before anything reads it)

config.APP_DIR = _WORK
config.CONFIG_PATH = os.path.join(_WORK, "soundboard_config.json")
config.SOUNDS_DIR = os.path.join(_WORK, "Sounds")
os.makedirs(config.SOUNDS_DIR, exist_ok=True)

import customtkinter as ctk  # noqa: E402
from soundboard.app import Soundboard  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "tutorial")
PAD = 10
WINDOW = "900x800"
# Narrow enough that the warning fills its own width instead of
# wrapping halfway across an empty label.
WARNING_WINDOW = "820x800"

# Device names as each platform actually spells them.
DEVICES = {
    "windows": ("Microphone (Realtek High Definition Audio)",
                "CABLE Input (VB-Audio Virtual Cable)"),
    "macos": ("MacBook Pro Microphone", "BlackHole 2ch"),
    "linux": ("pulse", "pulse"),
}
LOOPBACK = "Stereo Mix (Realtek High Definition Audio)"


def bbox(*widgets, pad=PAD):
    """The rectangle covering these widgets, in screen coordinates."""
    left = min(w.winfo_rootx() for w in widgets)
    top = min(w.winfo_rooty() for w in widgets)
    right = max(w.winfo_rootx() + w.winfo_width() for w in widgets)
    bottom = max(w.winfo_rooty() + w.winfo_height() for w in widgets)
    return (left - pad, top - pad, right + pad, bottom + pad)


def shoot(root, name, *widgets, pad=PAD):
    root.update_idletasks()
    root.update()
    box = bbox(*widgets, pad=pad)
    image = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY")).crop(box)
    if image.size[0] < 40 or image.size[1] < 20:
        raise SystemExit(f"{name}: cropped to {image.size}, which is not a screenshot")
    if len(image.getcolors(maxcolors=1 << 16) or [(0, 0)]) < 3:
        raise SystemExit(f"{name}: the crop is a flat colour, so the window was not drawn")
    path = os.path.join(OUT_DIR, name + ".png")
    image.save(path, optimize=True)
    print(f"{name}.png  {image.size[0]}x{image.size[1]}  {os.path.getsize(path) // 1024} KB")
    return path


def set_devices(board, mic, cable):
    board.input_devices = [(0, mic), (1, LOOPBACK)]
    board.output_devices = [(0, cable), (1, "Speakers (Realtek High Definition Audio)")]
    board.input_menu.configure(values=[name for _, name in board.input_devices])
    board.output_menu.configure(values=[name for _, name in board.output_devices])
    board.input_var.set(mic)
    board.output_var.set(cable)
    board.config["input_device"] = mic
    board.config["output_device"] = cable


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    root.geometry(WINDOW)
    board = Soundboard(root)
    # The meters are polled on a timer that would wipe the level set below.
    if board._meter_poll is not None:
        root.after_cancel(board._meter_poll)
        board._meter_poll = None
    root.update()

    # The row labels say which dropdown is which, so they belong in the
    # crop. They are laid out by grid and not kept on the app.
    frame = board.input_menu.master.master
    mic_label = frame.grid_slaves(row=0, column=0)[0]
    output_label = frame.grid_slaves(row=1, column=0)[0]
    output_side = board.hear_self_checkbox.master

    device_rows = (mic_label, output_label, board.input_menu,
                   board.output_menu, board.output_meter)
    for platform_name, (mic, cable) in DEVICES.items():
        set_devices(board, mic, cable)
        board._update_meter(board.input_meter, 0.0, True, True)
        board._update_meter(board.output_meter, 0.0, True, True)
        shoot(root, f"devices-{platform_name}", *device_rows)

    # The Test checkpoint: the output meter mid-tone, in the app's own
    # colours rather than ones picked here.
    set_devices(board, *DEVICES["windows"])
    board._update_meter(board.output_meter, 0.62, True, True)
    shoot(root, "test", output_label, board.output_menu, board.output_meter, output_side)

    # The warning the app raises by itself when the microphone is a
    # loopback device, which is what makes people hear themselves back.
    root.geometry(WARNING_WINDOW)
    board.config["input_device"] = LOOPBACK
    board.input_var.set(LOOPBACK)
    board._update_device_warnings()
    root.update_idletasks()
    root.update()
    if not board.loop_warning.cget("text"):
        raise SystemExit("the feedback warning did not appear; has the check moved?")
    shoot(root, "feedback", board.loop_warning, pad=3)

    root.destroy()


if __name__ == "__main__":
    main()
