"""Drive the real Soundboard window and assert on what it lays out.

The app is built the way it is at startup - a real `Soundboard` on a real
`CTk` window - against a throwaway config and `Sounds/` directory, so a
run cannot touch anyone's board. `tools/make_tutorial_shots.py` sets the
same thing up to take screenshots; this adds somewhere to put assertions.

Two rules, both of which cost an afternoon to learn:

Steps run as `after()` callbacks inside a real `mainloop()`, never as a
straight line of calls with `update()` pumped by hand. Tk does a lot of
its work between events - geometry propagation, the first `<Configure>`,
the idle tasks a CTk widget queues for itself - and hand-pumping gets
some of it but not all. A board driven that way renders rows that are
never drawn and reports positions that the running app never has, which
means invented failures and, worse, real ones hidden.

Assertions are about geometry, not pixels. `winfo_ismapped()`,
`winfo_y()`, `winfo_reqwidth()` and the scroller's canvas items are
exact, stable across machines, and say what is wrong rather than that
something is. Screenshots are still taken, because a human looking at
the failure wants to see it - but they are evidence, not the test.

A scenario is a generator. It yields between steps, and every yield
gives Tk a tick to lay out what the last step changed:

    def opens_and_closes(board):
        board.expect_visible(board.list_frame, "sound list")
        board.click_settings()
        yield
        board.expect_visible(board.list_frame, "sound list")

Checks record failures and carry on, so one run reports everything it
found instead of the first thing.
"""

import json
import math
import os
import struct
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

# The config has to be redirected before anything reads it, which means
# before the app package is imported any further than this.
from soundboard import config  # noqa: E402

_WORK = tempfile.mkdtemp(prefix="soundboard-e2e-")
config.APP_DIR = _WORK
config.CONFIG_PATH = os.path.join(_WORK, "soundboard_config.json")
config.SOUNDS_DIR = os.path.join(_WORK, "Sounds")
os.makedirs(config.SOUNDS_DIR, exist_ok=True)

import customtkinter as ctk  # noqa: E402

from soundboard.app import Soundboard  # noqa: E402
from soundboard.theme import set_appearance  # noqa: E402

SHOT_DIR = os.path.join(_WORK, "shots")

# A tick long enough for Tk to have settled after a step. Anything that
# needs longer than this is a finding in itself: the app is meant to
# redraw between one click and the next.
TICK_MS = 300
# The first tick waits longer, because startup does more: the theme, the
# device scan's first pass, and the loudness worker joining the board.
START_MS = 1500

DEFAULT_SOUNDS = ["Airhorn", "Applause", "Boing", "Cricket", "Drumroll",
                  "Laugh", "Sad Trombone", "Vine Boom"]


def _write_clip(path, seconds=1.0, rate=44100):
    """A second of a quiet tone. Real audio, because the board measures
    every clip's loudness when it joins and a zero-length file is not
    the thing under test."""
    frames = int(seconds * rate)
    with wave.open(path, "w") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(t / 20.0)))
            for t in range(frames)))


def make_board_files(names=DEFAULT_SOUNDS, appearance="dark"):
    """Write a config and its clips into the throwaway directory."""
    sounds = []
    for name in names:
        filename = name.lower().replace(" ", "_") + ".wav"
        _write_clip(os.path.join(config.SOUNDS_DIR, filename))
        sounds.append({"name": name, "path": filename, "hotkey": None,
                       "enabled": True, "volume": 100, "loop": False})
    with open(config.CONFIG_PATH, "w") as handle:
        json.dump({"profiles": [{"name": "Default", "sounds": sounds}],
                   "active_profile": "Default", "appearance": appearance,
                   # A test asks GitHub nothing. The check answers on a
                   # worker thread and calls root.after() when it does,
                   # which throws once the run has finished and buries
                   # the failures under a traceback.
                   "check_for_updates": False},
                  handle)
    return sounds


