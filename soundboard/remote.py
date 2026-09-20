"""A local control channel, so something other than the window can fire
a sound: a Stream Deck plugin, an OBS script, a chat bot, a shell alias.

The channel is a TCP socket on the loopback address carrying one JSON
object per line, and a JSON object back per line. Loopback is not a
default here, it is the whole design: a socket bound to anything else
turns a soundboard into a noise generator anyone on the network can
fire, and there is no token or password to add that would make that
safe - the same reasoning as the rule against shipping a GitHub token.
HOST is a constant for that reason and nothing reads it from the config.

It is off until switched on, in Settings.

The protocol, which is the part other people write against:

    {"command": "play", "sound": "Airhorn"}
    {"ok": true, "index": 0, "name": "Airhorn"}

Every reply has "ok". A failure carries "error" with a sentence meant to
be shown to whoever is driving. "protocol" in the ping reply is bumped
if a command's meaning ever changes; new commands and new fields do not
bump it, so a client should ignore fields it does not know.

Sounds are addressed by index or by name, and index wins when both are
given. Names are not unique on a board - nothing stops two clips called
"Airhorn" - so a name that matches more than one entry is an error that
says to use the index, rather than a silent guess at which one was
meant. Indexes are positions in the active profile, counted from 0, and
they are the profile's own order, not what the board happens to be
showing: a search or a sort in the window must not move what a Stream
Deck button fires.

Commands arrive on a socket thread and almost everything they touch is
Tk or engine state, so RemoteMixin hands each one to the Tk thread and
waits for the answer.
"""

import json
import socket
import threading

from .config import APP_VERSION, REMOTE_PORT

# Never configurable. See the module docstring. The port is
# (config.REMOTE_PORT), which is only a default: the address it is
# reachable at is part of what a client is written against, so it is in
# the config where it can be moved off something else that wants it.
HOST = "127.0.0.1"
PROTOCOL = 1

# How long a client waits for the window to answer, and how long the
# socket thread waits for the Tk thread. A command is a method call
# behind a queue; anything near this means the window is wedged.
CALL_TIMEOUT_S = 5.0
# A line longer than this is not one of ours. Read with a limit so a
# client that opens a connection and streams never stops cannot take the
# app's memory with it.
MAX_LINE = 64 * 1024
# Several clients at once is normal - a Stream Deck plugin and a script.
# A few hundred is something else, and each one costs a thread.
MAX_CLIENTS = 8


class RemoteError(Exception):
    """A command that cannot be carried out, with the sentence to say so."""


# -- the server --------------------------------------------------------


class RemoteServer:
    """Accepts local connections and passes each line to `handler`.

    `handler(payload)` is called on the connection's own thread and must
    return a dict. It is RemoteMixin's job to get from there to the Tk
    thread; nothing in this class knows what a command means.
    """

    def __init__(self, handler, port=REMOTE_PORT):
        self.handler = handler
        self.port = int(port)
        self._socket = None
        self._thread = None
        self._clients = set()
        self._lock = threading.Lock()
        self._stopping = False

    @property
    def running(self):
        return self._socket is not None

    def start(self):
        """Bind and listen. Raises OSError when the port is taken, which
        is the one failure worth telling the user about: something else
        is on it, or a previous copy of the app still is."""
        if self._socket is not None:
            return
        self._stopping = False
        server = socket.create_server((HOST, self.port), backlog=MAX_CLIENTS)
        server.settimeout(0.5)  # so stop() is noticed without a connection
        self._socket = server
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping = True
        server, self._socket = self._socket, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            # Shut the read down under the thread sitting in recv, rather
            # than waiting for a client that may never send anything.
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

    def _accept_loop(self):
        while not self._stopping:
            server = self._socket
            if server is None:
                return
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # closed under us by stop(); that is the exit
            with self._lock:
                if len(self._clients) >= MAX_CLIENTS:
                    client.close()
                    continue
                self._clients.add(client)
            threading.Thread(target=self._serve, args=(client,),
                             daemon=True).start()

    def _serve(self, client):
        try:
            with client.makefile("rwb") as stream:
                while not self._stopping:
                    line = stream.readline(MAX_LINE)
                    if not line:
                        return  # the client hung up
                    reply = self._answer(line)
                    stream.write(json.dumps(reply).encode("utf-8") + b"\n")
                    stream.flush()
        except OSError:
            return  # a client that goes away mid-command is not an error
        finally:
            with self._lock:
                self._clients.discard(client)
            try:
                client.close()
            except OSError:
                pass

    def _answer(self, line):
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"ok": False, "error": "that is not JSON"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "a command is a JSON object"}
        try:
            return self.handler(payload)
        except RemoteError as problem:
            return {"ok": False, "error": str(problem)}
        except Exception as problem:  # a bug here must not kill the thread
            return {"ok": False, "error": f"{type(problem).__name__}: {problem}"}


