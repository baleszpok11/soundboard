"""Paths, the JSON config file and the error log.

Config (devices + sound/hotkey mappings) is stored in
soundboard_config.json, created next to the script (or next to the
executable, when built) on first run. Sound files live in the Sounds/
folder next to it.
"""

import json
import os
import re
import sys
import tempfile

# This package sits one level below the project root, which is where the
# config, Sounds/ and assets/ live when running from source.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_dir():
    # A PyInstaller onefile build runs from a temp dir that is deleted on
    # exit, so user data must live elsewhere. macOS .app bundles may be
    # read-only (app translocation), so they use ~/Documents/Soundboard.
    if not getattr(sys, "frozen", False):
        return _PROJECT_ROOT
    if sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Documents", "Soundboard")
        os.makedirs(path, exist_ok=True)
        return path
    return os.path.dirname(os.path.abspath(sys.executable))


APP_VERSION = "0.9.0"  # bump before tagging a release; CI checks the tag matches
DEFAULT_PROFILE = "Default"
APP_DIR = _data_dir()
CONFIG_PATH = os.path.join(APP_DIR, "soundboard_config.json")
SOUNDS_DIR = os.path.join(APP_DIR, "Sounds")
ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", _PROJECT_ROOT), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")


def ensure_sounds_dir():
    os.makedirs(SOUNDS_DIR, exist_ok=True)


def resolve_sound_path(path):
    """Sound paths are either an absolute path (older configs, or a file
    picked from outside Sounds/) or a filename relative to Sounds/."""
    return path if os.path.isabs(path) else os.path.join(SOUNDS_DIR, path)


def sanitize_filename(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "sound"


def same_file(a, b):
    """Path comparison that ignores case on Windows and resolves links."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base}_{n}{ext}"):
        n += 1
    return f"{base}_{n}{ext}"


def load_config():
    """Raises ValueError if the file exists but is not a valid config."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        if (
            not isinstance(config, dict)
            or not isinstance(config.get("sounds", []), list)
            or not isinstance(config.get("profiles", []), list)
        ):
            raise ValueError("config has an unexpected structure")
    if "device" in config and "output_device" not in config:
        config["output_device"] = config.pop("device")
    config.setdefault("output_device", None)
    config.setdefault("input_device", None)
    config.pop("monitor_device", None)
    config.pop("monitor_muted", None)
    config.setdefault("hear_self", True)
    config.setdefault("mic_volume", 100)
    config.setdefault("sound_volume", 100)
    config.setdefault("stop_hotkey", None)
    config.setdefault("mic_muted", False)
    config.setdefault("mute_hotkey", None)
    config.setdefault("push_to_talk", False)
    config.setdefault("ptt_hotkey", None)
    config.setdefault("sound_view", "list")
    config.setdefault("close_to_tray", False)
    _load_profiles(config)
    return config


def _load_profiles(config):
    """Sounds used to be one flat list; they now live in named profiles.
    An older config keeps everything it had, in a profile called
    Default."""
    profiles = config.pop("profiles", None)
    if not profiles:
        # Pre-profile config (or a hand-emptied one): everything it has
        # becomes the first profile.
        profiles = [{"name": DEFAULT_PROFILE, "sounds": config.get("sounds", [])}]
    config.pop("sounds", None)  # the flat list is the profile's now

    cleaned, seen = [], set()
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        name = profile.get("name") or DEFAULT_PROFILE
        name = unique_profile_name(name, seen)
        seen.add(name)
        sounds = profile.get("sounds")
        sounds = sounds if isinstance(sounds, list) else []
        cleaned.append({"name": name, "sounds": [_load_sound(s) for s in sounds
                                                if isinstance(s, dict) and s.get("path")]})
    if not cleaned:
        cleaned = [{"name": DEFAULT_PROFILE, "sounds": []}]
    config["profiles"] = cleaned

    names = [p["name"] for p in cleaned]
    if config.get("active_profile") not in names:
        config["active_profile"] = names[0]


def _load_sound(sound):
    sound.setdefault("name", os.path.splitext(os.path.basename(sound["path"]))[0])
    sound.setdefault("hotkey", None)
    sound.setdefault("enabled", True)
    sound.setdefault("loop", False)
    sound.setdefault("volume", 100)
    return sound


def unique_profile_name(name, taken):
    """Profiles are addressed by name, so two can't share one."""
    name = str(name).strip() or DEFAULT_PROFILE
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def active_profile(config):
    for profile in config["profiles"]:
        if profile["name"] == config["active_profile"]:
            return profile
    return config["profiles"][0]


def profile_sounds(config):
    return active_profile(config)["sounds"]


def save_config(config):
    # Write to a temp file and swap it in, so a crash mid-write can't
    # leave a truncated config behind.
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def write_error_log(text):
    """Append to a log next to the config, or in the temp dir if that
    folder isn't writable. Returns the path written, or None."""
    for folder in (APP_DIR, tempfile.gettempdir()):
        path = os.path.join(folder, "soundboard_error.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
            return path
        except OSError:
            continue
    return None
