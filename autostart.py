"""Launching Soundboard at login, per platform.

Windows uses the HKCU Run key, macOS a LaunchAgent plist and Linux an
autostart .desktop entry. All three start the app with --hidden, so a
login doesn't pop the window open.
"""

import os
import shlex
import sys

APP_NAME = "Soundboard"
LAUNCH_AGENT_ID = "com.baleszpok11.soundboard"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

SUPPORTED = sys.platform in ("win32", "darwin") or sys.platform.startswith("linux")


def _command():
    """The command that starts this copy of Soundboard, minimized."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--hidden"]
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soundboard.py")
    return [sys.executable, script, "--hidden"]


def _launch_agent_path():
    return os.path.join(
        os.path.expanduser("~"), "Library", "LaunchAgents", f"{LAUNCH_AGENT_ID}.plist"
    )


def _desktop_entry_path():
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "autostart", "soundboard.desktop")


def is_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
        except OSError:
            return False
        return True
    if sys.platform == "darwin":
        return os.path.exists(_launch_agent_path())
    return os.path.exists(_desktop_entry_path())


def set_enabled(enabled):
    """Raises OSError when the entry can't be written or removed."""
    if not SUPPORTED:
        raise OSError(f"Starting at login isn't supported on {sys.platform}.")
    if sys.platform == "win32":
        _set_windows(enabled)
    elif sys.platform == "darwin":
        _set_launch_agent(enabled)
    else:
        _set_desktop_entry(enabled)


def _set_windows(enabled):
    import subprocess
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(_command()))
            return
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass


def _set_launch_agent(enabled):
    import plistlib
    path = _launch_agent_path()
    if not enabled:
        _remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(
            {"Label": LAUNCH_AGENT_ID, "ProgramArguments": _command(), "RunAtLoad": True}, f
        )


def _set_desktop_entry(enabled):
    path = _desktop_entry_path()
    if not enabled:
        _remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            f"Exec={shlex.join(_command())}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
