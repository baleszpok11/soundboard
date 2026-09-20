"""Regenerate the setup tutorial's screenshots.

    xvfb-run -a -s "-screen 0 1100x800x24" python tools/make_tutorial_shots.py

Each platform's shots are taken with that platform's look forced, in a
process of its own, because the app now draws itself differently on each
one and a Linux-rendered window is not what a Mac user is following.

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
import subprocess
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
from soundboard.theme import set_appearance  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "tutorial")
PAD = 10
WINDOW = "900x800"
# The shots in assets/ are two pixels per point, because they were taken
# on a Retina screen, and the help draws them scaled down: a one-to-one
# shot next to them is visibly soft on the same screen. On a display that
# has no scaling of its own, SOUNDBOARD_SHOT_SCALE=2 draws the app twice
# the size instead, which is the same pixels rather than an upscale. It
# needs a screen to fit the bigger window: xvfb-run -s "-screen 0
# 2000x1800x24". Leave it alone on a Retina Mac, which is already 2x.
SCALE = float(os.environ.get("SOUNDBOARD_SHOT_SCALE", "1"))
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
# An input that carries what the PC plays rather than a real microphone,
# as each platform spells it. devices.LOOPBACK_INPUT_HINTS has to match
# these or the warning never appears and the shot cannot be taken.
LOOPBACK = {
    "windows": "Stereo Mix (Realtek High Definition Audio)",
    "macos": "BlackHole 2ch",
    "linux": "Monitor of Built-in Audio Analog Stereo",
}


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
    screen = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY"))
    # Tk reports geometry in points and the grab comes back in pixels,
    # which are the same thing on X11 and not on a Retina screen: there
    # the crop would land in the top left quarter of the window.
    scale = screen.width / root.winfo_screenwidth()
    if scale != 1:
        box = tuple(round(edge * scale) for edge in box)
    image = screen.crop(box)
    if image.size[0] < 40 or image.size[1] < 20:
        raise SystemExit(f"{name}: cropped to {image.size}, which is not a screenshot")
    if len(image.getcolors(maxcolors=1 << 16) or [(0, 0)]) < 3:
        raise SystemExit(f"{name}: the crop is a flat colour, so the window was not drawn")
    path = os.path.join(OUT_DIR, name + ".png")
    image.save(path, optimize=True)
    print(f"{name}.png  {image.size[0]}x{image.size[1]}  {os.path.getsize(path) // 1024} KB")
    return path


def set_devices(board, mic, cable, loopback):
    board.input_devices = [(0, mic), (1, loopback)]
    board.output_devices = [(0, cable), (1, "Speakers (Realtek High Definition Audio)")]
    board.input_menu.configure(values=[name for _, name in board.input_devices])
    board.output_menu.configure(values=[name for _, name in board.output_devices])
    board.input_var.set(mic)
    board.output_var.set(cable)
    board.config["input_device"] = mic
    board.config["output_device"] = cable


def run_platform(platform_name):
    """Re-run this script with one platform's look forced, so a shot of the
    macOS setup actually shows the macOS chrome. SOUNDBOARD_PLATFORM is read
    once at import, so each platform needs its own process."""
    env = dict(os.environ, SOUNDBOARD_PLATFORM=platform_name)
    result = subprocess.run([sys.executable, os.path.abspath(__file__), platform_name], env=env)
    if result.returncode != 0:
        raise SystemExit(f"{platform_name} shots failed")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if len(sys.argv) < 2:
        # No platform named: do all three, each in its own process.
        for platform_name in DEVICES:
            run_platform(platform_name)
        return
    platform_name = sys.argv[1]
    if platform_name not in DEVICES:
        raise SystemExit(f"unknown platform {platform_name!r}; expected one of {', '.join(DEVICES)}")
    if SCALE != 1:
        # CustomTkinter's own scaling, so the widgets are drawn bigger
        # rather than the image being stretched afterwards.
        ctk.set_widget_scaling(SCALE)
        ctk.set_window_scaling(SCALE)
    root = ctk.CTk()
    root.geometry(WINDOW)
    board = Soundboard(root)
    # After the window is built, not before: Soundboard applies the theme
    # itself from the config, and a config with no appearance saved means
    # "system", which would make these shots depend on whatever desktop
    # they were generated on. The help is written against the dark look.
    set_appearance("dark")
    # The meters are polled on a timer that would wipe the level set below.
    if board._meter_poll is not None:
        root.after_cancel(board._meter_poll)
        board._meter_poll = None
    # Where the settings are, shot first and with the panel still
    # collapsed: the step before it tells the reader to find a tab or a
    # button, and this is the state they are looking at while they do.
    # The tab strip is the tabview's own segmented button, which is not
    # kept on the app. The pad is tighter than the rest because the
    # button is packed hard against the row under it, and the usual one
    # crops a slice of the profile row into the shot.
    shoot(root, f"settings-{platform_name}",
          board.tabview._segmented_button, board.settings_button, pad=2)

    # A crop follows widget geometry and an unmapped panel has none to
    # follow, so the panel has to be showing. Its tab is the only place
    # it is shown now: the button on the Soundboard tab opens that tab
    # rather than the panel, because the panel does not fit above a
    # board (see _build_settings).
    board.show_settings_tab()
    root.update()

    # The row labels say which dropdown is which, so they belong in the
    # crop. They are laid out by grid and not kept on the app.
    frame = board.input_menu.master.master
    mic_label = frame.grid_slaves(row=0, column=0)[0]
    output_label = frame.grid_slaves(row=1, column=0)[0]
    output_side = board.hear_self_checkbox.master

    device_rows = (mic_label, output_label, board.input_menu,
                   board.output_menu, board.output_meter)
    set_devices(board, *DEVICES[platform_name], LOOPBACK[platform_name])
    board._update_meter(board.input_meter, 0.0, True, True)
    board._update_meter(board.output_meter, 0.0, True, True)
    shoot(root, f"devices-{platform_name}", *device_rows)

    # The Test checkpoint: the output meter mid-tone, in the app's own
    # colours rather than ones picked here. Every page shows this one and
    # the warning below, so both are taken per platform - a macOS page
    # that goes from macOS dropdowns to a Windows-chrome meter reads as a
    # screenshot of some other app.
    board._update_meter(board.output_meter, 0.62, True, True)
    shoot(root, f"test-{platform_name}", output_label, board.output_menu,
          board.output_meter, output_side)

    # The warning the app raises by itself when the microphone is a
    # loopback device, which is what makes people hear themselves back.
    root.geometry(WARNING_WINDOW)
    loopback = LOOPBACK[platform_name]
    board.config["input_device"] = loopback
    board.input_var.set(loopback)
    board._update_device_warnings()
    root.update_idletasks()
    root.update()
    if not board.loop_warning.cget("text"):
        raise SystemExit(
            f"the feedback warning did not appear for {loopback!r}; is it still "
            "matched by devices.LOOPBACK_INPUT_HINTS?"
        )
    shoot(root, f"feedback-{platform_name}", board.loop_warning, pad=3)

    root.destroy()


if __name__ == "__main__":
    main()
