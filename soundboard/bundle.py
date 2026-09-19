"""Reading and writing board bundles: the board *and* its audio.

A .sbboard file is links, so a board of recorded takes, edited clips or
anything added from disk cannot travel in one at all. A bundle is the
other trade: a zip holding the list and every clip it plays, which moves
a whole board to another machine and is the only way to move the clips
that have no link behind them.

The two formats stay apart on purpose. A .sbboard is small, carries no
audio and can be passed around freely; a bundle is as big as the board
and holds other people's audio, so it is a different file with a
different extension rather than a .sbboard that sometimes has sound in
it.

Nothing here trusts the archive's own paths. A member is read by the
name the manifest gives, and written out under the basename alone, so a
bundle built by hand cannot name a destination outside Sounds/.
"""

import json
import os
import zipfile

from .config import sanitize_filename, sound_paths, unique_path

FORMAT_KEY = "soundboard_bundle"
FORMAT_VERSION = 1
SUFFIX = ".sbpack"
MANIFEST = "board.json"
AUDIO_DIR = "sounds"
# Kept in step with board_file.SHARED_KEYS, plus the ones that only mean
# something when the audio travels with the entry.
SHARED_KEYS = ("name", "hotkey", "volume", "loop", "enabled",
               "fade_in", "fade_out", "crossfade")
# A bundle is a file from someone else. These are the sizes past which
# it is refused rather than written across the disk: a board of a few
# hundred clips is comfortably inside both.
MAX_CLIP_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


class BundleError(ValueError):
    """The file isn't a bundle, or is one this version can't read."""


def _arcname(path, taken):
    """A name inside the zip: the clip's own filename, made unique
    across the bundle without letting it name a folder."""
    name = sanitize_filename(os.path.basename(path)) or "sound"
    base, ext = os.path.splitext(name)
    candidate = name
    n = 2
    while candidate.lower() in taken:
        candidate = f"{base}_{n}{ext}"
        n += 1
    taken.add(candidate.lower())
    return AUDIO_DIR + "/" + candidate


def build(profiles, resolve):
    """The manifest for these {name, sounds} profiles, the files it
    refers to as [(arcname, path)], and the clips left out because their
    file is gone. `resolve` turns a stored path into one on disk."""
    members, missing, taken, seen = [], [], set(), {}
    exported = []
    for profile in profiles:
        sounds = []
        for sound in profile["sounds"]:
            files, lost = [], False
            for stored in sound_paths(sound):
                real = resolve(stored)
                if not os.path.isfile(real):
                    lost = True
                    continue
                key = os.path.normcase(os.path.abspath(real))
                if key not in seen:
                    seen[key] = _arcname(real, taken)
                    members.append((seen[key], real))
                files.append(seen[key])
            if not files:
                missing.append((profile["name"], sound.get("name", "?")))
                continue
            if lost:
                missing.append((profile["name"], sound.get("name", "?")))
            entry = {key: sound[key] for key in SHARED_KEYS if key in sound}
            entry["files"] = files
            # Carried when it exists, so a bundle can be re-exported as a
            # .sbboard without losing where the clip came from.
            if sound.get("source", {}).get("url"):
                entry["source"] = dict(sound["source"])
            sounds.append(entry)
        exported.append({"name": profile["name"], "sounds": sounds})
    return {FORMAT_KEY: FORMAT_VERSION, "profiles": exported}, members, missing


def write(path, profiles, resolve, app_version=None):
    """Write a bundle. Returns what build() left out."""
    manifest, members, missing = build(profiles, resolve)
    if app_version:
        manifest["app_version"] = app_version
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2),
                    compress_type=zipfile.ZIP_DEFLATED)
        for arcname, real in members:
            # Stored, not deflated: mp3, ogg and m4a are compressed
            # already, and a board of them would be zipped for minutes to
            # save nothing.
            zf.write(real, arcname, compress_type=zipfile.ZIP_STORED)
    return missing


def _clean_entry(entry, names):
    if not isinstance(entry, dict):
        return None
    files = [f for f in entry.get("files", []) if isinstance(f, str) and f in names]
    if not files:
        return None
    clean = {key: entry[key] for key in SHARED_KEYS if key in entry}
    clean["files"] = files
    source = entry.get("source")
    if isinstance(source, dict) and source.get("url"):
        clean["source"] = dict(source)
    return clean


def read(path):
    """The profiles in a bundle, each sound naming members of the zip
    that are really in it. Raises BundleError rather than half-reading
    one."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names:
                raise BundleError("This isn't a Soundboard bundle: it has no board.json in it.")
            try:
                data = json.loads(zf.read(MANIFEST).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise BundleError(f"The bundle's board.json isn't valid JSON ({e}).")
            total = 0
            for info in zf.infolist():
                if info.filename == MANIFEST:
                    continue
                if info.file_size > MAX_CLIP_BYTES:
                    raise BundleError(
                        f"'{os.path.basename(info.filename)}' in this bundle is "
                        f"{info.file_size / (1024 ** 2):.0f} MB, which is more than one clip should be."
                    )
                total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise BundleError(
                    f"This bundle holds {total / (1024 ** 3):.1f} GB of audio, which is more "
                    "than Soundboard will unpack."
                )
    except zipfile.BadZipFile:
        raise BundleError("This file isn't a Soundboard bundle.")
    except OSError as e:
        raise BundleError(f"Can't read that file ({e}).")

    if not isinstance(data, dict) or FORMAT_KEY not in data:
        raise BundleError("This isn't a Soundboard bundle.")
    version = data.get(FORMAT_KEY)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise BundleError(
            f"This bundle is version {version}; this Soundboard reads up to {FORMAT_VERSION}. "
            "Update Soundboard to open it."
        )
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        raise BundleError("This bundle has no profiles in it.")

    cleaned = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        sounds = [e for e in (_clean_entry(s, names) for s in profile.get("sounds", []))
                  if e is not None]
        cleaned.append({"name": str(profile.get("name") or "Imported"), "sounds": sounds})
    if not any(p["sounds"] for p in cleaned):
        raise BundleError("This bundle has no clips in it.")
    return cleaned


def extract(zf, arcname, dest_dir):
    """Write one member into dest_dir and return the path written.

    The destination is built from the basename and nothing else: the
    name inside the archive never reaches the filesystem, so a member
    called ../../autostart cannot escape the folder.
    """
    name = sanitize_filename(os.path.basename(arcname)) or "sound"
    target = unique_path(os.path.join(dest_dir, name))
    info = zf.getinfo(arcname)
    written = 0
    try:
        with zf.open(arcname) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                # A zip header can claim one size and deliver another.
                if written > MAX_CLIP_BYTES or written > info.file_size:
                    raise BundleError(f"'{name}' in this bundle is larger than it says it is.")
                out.write(chunk)
    except (BundleError, zipfile.BadZipFile, OSError) as e:
        # Half a clip is not a clip: take it back out rather than leave
        # something the board would point at and fail to play.
        try:
            os.remove(target)
        except OSError:
            pass
        if isinstance(e, zipfile.BadZipFile):
            raise BundleError(f"'{name}' in this bundle is damaged ({e}).")
        raise
    return target


def clip_count(profiles):
    return sum(len(sound["files"]) for p in profiles for sound in p["sounds"])
