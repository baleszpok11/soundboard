"""Finding, fetching and installing a new release.

Only frozen builds update themselves; running from source, this does
nothing but report what the latest release is. Nothing here installs
without being asked, and a failed check is silent - a soundboard that
interrupts you because GitHub was briefly unreachable is worse than one
that misses an update.

Replacing the running program differs per platform:

- Linux: rename the new binary over the old path. The running process
  keeps its inode, so this is safe while it executes.
- Windows: a running .exe cannot be deleted or written, but it can be
  renamed. Move the old one aside, move the new one into place, and
  clear the leftover on the next start.
- macOS: not done at all. The .app is ad-hoc signed, and a downloaded
  replacement carries a quarantine flag that can stop it launching, so
  the download is revealed in Finder and the user drags it over.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from .config import APP_VERSION

REPO = "baleszpok11/soundboard"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = f"soundboard/{APP_VERSION}"
CHECK_TIMEOUT_S = 10
DOWNLOAD_TIMEOUT_S = 60
DOWNLOAD_CHUNK = 64 * 1024
PROGRESS_INTERVAL_S = 0.1  # a chunk-by-chunk report floods the Tk event queue
MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024  # a release zip is ~100 MB; well past that is not ours
OLD_SUFFIX = ".old"  # the previous exe, left behind on Windows until the next start

ASSETS = {
    "Windows": "soundboard-windows.zip",
    "Darwin": "soundboard-macos.zip",
    "Linux": "soundboard-linux.zip",
}
# What the zip should contain, per platform.
MEMBERS = {
    "Windows": "soundboard.exe",
    "Darwin": "soundboard.app",
    "Linux": "soundboard",
}


class UpdateError(Exception):
    """Something went wrong that the user should be told about."""


class Release:
    def __init__(self, version, tag, notes, asset_name, asset_url, asset_size):
        self.version = version
        self.tag = tag
        self.notes = notes
        self.asset_name = asset_name
        self.asset_url = asset_url
        self.asset_size = asset_size


def parse_version(text):
    """"v1.2.3" and "1.2.3" both become (1, 2, 3). Anything unparsable
    becomes (), which compares lower than every real version."""
    if not text:
        return ()
    text = str(text).strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for piece in text.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate, current=APP_VERSION):
    candidate, current = parse_version(candidate), parse_version(current)
    return bool(candidate) and candidate > current


def is_frozen():
    return getattr(sys, "frozen", False)


def can_self_update():
    """Whether apply() can replace this program in place."""
    return is_frozen() and platform.system() in ("Windows", "Linux")


def _request(url, accept="application/vnd.github+json"):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})


def check(timeout=CHECK_TIMEOUT_S, current=None):
    """The latest release if it is newer than this build, else None.
    Never raises: an update check is not worth an error dialog.

    `current` overrides the running version, which is what lets this be
    tested against the real API from a build that is already ahead of it.
    """
    try:
        with urllib.request.urlopen(_request(LATEST_URL), timeout=timeout) as response:
            data = json.load(response)
    except Exception:
        return None
    tag = data.get("tag_name") or ""
    if not is_newer(tag, current or APP_VERSION):
        return None
    wanted = ASSETS.get(platform.system())
    asset = next((a for a in data.get("assets", []) if a.get("name") == wanted), None)
    return Release(
        version=str(tag).lstrip("vV"),
        tag=tag,
        notes=(data.get("body") or "").strip(),
        asset_name=asset["name"] if asset else None,
        asset_url=asset["browser_download_url"] if asset else None,
        asset_size=asset.get("size", 0) if asset else 0,
    )


def check_async(on_done, timeout=CHECK_TIMEOUT_S):
    """check() on a worker thread. on_done is called with the Release or
    None, on that thread - marshal it back to Tk yourself."""
    thread = threading.Thread(target=lambda: on_done(check(timeout)), daemon=True)
    thread.start()
    return thread


def download(release, directory, on_progress=None, cancel=None):
    """Fetch the release asset into `directory`, returning its path.
    on_progress(done, total) is called as it goes; cancel() returning
    True stops it and removes the partial file."""
    if not release.asset_url:
        raise UpdateError(
            f"Release {release.tag} has no build for {platform.system()}."
        )
    destination = os.path.join(directory, release.asset_name)
    total = release.asset_size or 0
    done = 0
    reported_at = 0.0
    try:
        with urllib.request.urlopen(
            _request(release.asset_url, accept="application/octet-stream"),
            timeout=DOWNLOAD_TIMEOUT_S,
        ) as response:
            total = total or int(response.headers.get("Content-Length") or 0)
            with open(destination, "wb") as out:
                while True:
                    if cancel is not None and cancel():
                        raise UpdateError("Download cancelled.")
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_DOWNLOAD_BYTES:
                        raise UpdateError("The download is far larger than a release should be.")
                    out.write(chunk)
                    now = time.monotonic()
                    if on_progress is not None and (
                        done == total or now - reported_at >= PROGRESS_INTERVAL_S
                    ):
                        reported_at = now
                        on_progress(done, total)
    except UpdateError:
        _remove(destination)
        raise
    except Exception as e:
        _remove(destination)
        raise UpdateError(f"Could not download the update: {e}")
    if total and done != total:
        _remove(destination)
        raise UpdateError("The download ended early. Try again.")
    return destination


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def extract(zip_path, directory):
    """Unpack the one member we expect, refusing paths that escape."""
    member_name = MEMBERS.get(platform.system())
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            wanted = [n for n in names if n == member_name or n.startswith(member_name + "/")]
            if not wanted:
                raise UpdateError(
                    f"The download does not contain {member_name}; it may not be a Soundboard build."
                )
            for name in wanted:
                target = os.path.realpath(os.path.join(directory, name))
                if not target.startswith(os.path.realpath(directory) + os.sep):
                    raise UpdateError("The download contains a path outside the folder it unpacks to.")
            archive.extractall(directory, members=wanted)
    except zipfile.BadZipFile:
        raise UpdateError("The download is not a valid zip file.")
    return os.path.join(directory, member_name)


def current_program():
    """The file that would be replaced."""
    return os.path.realpath(sys.executable)


def clear_old_binary():
    """Remove what a previous Windows update left behind. Called at
    startup, when the old exe is no longer running and can be deleted."""
    if not is_frozen():
        return
    leftover = current_program() + OLD_SUFFIX
    if os.path.exists(leftover):
        _remove(leftover)


def apply(zip_path, relaunch=True, _program=None, _system=None):
    """Put the downloaded build in place of this one and restart.

    Returns the path that was replaced. Raises UpdateError and leaves
    the running program untouched if anything fails.
    """
    system = _system or platform.system()
    program = _program or current_program()
    try:
        # Next to the program, so the move into place stays on one
        # filesystem. An install directory we cannot write to fails here.
        staging = tempfile.mkdtemp(prefix="soundboard-update-", dir=os.path.dirname(program))
    except OSError as e:
        raise UpdateError(f"Could not write to the folder Soundboard is installed in: {e}")
    try:
        new_program = extract(zip_path, staging)
        if not os.path.exists(new_program):
            raise UpdateError("The update unpacked to nothing.")
        if system == "Linux":
            os.chmod(new_program, 0o755)
            # A rename over a running program is allowed: this process
            # keeps the inode it is already executing.
            os.replace(new_program, program)
        elif system == "Windows":
            old = program + OLD_SUFFIX
            _remove(old)
            # A running .exe cannot be written or deleted, but it can be
            # moved aside. If the second step fails, put it back.
            os.rename(program, old)
            try:
                os.replace(new_program, program)
            except Exception:
                os.rename(old, program)
                raise
        else:
            raise UpdateError(
                "Updating in place is not supported on this system; install the download by hand."
            )
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"Could not install the update: {e}")
    shutil.rmtree(staging, ignore_errors=True)
    if relaunch:
        relaunch_program(program, system)
    return program


def child_environment():
    """A copy of our environment with PyInstaller's onefile markers
    removed.

    A onefile bootloader unpacks the program into a temporary directory
    and tells the Python process it starts where that is, through the
    environment. Those variables are still set while we run, so a child
    started from them believes it has already been unpacked and uses our
    temporary directory instead of its own - which for the new build
    means running the old build's files, until the old process exits and
    deletes them underneath it.
    """
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("_PYI_") or name == "_MEIPASS2":  # _MEIPASS2 predates PyInstaller 6
            del env[name]
    return env


def relaunch_program(program, system=None):
    """Start the new build and leave. Detached, so it survives this
    process exiting."""
    system = system or platform.system()
    kwargs = {"close_fds": True, "env": child_environment()}
    if system == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([program], **kwargs)


def reveal(path):
    """Show a downloaded file in the system file manager, for the
    platforms that finish the install by hand."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", "-R", path])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass
