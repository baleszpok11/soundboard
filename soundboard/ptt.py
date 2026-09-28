"""Holding the chat app's push-to-talk key while a clip plays.

The listener side of pynput is in hotkeys.py; this is the sending half.
It presses a key the soundboard does not own - the one Discord or the
game is set to - so a clip fired from here arrives in a call that is set
to push to talk rather than going nowhere.

Everything is driven from two calls: begin() when something is about to
play, and update() with whether anything is still going down the cable.
The key is reference-counted against that state rather than against
clips, so overlapping clips hold it once and release it once.
"""

import atexit
import sys
import threading
import time

from pynput import keyboard as pynkeyboard

MACOS_ACCESSIBILITY_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")

HOLD, TAP = "hold", "tap"
# config.py checks the stored mode against these two names spelled out
# rather than importing them: it is imported by `soundboard --play X`,
# which has no business loading pynput to send one line down a socket.
MODES = (HOLD, TAP)
# Longest lead and tail the settings offer, in seconds. A tenth of a
# second is what an app that opens its channel late needs; half a second
# is already a clip that feels late to the person pressing the key.
MAX_LEAD_S = 0.5
MAX_TAIL_S = 1.0
# How long the channel is held open by begin() alone, on top of the lead.
# A clip is decoded on the engine's worker thread, so it is not in the
# mix yet when begin() returns and update() would otherwise close the
# key before the sound it was opened for arrives.
GRACE_S = 0.5


def key_sending_granted():
    """False when macOS will silently drop the keys we send. Sending
    needs Accessibility, which is not the same permission as the Input
    Monitoring the listener asks for - a Mac can have one and not the
    other."""
    if sys.platform != "darwin":
        return True
    try:
        import HIServices
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        return True


def ptt_keys(hotkey):
    """The keys a hotkey text presses, in the order they go down, or None
    if it cannot be parsed. Order matters here and not in hotkeys.py:
    modifiers have to be held before the key they modify."""
    try:
        keys = pynkeyboard.HotKey.parse(hotkey)
    except ValueError:
        return None
    return tuple(keys) or None


class AutoPTT:
    """Presses and holds the chat app's talk key while the board plays.

    `on_error` is called with a message the first time sending a key
    fails, so a Mac that has not been given Accessibility says so in the
    settings instead of doing nothing at all.
    """

    def __init__(self, on_error=None):
        self.on_error = on_error
        self.enabled = False
        self.hotkey = None
        self.mode = HOLD
        self.lead_s = 0.0
        self.tail_s = 0.0
        self.error = None  # what went wrong the last time a key was sent
        self._keys = None
        self._open = False  # the channel is held open by us
        self._until = 0.0  # keep it open at least this long (monotonic)
        self._controller = None
        # begin() runs on the Tk thread, update() on the Tk thread, and
        # release() can come from the hotkey listener's thread.
        self._lock = threading.RLock()
        atexit.register(self.release)

    # -- settings -------------------------------------------------------

    def configure(self, enabled, hotkey, mode=HOLD, lead_s=0.0, tail_s=0.0):
        """Point it at a key. Anything held under the old settings is let
        go first, so changing the key cannot leave one down."""
        with self._lock:
            keys = ptt_keys(hotkey) if hotkey else None
            if self._open and (not enabled or keys != self._keys or mode != self.mode):
                self.release()
            self.enabled = bool(enabled)
            self.hotkey = hotkey
            self.mode = mode if mode in MODES else HOLD
            self.lead_s = max(0.0, min(MAX_LEAD_S, float(lead_s)))
            self.tail_s = max(0.0, min(MAX_TAIL_S, float(tail_s)))
            self._keys = keys

    @property
    def active(self):
        """Whether it is switched on and has a key it can actually send."""
        return bool(self.enabled and self._keys)

    # -- driving it -----------------------------------------------------

    def begin(self):
        """Open the channel for a clip that is about to play, and return
        how many milliseconds that clip should wait.

        Called before the audio is queued rather than after: the key has
        to be down first, and an app whose channel opens a moment late
        would otherwise swallow the front of the clip.
        """
        with self._lock:
            if not self.active:
                return 0
            self._until = max(self._until, time.monotonic() + self.lead_s + GRACE_S)
            self._press()
            return int(round(self.lead_s * 1000))

    def update(self, playing):
        """Called while the board polls what it is playing. `playing` is
        whether anything is going down the cable - a monitor-only preview
        is not something a call has to hear."""
        with self._lock:
            if not self.active:
                if self._open:
                    self.release()
                return
            now = time.monotonic()
            if playing:
                # Refreshed every tick, so the tail is counted from the
                # last moment something was playing.
                self._until = max(self._until, now + self.tail_s)
                self._press()
            elif self._open and now >= self._until:
                self.release()

    def release(self):
        """Let the key go now, whatever the tail says. "Stop all" and
        quitting both mean the channel should close at once - a key we
        hold and never release leaves someone's mic open."""
        with self._lock:
            if not self._open:
                return
            self._open = False
            self._until = 0.0
            if self.mode == TAP:
                self._tap()  # a toggle needs a second press to close
            else:
                self._send(reversed(self._keys), press=False)

    # -- sending --------------------------------------------------------

    def _press(self):
        if self._open:
            return
        self._open = True
        if self.mode == TAP:
            self._tap()
        else:
            self._send(self._keys, press=True)

    def _tap(self):
        self._send(self._keys, press=True)
        self._send(reversed(self._keys), press=False)

    def _send(self, keys, press):
        try:
            if self._controller is None:
                self._controller = pynkeyboard.Controller()
            for key in keys:
                if press:
                    self._controller.press(key)
                else:
                    self._controller.release(key)
        except Exception as e:
            # A Mac without Accessibility, or a Linux session with no
            # display to send to. Reported once: this runs on every clip.
            message = f"Could not send '{self.hotkey}': {e}"
            first = self.error is None
            self.error = message
            self._controller = None
            if first and self.on_error is not None:
                self.on_error(message)
