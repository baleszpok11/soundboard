"""Reading and writing shareable board files.

A board file lists what a board plays and where each clip came from, but
never the audio itself: each person downloads their own copy from the
original link. That keeps the file small and means nothing is
redistributed.

Only sounds with a source can travel. Anything added from disk, and
anything the Sound Editor produced, stays behind - it has no link to
rebuild it from. A random group is a list of files with one link between
them, so it travels as the clip that link fetches, and the export says
so rather than pretending the group made it across.
"""

import json

FORMAT_KEY = "soundboard_board"
FORMAT_VERSION = 1
# Copied onto each exported sound; anything else is local to one machine.
SHARED_KEYS = ("name", "hotkey", "volume", "loop", "enabled")


class BoardFileError(ValueError):
    """The file isn't a board file, or is one this version can't read."""


def is_shareable(sound):
    return bool(sound.get("source", {}).get("url"))


def export_sound(sound):
    entry = {key: sound[key] for key in SHARED_KEYS if key in sound}
    entry["source"] = dict(sound["source"])
    return entry


def build(profiles, app_version=None):
    """A board file for these {name, sounds} profiles, the sounds left
    out because nothing could rebuild them, and the groups travelling as
    a single clip."""
    exported, skipped, partial = [], [], []
    for profile in profiles:
        sounds = []
        for sound in profile["sounds"]:
            if is_shareable(sound):
                sounds.append(export_sound(sound))
                extra = len(sound.get("paths") or []) - 1
                if extra > 0:
                    partial.append((profile["name"], sound.get("name", "?"), extra))
            else:
                skipped.append((profile["name"], sound.get("name", "?")))
        exported.append({"name": profile["name"], "sounds": sounds})
    data = {FORMAT_KEY: FORMAT_VERSION, "profiles": exported}
    if app_version:
        data["app_version"] = app_version
    return data, skipped, partial


def parse(text):
    """Profiles from a board file. Raises BoardFileError on anything that
    isn't one, rather than half-importing it."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise BoardFileError(f"This file isn't valid JSON ({e}).")
    if not isinstance(data, dict) or FORMAT_KEY not in data:
        raise BoardFileError("This isn't a Soundboard board file.")
    version = data.get(FORMAT_KEY)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise BoardFileError(
            f"This board file is version {version}; this Soundboard reads up to {FORMAT_VERSION}. "
            "Update Soundboard to open it."
        )
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise BoardFileError("This board file has no profiles in it.")

    cleaned = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        sounds = [s for s in profile.get("sounds", [])
                  if isinstance(s, dict) and is_shareable(s)]
        cleaned.append({"name": str(profile.get("name") or "Imported"), "sounds": sounds})
    if not cleaned:
        raise BoardFileError("This board file has no profiles in it.")
    return cleaned


def urls(profiles):
    """Every link an import of these profiles would fetch, in order."""
    return [s["source"]["url"] for p in profiles for s in p["sounds"]]


def dumps(data):
    return json.dumps(data, indent=2)
