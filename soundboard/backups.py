"""Dated copies of the config, and reading one back.

There is one config file, and a board only exists inside it: delete a
profile by accident and there is nothing to go back to. A copy is taken
each day the app starts, kept for a few days and then dropped, so the
folder cannot grow without limit.

Nothing here writes the live config. Restoring is the caller's job,
because it is the one thing in this app that can empty a board with no
error, and it belongs next to the confirmation that asks for it.
"""

import datetime
import json
import os
import shutil

from . import config as cfg

FOLDER = "Backups"
PREFIX = "soundboard_config-"
SUFFIX = ".json"
# A week of daily copies. Long enough to cover "I broke it on Friday and
# noticed on Monday", short enough that a board of a few hundred entries
# stays a handful of small files.
KEEP = 7


def backup_dir():
    """Beside the config rather than inside Sounds/: it is the config's
    own history, and nothing should mistake it for a clip."""
    return os.path.join(cfg.APP_DIR, FOLDER)


def _stamp(when):
    return when.strftime("%Y-%m-%d_%H-%M-%S")


def backup_path(when=None):
    when = when or datetime.datetime.now()
    return os.path.join(backup_dir(), PREFIX + _stamp(when) + SUFFIX)


def _taken_at(name, path):
    """When a backup was taken, from its own name.

    Not from the file's timestamp: the copy keeps the config's, so a
    week of backups of a board nobody edited would all claim the same
    moment and sort in whatever order the folder listed them. The name
    is written here and says exactly when.
    """
    try:
        return datetime.datetime.strptime(name[len(PREFIX):len(PREFIX) + 19],
                                          "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return datetime.datetime.fromtimestamp(os.stat(path).st_mtime)


def list_backups():
    """Every backup, newest first, as (path, taken datetime, bytes)."""
    folder = backup_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for name in names:
        if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
            continue
        path = os.path.join(folder, name)
        try:
            size = os.stat(path).st_size
        except OSError:
            continue
        found.append((path, _taken_at(name, path), size))
    # By name after time: two copies taken in the same second differ only
    # by the _2 the second one gets, and that one is the later.
    found.sort(key=lambda item: (item[1], os.path.basename(item[0])), reverse=True)
    return found


def prune(keep=KEEP):
    """Drop the oldest copies beyond `keep`. Returns how many went."""
    dropped = 0
    for path, _when, _size in list_backups()[keep:]:
        try:
            os.remove(path)
            dropped += 1
        except OSError:
            pass
    return dropped


def backup_now(keep=KEEP):
    """Copy the live config to a dated file and prune. Returns the path,
    or None when there is no config yet or the copy failed - a backup
    that cannot be taken is not worth interrupting anyone over, and the
    next start tries again."""
    if not os.path.exists(cfg.CONFIG_PATH):
        return None
    try:
        os.makedirs(backup_dir(), exist_ok=True)
        path = cfg.unique_path(backup_path())
        shutil.copy2(cfg.CONFIG_PATH, path)
    except OSError:
        return None
    prune(keep)
    return path


def backup_daily(keep=KEEP):
    """Take today's copy unless one was already taken today. Called at
    startup, where a copy per launch would push a week of history out of
    the folder in an afternoon."""
    today = datetime.date.today()
    for _path, when, _size in list_backups():
        if when.date() == today:
            return None
    return backup_now(keep)


class BackupError(ValueError):
    """The file isn't a config this version can read."""


def read_backup(path):
    """The board in a backup file, as a config dict that has been
    through the same reader as the live one. Raises BackupError rather
    than handing back half a board."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise BackupError(f"Can't read that file ({e}).")
    except ValueError as e:
        raise BackupError(f"That file isn't valid JSON ({e}).")
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("sounds", []), list)
        or not isinstance(raw.get("profiles", []), list)
    ):
        raise BackupError("That file isn't a Soundboard config.")
    return cfg.prepare_config(raw)


def describe(path, when, size):
    """One line for the list in the app. The filename is in it as well as
    the date, because two copies can be taken in the same minute and
    because it is what to look for in the folder."""
    return f"{when:%Y-%m-%d %H:%M}  ({max(1, round(size / 1024))} KB)  {os.path.basename(path)}"