# -- the client --------------------------------------------------------


def send(payload, port=REMOTE_PORT, timeout=CALL_TIMEOUT_S):
    """Send one command to a running Soundboard and return its reply.

    Raises OSError when nothing is listening, which is what "the app is
    not running, or its remote control is off" looks like from here.
    """
    with socket.create_connection((HOST, int(port)), timeout) as sock:
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        with sock.makefile("rb") as stream:
            line = stream.readline(MAX_LINE)
    if not line:
        raise OSError("the app closed the connection without answering")
    return json.loads(line.decode("utf-8"))


# -- the command line --------------------------------------------------

# Flag -> (command, name of the argument it takes, or None).
CLI_FLAGS = {
    "--play": ("play", "sound"),
    "--stop": ("stop", "sound"),
    "--stop-all": ("stop_all", None),
    "--pause": ("pause", None),
    "--next": ("next", None),
    "--previous": ("previous", None),
    "--mute": ("mute", "value"),
    "--volume": ("volume", "value"),
    "--profile": ("profile", "name"),
    "--status": ("status", None),
    "--sounds": ("sounds", None),
    "--profiles": ("profiles", None),
}

CLI_HELP = """Control a running Soundboard:

  --play NAME|INDEX     play a sound
  --stop NAME|INDEX     stop that sound
  --stop-all            stop everything
  --pause               pause, or let a paused board go on
  --next, --previous    step through the board
  --mute [on|off]       the microphone; no value toggles it
  --volume 0-150        the soundboard volume, in percent
  --profile NAME        switch profile
  --status              what is playing
  --sounds, --profiles  what there is to play

Prints the reply as JSON and exits 0 when the command worked. Needs the
remote control switched on in the app's Settings; it talks to the
running window rather than starting a second one."""


# Answered here rather than by opening a window, since nobody asking for
# help wants the app.
CLI_HELP_FLAGS = ("--help", "-h")


def is_cli(args):
    """Whether these arguments are a command for a running app rather
    than a request to open the window."""
    return any(arg in CLI_FLAGS or arg in CLI_HELP_FLAGS for arg in args)


def _payload_for(args):
    """Turn the arguments into one command, or raise RemoteError."""
    flags = [arg for arg in args if arg in CLI_FLAGS]
    if len(flags) > 1:
        raise RemoteError(f"one command at a time, not {' and '.join(flags)}")
    flag = flags[0]
    command, argument = CLI_FLAGS[flag]
    payload = {"command": command}
    rest = args[args.index(flag) + 1:]
    value = rest[0] if rest and not rest[0].startswith("--") else None
    if argument is None:
        return payload
    if command in ("play", "stop"):
        if value is None:
            raise RemoteError(f"{flag} needs a sound name or index")
        # A board can have a sound called "7", so this is a guess - but
        # it is the guess someone typing a number is making too, and
        # --play with a name that is a number can still be sent as JSON.
        if value.lstrip("-").isdigit():
            payload["index"] = int(value)
        else:
            payload["sound"] = value
    elif command == "mute":
        payload["value"] = _switch_value(flag, value)
    elif command == "volume":
        if value is None or not value.lstrip("-").isdigit():
            raise RemoteError("--volume needs a number, such as --volume 80")
        payload["value"] = int(value)
    elif command == "profile":
        if value is None:
            raise RemoteError("--profile needs a profile name")
        payload["name"] = value
    return payload