class Board:
    """A running app, and the checks that can be made against it."""

    def __init__(self, geometry="900x800+60+60", sounds=DEFAULT_SOUNDS,
                 appearance="dark"):
        make_board_files(sounds, appearance)
        os.makedirs(SHOT_DIR, exist_ok=True)
        self.failures = []
        self.root = ctk.CTk()
        self.root.geometry(geometry)
        self.app = Soundboard(self.root)
        # After the window is built, not before: the app applies the
        # config's own appearance, and "system" would make a run depend
        # on the desktop it happened to run on.
        set_appearance(appearance)
        self._shots = 0

    # -- the widgets a scenario talks about -------------------------------

    @property
    def board_tab(self):
        """The Soundboard tab. It is not kept on the app, but everything
        on it is parented to it, so any child leads back."""
        return self.app.settings_button.master

    @property
    def list_frame(self):
        return self.app.list_frame

    @property
    def rows_on_screen(self):
        """The scroller's canvas items, which are the rows that exist."""
        canvas = self.list_frame.canvas
        return [(item, canvas.coords(item),
                 int(float(canvas.itemcget(item, "width") or 0)))
                for item in canvas.find_all()]

    def widgets_below_settings(self):
        """Everything on the board tab that the settings panel opens
        above, which is what must not be pushed off the window."""
        return [child for child in self.board_tab.winfo_children()
                if child is not self.app.settings_button
                and child is not self.app.settings_panel]

    # -- acting on it -----------------------------------------------------

    def click_settings(self):
        """The disclosure button on the Soundboard tab."""
        self.app.settings_button.invoke()

    def open_tab(self, name):
        # set() does not fire the tabview's command, which is what moves
        # the settings panel between its two homes, so call it the way a
        # click would.
        self.app.tabview.set(name)
        command = self.app.tabview._command
        if command is not None:
            command()

    def search(self, text):
        self.app.search_entry.delete(0, "end")
        self.app.search_entry.insert(0, text)
        self.app._on_search()

    def resize(self, geometry):
        self.root.geometry(geometry)

    # -- checks -----------------------------------------------------------

    def fail(self, message):
        self.failures.append(message)
        self.shot("failure")

    def check(self, ok, message):
        if not ok:
            self.fail(message)
        return ok

    def expect_visible(self, widget, label):
        return self.check(
            bool(widget.winfo_ismapped()),
            f"{label} is not on screen (unmapped)")

    def expect_hidden(self, widget, label):
        return self.check(
            not widget.winfo_ismapped(),
            f"{label} is on screen but should not be")

    def expect_fits(self, widget, label):
        """Nothing clipped: the space a widget was given is at least what
        it asked for. This is the check that catches a button reading
        "ort a" instead of "Report a bug"."""
        if not widget.winfo_ismapped():
            return True  # a hidden widget's size is not a layout fact
        asked, given = widget.winfo_reqwidth(), widget.winfo_width()
        return self.check(
            given >= asked,
            f"{label} is clipped: asked for {asked}px, given {given}px")

    def expect_rows_full_width(self):
        """The scroller lays its rows out as canvas items, so a row that
        is technically mapped can still be a pixel wide."""
        viewport = self.list_frame.viewport_width()
        thin = [width for _, _, width in self.rows_on_screen
                if width < viewport / 2]
        return self.check(
            not thin,
            f"{len(thin)} of {len(self.rows_on_screen)} sound rows are "
            f"narrower than half the {viewport}px viewport "
            f"(widths {sorted(set(thin))}) - the list looks empty")

    def expect_drawn(self, widget, label, least=3):
        """That something was actually painted where this widget is.

        The only check here that reads pixels, and it is deliberately the
        coarsest one that can work: a rectangle of a single flat colour
        means nothing was drawn, whatever the geometry says. It exists
        because geometry cannot see a stale repaint - #156 left the board
        correctly laid out and entirely undrawn, and all five scenarios
        passed against a blank window.

        Not a screenshot diff: no reference image, nothing to update when
        a colour or a font changes. It only asks whether anything is
        there at all.
        """
        if not widget.winfo_ismapped():
            return True  # absence is expect_visible's business, not this
        image = self._grab(widget)
        if image is None:
            return True  # no Pillow, or nothing to grab; not a failure
        found = len(image.getcolors(maxcolors=1 << 16) or [])
        return self.check(
            found >= least,
            f"{label} is laid out but not drawn: its {image.size[0]}x"
            f"{image.size[1]} area has {found} distinct colour(s)")

    def snapshot(self):
        """Where everything on the board tab sits, for comparing a state
        against the same state later.

        An unmapped widget keeps whatever coordinates it had when it was
        last on screen, which are not a layout fact - a widget that was
        never packed reads (0, 0) and the same widget after one round
        trip reads wherever it went. Only its absence is compared.
        """
        state = {}
        for child in self.board_tab.winfo_children():
            if child.winfo_ismapped():
                state[id(child)] = (True, child.winfo_x(), child.winfo_y())
            else:
                state[id(child)] = (False, None, None)
        return state

    def expect_restored(self, before, label):
        """A reversible action must leave the board where it found it."""
        after = self.snapshot()
        moved = [key for key in before if key in after and before[key] != after[key]]
        return self.check(
            not moved,
            f"{len(moved)} widget(s) did not return to their starting "
            f"position after {label}")

    # -- evidence ---------------------------------------------------------

    def shot(self, name):
        """A screenshot of the window, saved next to the run's output.
        Never an assertion - just what a human wants to see when one
        trips."""
        image = self._grab(self.root)
        if image is None:
            return None
        self._shots += 1
        path = os.path.join(SHOT_DIR, f"{self._shots:02d}-{name}.png")
        image.save(path)
        return path

    def _grab(self, widget):
        """What is on the screen where `widget` is."""
        try:
            from PIL import ImageGrab
        except ImportError:
            return None
        self.root.update_idletasks()
        width, height = widget.winfo_width(), widget.winfo_height()
        if width < 2 or height < 2:
            return None
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        screen = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY"))
        # Tk reports points and the grab comes back in pixels, which are
        # the same on X11 and not on a Retina screen.
        scale = screen.width / self.root.winfo_screenwidth()
        return screen.crop(tuple(round(edge * scale)
                                 for edge in (x, y, x + width, y + height)))

    # -- running ----------------------------------------------------------

    def run(self, scenario):
        """Advance the scenario one step per tick, inside a real mainloop."""
        steps = scenario(self)
        crash = []

        def tick():
            try:
                next(steps)
            except StopIteration:
                self.root.quit()
                return
            except Exception as error:  # a scenario's own bug, or the app's
                crash.append(error)
                self.shot("crash")
                self.root.quit()
                return
            self.root.after(TICK_MS, tick)

        self.root.after(START_MS, tick)
        self.root.mainloop()
        try:
            self.root.destroy()
        except Exception:
            pass  # already gone; nothing here outlives the run
        if crash:
            raise crash[0]
        return self.failures
