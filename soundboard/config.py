"""Paths, the JSON config file and the error log.

Config (devices + sound/hotkey mappings) is stored in
soundboard_config.json, with sound files in a Sounds/ folder beside it.
Running from source that is the project root; a built app puts them in
the per-user data folder for the platform, so the executable can be
moved, replaced by an update or run from a download folder without the
board going with it.
"""

import json
import os
import re
import shutil
import sys
import tempfile

# This package sits one level below the project root, which is where the
# config, Sounds/ and assets/ live when running from source.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_FOLDER_NAME = "Soundboard"


def _data_dir():
    # A PyInstaller onefile build runs from a temp dir that is deleted on
    # exit, so user data must live elsewhere. It used to go beside the
    # executable, which put people's boards in their Downloads folder and
    # lost them the moment the executable was moved.
    if not getattr(sys, "frozen", False):
        return _PROJECT_ROOT
    if sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Documents", APP_FOLDER_NAME)
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        path = os.path.join(base, APP_FOLDER_NAME)
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, APP_FOLDER_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # Nowhere to write: fall back to the old behaviour rather than
        # failing to start.
        return _legacy_data_dir()
    return path


def _legacy_data_dir():
    """Where a built app kept its data before it moved to the per-user
    folder: beside the executable."""
    if not getattr(sys, "frozen", False):
        return _PROJECT_ROOT
    return os.path.dirname(os.path.abspath(sys.executable))


APP_VERSION = "0.9.1"  # bump before tagging a release; CI checks the tag matches
# What a freshly added sound is set to. Clips are usually mastered far
# louder than a voice, so full volume is how people blow out a call the
# first time they press a button; quiet is the recoverable mistake.
# Only new sounds - a board already on disk keeps whatever it has, and a
# sound that predates the volume key still loads at 100 (see _load_sound).
NEW_SOUND_VOLUME = 20
DEFAULT_PROFILE = "Default"
APP_DIR = _data_dir()
CONFIG_PATH = os.path.join(APP_DIR, "soundboard_config.json")
SOUNDS_DIR = os.path.join(APP_DIR, "Sounds")
ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", _PROJECT_ROOT), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")
# Windows wants an .ico: iconphoto() is ignored there by
# CustomTkinter, which only leaves an icon alone once
# iconbitmap() has been called.
ICON_ICO_PATH = os.path.join(ASSETS_DIR, "icon.ico")


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


def migrate_data_dir():
    """Copy a board left beside the executable into the per-user folder.

    Copies rather than moves, and only when there is nothing to lose:
    if the new location already has a config, the old one is left alone
    and ignored. The originals stay where they are, so a failure here
    costs nothing and the old folder remains a fallback until the user
    deletes it. Returns True when something was copied.
    """
    old_dir = _legacy_data_dir()
    if same_file(old_dir, APP_DIR) or os.path.exists(CONFIG_PATH):
        return False
    old_config = os.path.join(old_dir, "soundboard_config.json")
    if not os.path.exists(old_config):
        return False
    old_sounds = os.path.join(old_dir, "Sounds")
    try:
        if os.path.isdir(old_sounds):
            # dirs_exist_ok: the folder may already be there and empty.
            shutil.copytree(old_sounds, SOUNDS_DIR, dirs_exist_ok=True)
        # The config goes last: until it lands, nothing treats the new
        # folder as the live one, so an interrupted copy is not a
        # half-migrated board.
        shutil.copy2(old_config, CONFIG_PATH)
    except OSError:
        return False
    return True


def load_config():
    """Raises ValueError if the file exists but is not a valid config."""
    migrate_data_dir()
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
    config.setdefault("replay_buffer", True)
    config.setdefault("replay_hotkey", None)
    config.setdefault("mic_effect", "none")
    config.setdefault("mic_effect_amount", 70)
    config.setdefault("ptt_hotkey", None)
    config.setdefault("sound_view", "list")
    config.setdefault("close_to_tray", False)
    config.setdefault("check_for_updates", True)
    # "system", "light" or "dark"; "system" follows the OS on
    # Windows and macOS and falls back to light elsewhere.
    config.setdefault("appearance", "system")
    config.setdefault("skipped_version", None)
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
    _load_sound_group(sound)
    return sound


def _load_sound_group(sound):
    """Keep "paths" a list of at least two clips led by "path", or drop
    it. An entry always plays "path" on its own, so a board written by a
    newer version still works in an older one."""
    paths = sound.get("paths")
    if not isinstance(paths, list):
        sound.pop("paths", None)
        return
    members = [p for p in paths if isinstance(p, str) and p]
    members = list(dict.fromkeys([sound["path"], *members]))
    if len(members) > 1:
        sound["paths"] = members
    else:
        sound.pop("paths", None)


def sound_paths(sound):
    """Every clip a board entry can play: one for a plain sound, several
    for a random group."""
    paths = sound.get("paths")
    if isinstance(paths, list) and len(paths) > 1:
        return list(paths)
    return [sound["path"]]


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
