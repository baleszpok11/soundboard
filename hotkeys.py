"""Global hotkey plumbing: key naming, the listener, and the macOS
permission and keyboard layout quirks."""

import contextlib
import re
import sys

from pynput import keyboard as pynkeyboard

MACOS_INPUT_MONITORING_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

# Tk keysym prefixes of modifier keys, mapped to pynput names.
MODIFIER_KEYSYMS = {
    "Control": "ctrl",
    "Shift": "shift",
    "Alt": "alt",
    "Option": "alt",
    "Command": "cmd",
    "Super": "cmd",
    "Win": "cmd",
    # Meta is the Command key in Tk on macOS, and usually Alt elsewhere.
    "Meta": "cmd" if sys.platform == "darwin" else "alt",
}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")
# Tk keysyms of non-character keys, mapped to pynput Key names.
NAMED_KEYSYMS = {
    "Return": "enter", "KP_Enter": "enter", "space": "space", "Tab": "tab",
    "BackSpace": "backspace", "Delete": "delete", "Insert": "insert",
    "Home": "home", "End": "end", "Prior": "page_up", "Next": "page_down",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Pause": "pause", "Print": "print_screen", "Scroll_Lock": "scroll_lock",
    "Caps_Lock": "caps_lock", "Num_Lock": "num_lock", "Menu": "menu",
}

HOTKEY_MODIFIERS = frozenset(
    {pynkeyboard.Key.ctrl, pynkeyboard.Key.alt, pynkeyboard.Key.shift, pynkeyboard.Key.cmd}
)


def hotkey_permission_granted():
    """False when macOS will silently block global hotkeys because the app
    has neither Input Monitoring nor Accessibility permission."""
    if sys.platform != "darwin":
        return True
    try:
        import HIServices
        import Quartz
        return bool(HIServices.AXIsProcessTrusted() or Quartz.CGPreflightListenEventAccess())
    except Exception:
        return True


def request_hotkey_permission():
    """Ask macOS to show its Input Monitoring prompt (only shown once)."""
    try:
        import Quartz
        Quartz.CGRequestListenEventAccess()
    except Exception:
        pass


_macos_layout_context = None


def pin_macos_keyboard_layout():
    """pynput reads the keyboard layout when its listener thread starts,
    but current macOS only allows that on the main thread and kills the
    process otherwise. Read it here (call from the main thread) and give
    pynput the cached value."""
    if sys.platform != "darwin":
        return
    from pynput._util import darwin as darwin_util
    from pynput.keyboard import _darwin as darwin_keyboard

    global _macos_layout_context
    with darwin_util.keycode_context() as context:
        _macos_layout_context = context

    @contextlib.contextmanager
    def cached_context():
        yield context

    darwin_keyboard.keycode_context = cached_context


def macos_base_char(vk):
    """The character a key types without modifiers, from the layout pinned
    by pin_macos_keyboard_layout (safe to call from any thread)."""
    if _macos_layout_context is None:
        return None
    from pynput._util import darwin as darwin_util
    try:
        char = darwin_util.keycode_to_string(_macos_layout_context, vk)
    except Exception:
        return None
    return char if len(char) == 1 and char.isprintable() else None


def modifier_for_keysym(keysym):
    return MODIFIER_KEYSYMS.get(keysym.split("_")[0])


def hotkey_part_for_key(keysym, keycode):
    """The pynput hotkey part for a non-modifier key event, using the key's
    unmodified character (Shift+1 gives "1", Option+1 on macOS gives "1").
    Returns None for keys that can't be used."""
    if keysym in NAMED_KEYSYMS:
        return f"<{NAMED_KEYSYMS[keysym]}>"
    if re.fullmatch(r"F([1-9]|1[0-9]|20)", keysym):
        return f"<{keysym.lower()}>"
    if keysym.startswith("KP_"):
        return None  # numpad keys don't map to a stable character
    char = None
    try:
        if sys.platform == "darwin":
            # Tk on macOS puts the virtual key code in the top byte.
            from pynput._util import darwin as darwin_util
            with darwin_util.keycode_context() as context:
                char = darwin_util.keycode_to_string(context, keycode >> 24)
        elif sys.platform == "win32":
            import ctypes
            # MAPVK_VK_TO_CHAR; the high bit marks dead keys.
            code = ctypes.windll.user32.MapVirtualKeyW(keycode, 2) & 0x7FFFFFFF
            char = chr(code) if code else None
    except Exception:
        char = None
    if not char and len(keysym) == 1:
        char = keysym
    if not char or len(char) != 1 or not char.isprintable() or char.isspace():
        return None
    return char.lower()


def hotkey_keys(hotkey):
    """The set of keys a hotkey text resolves to, or None if unparsable."""
    try:
        return frozenset(pynkeyboard.HotKey.parse(hotkey))
    except ValueError:
        return None


def is_valid_hotkey(hotkey):
    try:
        pynkeyboard.GlobalHotKeys({hotkey: lambda: None})
    except ValueError:
        return False
    return True


class HotkeyListener(pynkeyboard.GlobalHotKeys):
    """GlobalHotKeys that only fires a hotkey when exactly its modifiers are
    held (pynput alone also fires <alt>+1 for Ctrl+Alt+1), and reports while
    a key combination is held (push-to-talk). Callbacks run on the listener
    thread."""

    def __init__(self, hotkeys, hold_hotkey=None, on_hold=None):
        self._pressed = set()
        exact = {
            text: self._when_modifiers(frozenset(pynkeyboard.HotKey.parse(text)) & HOTKEY_MODIFIERS, action)
            for text, action in hotkeys.items()
        }
        super().__init__(exact)
        self._hold_keys = frozenset(pynkeyboard.HotKey.parse(hold_hotkey)) if hold_hotkey else frozenset()
        self._on_hold = on_hold
        self._holding = False

    def _when_modifiers(self, modifiers, action):
        def run():
            if self._pressed & HOTKEY_MODIFIERS == modifiers:
                action()
        return run

    def canonical(self, key):
        # pynput on macOS reports the typed character (Option+1 is "¡",
        # Shift+1 is "!"), so hotkeys like <alt>+1 would never match. Use
        # the key's unmodified character instead, as Windows does.
        if sys.platform == "darwin" and isinstance(key, pynkeyboard.KeyCode) and key.vk is not None:
            char = macos_base_char(key.vk)
            if char:
                return pynkeyboard.KeyCode.from_char(char.lower())
        return super().canonical(key)

    def _on_press(self, key, injected):
        if not injected:
            self._pressed.add(self.canonical(key))
        super()._on_press(key, injected)
        if self._hold_keys and not injected:
            self._update_hold()

    def _on_release(self, key, injected):
        if not injected:
            self._pressed.discard(self.canonical(key))
        super()._on_release(key, injected)
        if self._hold_keys and not injected:
            self._update_hold()

    def _update_hold(self):
        holding = self._hold_keys <= self._pressed
        if holding != self._holding:
            self._holding = holding
            self._on_hold(holding)
