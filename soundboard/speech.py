"""Text to speech, through whatever the machine already has.

pyttsx3 drives SAPI5 on Windows and eSpeak on Linux, both offline and
neither paid, which is what this project allows. macOS goes to its own
say command instead: pyttsx3's driver there only works on the main
thread, and the Speak tab cannot block the UI for the length of the
line. A machine without any of them is normal (a bare Linux without
eSpeak), so nothing here raises on import: unavailable_reason() says
whether speaking is possible and why not, and the tab shows that
instead of failing when the button is pressed.

An engine is built per call rather than kept, and built directly rather
than through pyttsx3.init(), which hands back a cached engine per driver:
sharing one engine between the Tk thread that lists the voices and the
worker thread that speaks leaves its run loop stalled, and the line is
never spoken. On Windows the SAPI driver also expects COM on the thread
it runs on, which a per-call engine gets for free.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import pyttsx3
except Exception as e:  # pragma: no cover - depends on the machine
    pyttsx3 = None
    IMPORT_ERROR = e
else:
    IMPORT_ERROR = None

DEFAULT_RATE = 175
RATE_MIN = 80
RATE_MAX = 320
# A voice list of every language eSpeak ships is not a choice anyone can
# make; the app shows this many and the rest stay reachable in the OS.
MAX_VOICES = 40

IS_MACOS = sys.platform == "darwin"
# say writes whatever the extension asks for; this is what the rest of
# the app reads back.
SAY_FORMAT = "LEF32@22050"
# A header and a handful of frames; every real line clears this easily.
MIN_AUDIO_BYTES = 8192
# "Eddy               en_GB    # Hello!" - the name is everything before
# the locale column.
_SAY_LINE = re.compile(r"^(.+?)\s+[a-z]{2}(?:_[A-Z]{2})?\s")


def _engine():
    if pyttsx3 is None:
        raise RuntimeError(f"Speech is unavailable: {IMPORT_ERROR}")
    # Engine(), not init(): init() caches one engine per driver and hands
    # the same one to every caller, including threads.
    return pyttsx3.Engine()


def _say_voices():
    """(name, name) for each voice say knows about. say -v takes the name
    it prints here as well as the identifier pyttsx3 reports, so either
    can be stored."""
    try:
        listed = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    found = []
    for line in listed.stdout.splitlines():
        match = _SAY_LINE.match(line)
        if match:
            name = match.group(1).strip()
            found.append((name, name))
    return found


def _say(text, path, voice_id, rate):
    """Speak into a wav with macOS's own say.

    pyttsx3's macOS driver writes a 4096-byte stub and returns at once
    when it is called off the main thread, which is where the Speak tab
    has to call it - the UI cannot block for the length of the line. say
    is a subprocess, so it does not care which thread started it, and it
    takes the same voice identifiers pyttsx3 reports.
    """
    command = ["say", "-o", path, "--data-format=" + SAY_FORMAT,
               "-r", str(max(RATE_MIN, min(RATE_MAX, int(rate))))]
    if voice_id:
        command += ["-v", voice_id]
    # -- so a line that opens with a dash is text, not an option.
    command += ["--", text]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip()
        if voice_id and "voice" in message.lower():
            # The saved voice was removed from the system; the default
            # still speaks, which beats refusing the line.
            return _say(text, path, None, rate)
        raise RuntimeError(message or "say could not speak that line.")


def unavailable_reason():
    """None when a line can be spoken, otherwise why it can't be."""
    if IS_MACOS:
        if shutil.which("say") is None:
            return "Speech is unavailable on this machine (say was not found)."
        return None
    try:
        _engine()
    except Exception as e:
        if "espeak" in str(e).lower():
            return ("No speech engine was found. On Linux, install eSpeak "
                    "(for example: sudo apt install espeak-ng) and reopen "
                    "Soundboard.")
        return f"Speech is unavailable on this machine ({e})."
    return None


def voices():
    """(id, name) for each installed voice, or an empty list."""
    try:
        engine = _engine()
        found = [(v.id, (v.name or v.id).strip()) for v in engine.getProperty("voices")]
    except Exception:
        found = []
    if not found and IS_MACOS:
        found = _say_voices()
    return found[:MAX_VOICES]


def default_rate():
    try:
        engine = _engine()
        return int(engine.getProperty("rate"))
    except Exception:
        return DEFAULT_RATE


def synthesize(text, path, voice_id=None, rate=DEFAULT_RATE):
    """Speak `text` into a wav at `path`. Runs on a worker thread: on a
    long line this takes a moment, and the engine blocks while it works."""
    if IS_MACOS:
        _say(text, path, voice_id, rate)
    else:
        engine = _engine()
        if voice_id:
            try:
                engine.setProperty("voice", voice_id)
            except Exception:
                pass  # a voice that went away since the list was built
        engine.setProperty("rate", max(RATE_MIN, min(RATE_MAX, int(rate))))
        engine.save_to_file(text, path)
        engine.runAndWait()
    # A size of its own is not proof of audio: the macOS driver used to
    # leave a 4096-byte header here and the tab played silence and called
    # it done. Anything too short to hold a syllable is a failure.
    if not os.path.isfile(path) or os.path.getsize(path) < MIN_AUDIO_BYTES:
        raise RuntimeError("The speech engine produced no audio.")
    return path


def temp_wav():
    handle, path = tempfile.mkstemp(prefix="soundboard-tts-", suffix=".wav")
    os.close(handle)
    return path