def _switch_value(flag, value):
    """on/off/toggle, where leaving it out means toggle."""
    if value is None or value == "toggle":
        return None
    if value in ("on", "true", "1", "yes"):
        return True
    if value in ("off", "false", "0", "no"):
        return False
    raise RemoteError(f"{flag} takes on, off or nothing at all")


def run_cli(args, port=REMOTE_PORT):
    """Send one command and print the reply. Returns an exit code."""
    if any(arg in CLI_HELP_FLAGS for arg in args):
        print(CLI_HELP)
        return 0
    try:
        payload = _payload_for(args)
    except RemoteError as problem:
        print(f"soundboard: {problem}")
        return 2
    try:
        reply = send(payload, port)
    except OSError as problem:
        print(json.dumps({
            "ok": False,
            "error": f"no Soundboard answering on {HOST}:{port} ({problem}). "
                     "Start it, and switch its remote control on in Settings.",
        }, indent=2))
        return 1
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


# -- the app's side ----------------------------------------------------


class RemoteMixin:
    """The commands themselves, and getting them onto the Tk thread."""

    def _start_remote(self):
        """Start the control channel if it is switched on. Returns the
        error to show, or None when there is nothing to say."""
        self._stop_remote()
        if not self.config.get("remote_control"):
            return None
        server = RemoteServer(self._remote_command,
                              self.config.get("remote_port", REMOTE_PORT))
        try:
            server.start()
        except OSError as problem:
            self.config["remote_control"] = False
            return (f"Remote control could not listen on {HOST}:{server.port} "
                    f"({problem}). Another program may be using that port, or "
                    f"another copy of Soundboard may still be running.")
        self.remote_server = server
        return None

    def _stop_remote(self):
        server = getattr(self, "remote_server", None)
        if server is not None:
            server.stop()
        self.remote_server = None

    # -- dispatch ------------------------------------------------------

    def _remote_command(self, payload):
        """Called on a socket thread: run the command where the rest of
        the app lives and wait for its answer.

        Everything a command touches - the board, the engine, the
        widgets - belongs to the Tk thread, the same reason the hotkey
        callbacks hand themselves over with after(). The wait is bounded
        because a socket thread blocked forever on a window that is
        closing would keep the process alive.
        """
        done = threading.Event()
        answer = {}

        def run():
            try:
                answer["reply"] = self._run_remote(payload)
            except RemoteError as problem:
                answer["reply"] = {"ok": False, "error": str(problem)}
            except Exception as problem:
                answer["reply"] = {"ok": False,
                                   "error": f"{type(problem).__name__}: {problem}"}
            finally:
                done.set()

        try:
            self.root.after(0, run)
        except Exception:
            return {"ok": False, "error": "Soundboard is closing"}
        if not done.wait(CALL_TIMEOUT_S):
            return {"ok": False, "error": "Soundboard did not answer in time"}
        return answer["reply"]

    def _run_remote(self, payload):
        command = payload.get("command")
        handler = getattr(self, f"_remote_{command}", None) if command else None
        if handler is None or command.startswith("_"):
            raise RemoteError(f"unknown command {command!r}")
        return handler(payload)

    # -- resolving a sound ---------------------------------------------

    def _remote_entry(self, payload):
        """The (index, sound) a command is about. Index wins over name."""
        sounds = self.sounds
        if payload.get("index") is not None:
            index = payload["index"]
            if not isinstance(index, int) or isinstance(index, bool):
                raise RemoteError("index must be a whole number")
            if not 0 <= index < len(sounds):
                raise RemoteError(
                    f"no sound at index {index}; this profile has "
                    f"{len(sounds)}")
            return index, sounds[index]
        name = payload.get("sound")
        if not isinstance(name, str) or not name.strip():
            raise RemoteError("say which sound, by \"sound\" or \"index\"")
        wanted = name.strip().casefold()
        matches = [i for i, sound in enumerate(sounds)
                   if str(sound.get("name", "")).casefold() == wanted]
        if not matches:
            raise RemoteError(f"no sound called {name!r} in this profile")
        if len(matches) > 1:
            raise RemoteError(
                f"{len(matches)} sounds are called {name!r} (indexes "
                f"{', '.join(str(i) for i in matches)}); use \"index\"")
        return matches[0], sounds[matches[0]]

    # -- the commands --------------------------------------------------

    def _remote_ping(self, payload):
        return {"ok": True, "version": APP_VERSION, "protocol": PROTOCOL}

    def _remote_play(self, payload):
        index, sound = self._remote_entry(payload)
        if not sound.get("enabled", True):
            raise RemoteError(f"{sound.get('name')!r} is switched off")
        self.play_sound(sound)
        return {"ok": True, "index": index, "name": sound.get("name")}

    def _remote_stop(self, payload):
        index, sound = self._remote_entry(payload)
        self.stop_sound(sound)
        return {"ok": True, "index": index, "name": sound.get("name")}

    def _remote_stop_all(self, payload):
        self.audio_engine.stop_all()
        return {"ok": True}

    def _remote_pause(self, payload):
        self.toggle_pause()
        return {"ok": True, "paused": bool(self.audio_engine.paused_keys())}

    def _remote_next(self, payload):
        return {"ok": True, "played": self.play_next()}

    def _remote_previous(self, payload):
        return {"ok": True, "played": self.play_previous()}

    def _remote_mute(self, payload):
        value = payload.get("value")
        if value is None:
            value = not self.config.get("mic_muted")
        value = bool(value)
        if value:
            self.mute_checkbox.select()
        else:
            self.mute_checkbox.deselect()
        # Not _toggle_mute_from_hotkey: that is for the listener thread
        # and defers with after(), which would mean answering before the
        # change had happened.
        self._on_toggle_mute()
        return {"ok": True, "muted": bool(self.config.get("mic_muted"))}

    def _remote_volume(self, payload):
        value = payload.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RemoteError("volume is a number of percent, such as 80")
        return {"ok": True,
                "volume": self.set_volume_percent("sound_volume", value)}

    def _remote_profile(self, payload):
        name = payload.get("name")
        names = self._profile_names()
        if name not in names:
            raise RemoteError(
                f"no profile called {name!r}; there is "
                f"{', '.join(repr(other) for other in names)}")
        if name != self.config["active_profile"]:
            self._switch_profile(name)
        return {"ok": True, "profile": name}

    def _remote_profiles(self, payload):
        return {"ok": True, "profiles": self._profile_names(),
                "active": self.config["active_profile"]}

    def _remote_sounds(self, payload):
        return {"ok": True, "profile": self.config["active_profile"],
                "sounds": [{"index": index,
                            "name": sound.get("name"),
                            "enabled": bool(sound.get("enabled", True)),
                            "category": sound.get("category"),
                            "favourite": bool(sound.get("favourite")),
                            "hotkey": sound.get("hotkey")}
                           for index, sound in enumerate(self.sounds)]}

    def _remote_status(self, payload):
        playing = self.audio_engine.active_keys()
        paused = self.audio_engine.paused_keys()
        entries = []
        for index, sound in enumerate(self.sounds):
            key = self._sound_key(sound)
            if key in playing:
                entries.append({"index": index, "name": sound.get("name"),
                                "progress": round(playing[key], 4),
                                "paused": key in paused})
        return {"ok": True,
                "profile": self.config["active_profile"],
                "playing": entries,
                "paused": bool(paused),
                "muted": bool(self.config.get("mic_muted")),
                "volume": self.config.get("sound_volume"),
                "mic_volume": self.config.get("mic_volume")}
