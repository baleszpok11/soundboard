"""Text to speech, through whatever the machine already has.

pyttsx3 drives SAPI5 on Windows, NSSpeechSynthesizer on macOS and
eSpeak on Linux, all offline and none of them paid, which is what this
project allows. A machine without any of them is normal (a bare Linux
without eSpeak), so nothing here raises on import: available() says
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


def _engine():
    if pyttsx3 is None:
        raise RuntimeError(f"Speech is unavailable: {IMPORT_ERROR}")
    # Engine(), not init(): init() caches one engine per driver and hands
    # the same one to every caller, including threads.
    return pyttsx3.Engine()


def unavailable_reason():
    """None when a line can be spoken, otherwise why it can't be."""
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
        return []
    return found[:MAX_VOICES]


def default_rate():
    try:
        engine = _engine()
        return int(engine.getProperty("rate"))
    except Exception:
        return DEFAULT_RATE


def synthesize(text, path, voice_id=None, rate=DEFAULT_RATE):
    """Speak `text` into a wav at `path`. Runs on a worker thread: on a
    long line this takes a moment, and pyttsx3 blocks while it works."""
    engine = _engine()
    if voice_id:
        try:
            engine.setProperty("voice", voice_id)
        except Exception:
            pass  # a voice that went away since the list was built
    engine.setProperty("rate", max(RATE_MIN, min(RATE_MAX, int(rate))))
    engine.save_to_file(text, path)
    engine.runAndWait()
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError("The speech engine produced no audio.")
    return path


def temp_wav():
    handle, path = tempfile.mkstemp(prefix="soundboard-tts-", suffix=".wav")
    os.close(handle)
    return path
