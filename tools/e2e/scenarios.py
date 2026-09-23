"""What the harness actually checks.

Each scenario is a generator taking a Board. It yields between steps, and
every yield gives Tk a tick to lay out whatever the last step changed.
Add one by writing the function and listing it in SCENARIOS.
"""

import json
import socket
import threading

import customtkinter as ctk

from soundboard import remote


def _labelled(widget, fallback):
    """A widget's own text if it has some, so a failure names the button
    the reader can see rather than a class and an address."""
    try:
        text = widget.cget("text")
    except Exception:
        text = None
    return f"{fallback} {text!r}" if text else fallback


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


def board_opens(board):
    """The first thing anyone sees: a board with its sounds on it.

    Covers the case where the rows exist, are mapped, are the right
    height and are one pixel wide, which looks exactly like an empty
    board and is invisible to any check that only asks whether a widget
    is mapped.
    """
    board.shot("board-opens")
    board.expect_visible(board.list_frame, "sound list")
    board.expect_drawn(board.list_frame, "the sound list at startup")
    board.expect_rows_full_width()
    board.check(
        len(board.rows_on_screen) > 0,
        "the scroller built no rows at all for a board that has sounds")
    # Nothing on a board that never asked for it should be listening on
    # a socket.
    board.check(
        board.app.remote_server is None,
        "the remote control was listening on a board that never switched it on")
    yield


def nothing_is_clipped(board):
    """Every control on the board tab got at least the width it asked
    for, and every control that was added to a wrapping row is actually
    on screen.

    The second half is not redundant. expect_fits has to skip unmapped
    widgets - a hidden widget's size is not a layout fact - so a control
    that was never laid out at all passes it silently, which is exactly
    how a row that had lost four of its five controls came back clean.
    """
    # Through the package, the way the app imports it: imported as a
    # bare module it is a second, unrelated class object and every
    # isinstance below is quietly False.
    from soundboard.flow_row import FlowRow

    board.shot("clipping")
    for widget in _descendants(board.board_tab):
        if isinstance(widget, (ctk.CTkButton, ctk.CTkLabel)):
            board.expect_fits(widget, _labelled(widget, type(widget).__name__))
        if isinstance(widget, FlowRow):
            missing = [_labelled(child, type(child).__name__)
                       for child, _, _ in widget._items
                       if not child.winfo_ismapped()]
            board.check(
                not missing,
                f"a control row left {len(missing)} of {len(widget._items)} "
                f"of its controls unplaced: {', '.join(missing)}")
    yield


def settings_button(board):
    """The board's Settings button opens the settings tab.

    It used to open the panel inline above the list, which unmapped the
    board underneath it (#150). Whatever it does, the board has to be
    intact when you come back to it.
    """
    before = board.snapshot()
    board.shot("board-before-settings")
    yield

    board.click_settings()
    yield
    board.shot("settings-opened")
    board.check(board.app.tabview.get() == "Settings",
                "the Settings button did not open the settings")
    board.expect_visible(board.app.settings_panel, "settings panel")
    yield

    board.open_tab("Soundboard")
    yield
    board.shot("board-after-settings")
    board.expect_hidden(board.app.settings_panel, "settings panel")
    board.expect_visible(board.list_frame, "sound list")
    board.expect_rows_full_width()
    board.expect_drawn(board.list_frame, "the sound list, back from the settings")
    for widget in board.widgets_below_settings():
        board.expect_visible(widget, f"{type(widget).__name__} on the board")
    board.expect_restored(before, "a trip to the settings and back")
    yield


def settings_tab_round_trip(board):
    """The panel's other home. Leaving the tab and coming back has to
    leave the board exactly as it was."""
    before = board.snapshot()
    yield

    board.open_tab("Settings")
    yield
    board.shot("settings-tab")
    board.expect_visible(board.app.settings_panel, "settings panel on its own tab")
    yield

    board.open_tab("Soundboard")
    yield
    board.shot("back-on-board")
    board.expect_visible(board.list_frame, "sound list")
    board.expect_rows_full_width()
    board.expect_drawn(board.list_frame, "the sound list after a tab round trip")
    board.expect_restored(before, "a round trip through the Settings tab")
    yield


def search_and_clear(board):
    """Filtering rebuilds the slice on screen, which is the other path
    that sets the list's shape."""
    before = board.snapshot()
    yield

    board.search("air")
    yield
    board.shot("searched")
    board.expect_visible(board.list_frame, "sound list while searching")
    board.expect_rows_full_width()
    yield

    board.search("")
    yield
    board.shot("search-cleared")
    board.expect_rows_full_width()
    board.expect_restored(before, "clearing the search box")
    yield


def organising_the_board(board):
    """Favourites, sorting, categories and play counts.

    The board is addressed by profile index throughout - what a row
    plays must not depend on where the sort happens to have put it - so
    each step checks the order on screen and then that the right sound
    is still behind the right row.
    """
    app = board.app
    names = lambda: [s["name"] for _, s in app._visible]
    started = names()
    board.check(started == [s["name"] for s in app.sounds],
                f"the board did not open in its own order: {started}")
    yield

    # A favourite leads, whatever else is going on.
    app.set_favourite(app.sounds.index(
        next(s for s in app.sounds if s["name"] == "Vine Boom")), True)
    yield
    board.check(names()[0] == "Vine Boom",
                f"a favourite did not come first: {names()}")
    board.shot("organise-favourite")
    yield

    # Sorting by name keeps the favourite at the top and orders the rest.
    app._on_sort_change("Name")
    yield
    rest = names()[1:]
    board.check(rest == sorted(rest),
                f"sorting by name did not order the rest: {rest}")
    board.check(names()[0] == "Vine Boom",
                f"the favourite stopped leading under a sort: {names()}")
    yield

    # A row still plays the sound it shows, not the one at its index.
    first_index, first_sound = app._visible[0]
    board.check(app.sounds[first_index] is first_sound,
                "a sorted row points at a different sound than it shows")
    yield

    # A category narrows the board to itself.
    app._on_sort_change("My order")
    for name in ("Airhorn", "Applause"):
        app.set_category(app.sounds.index(
            next(s for s in app.sounds if s["name"] == name)), "Stingers")
    yield
    board.check(sorted(board.app.categories()) == ["Stingers"],
                f"the categories in use are wrong: {app.categories()}")
    app._on_category_filter("Stingers")
    yield
    board.check(sorted(names()) == ["Airhorn", "Applause"],
                f"the category filter showed {names()}")
    board.expect_rows_full_width()
    board.shot("organise-category")
    yield

    app._on_category_filter("All")
    yield
    board.check(len(names()) == len(app.sounds),
                f"clearing the filter left {len(names())} of {len(app.sounds)}")
    yield

    # Playing counts, and the count survives a rebuild of the list.
    target = next(s for s in app.sounds if s["name"] == "Boing")
    before = int(target.get("plays") or 0)
    app.play_sound(target)
    yield
    board.check(int(target.get("plays") or 0) == before + 1,
                f"playing did not count: {before} -> {target.get('plays')}")
    board.check(target.get("last_played"), "playing did not record a time")
    yield

    # Most played puts it first, behind the favourite.
    app._on_sort_change("Most played")
    yield
    board.check(names()[1] == "Boing",
                f"most played did not lead after the favourite: {names()}")
    board.shot("organise-sorted")
    yield


def colours_and_defaults(board):
    """A sound's colour reaches the grid, and a board that has never
    been organised looks exactly as it did before any of this."""
    app = board.app
    for sound in app.sounds:
        board.check(sound.get("plays") == 0 and not sound.get("favourite")
                    and sound.get("category") is None,
                    f"{sound['name']} did not default to unorganised")
    board.check(app.config["sound_sort"] == "custom",
                "a new board did not default to its own order")
    yield

    app._on_view_change("Grid")
    yield
    board.expect_drawn(board.list_frame, "the grid")
    board.shot("organise-grid-plain")
    yield

    from soundboard.theme import TILE_COLOURS
    for name, colour in zip(["Airhorn", "Applause", "Boing"], TILE_COLOURS):
        app.set_colour(app.sounds.index(
            next(s for s in app.sounds if s["name"] == name)), colour)
    yield
    board.expect_drawn(board.list_frame, "the grid with coloured tiles")
    board.shot("organise-grid-coloured")
    # An unknown colour must not break a tile - a board from a newer
    # version can carry one.
    app.set_colour(0, "Chartreuse")
    yield
    board.expect_drawn(board.list_frame, "the grid with an unknown colour")
    yield


def _free_port():
    """A port nothing is on, picked in this subprocess so two scenarios
    running one after another cannot collide."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ask(port, replies, name, payload, raw=None):
    """Send one command from a worker thread.

    Not from this one: the scenario runs on the Tk thread, and the
    command needs that same thread to answer it - sending from here
    would wait for a reply that cannot arrive until we stop waiting.
    """
    def work():
        try:
            if raw is not None:
                with socket.create_connection((remote.HOST, port), 5) as sock:
                    sock.sendall(raw)
                    with sock.makefile("rb") as stream:
                        line = stream.readline(remote.MAX_LINE)
                replies[name] = json.loads(line.decode())
            else:
                replies[name] = remote.send(payload, port)
        except Exception as problem:
            replies[name] = {"ok": False, "error": f"{type(problem).__name__}: {problem}"}
    threading.Thread(target=work, daemon=True).start()


def _settled(replies, name, tries=10):
    """Tick until the reply lands, rather than for a fixed time."""
    for _ in range(tries):
        if name in replies:
            return
        yield


def remote_control(board):
    """The local control channel: a second process drives the board.

    Everything here goes over a real socket from a real second thread,
    because that is the part that cannot be checked by calling the
    methods directly - the commands arrive where Tk may not be touched
    and have to be handed over to the thread that owns the window.
    """
    app = board.app
    port = app.config["remote_port"]
    replies = {}

    def call(name, payload=None, raw=None):
        _ask(port, replies, name, payload, raw)

    board.check(app.remote_server is not None,
                "the remote control did not start although it was switched on")
    if app.remote_server is None:
        return
    # The one property that cannot be allowed to regress: bound to
    # loopback, never to every interface on the machine.
    address = app.remote_server._socket.getsockname()[0]
    board.check(address == "127.0.0.1",
                f"the control channel is listening on {address}, not loopback")

    # Every command has to run on the thread that owns the window, not
    # on the socket thread it arrived on. This is asserted rather than
    # inferred: with the marshalling taken out, the whole of the rest of
    # this scenario still passes - Tk here answers a call from another
    # thread often enough that nothing visibly breaks, which is what
    # makes that class of bug so quiet.
    threads = []
    answer_command = app._run_remote

    def watched(payload):
        threads.append(threading.current_thread() is threading.main_thread())
        return answer_command(payload)

    app._run_remote = watched
    yield

    call("ping", {"command": "ping"})
    yield from _settled(replies, "ping")
    board.check(replies["ping"].get("ok") and replies["ping"].get("version"),
                f"ping did not answer: {replies['ping']}")
    board.check(threads and all(threads),
                "a command ran on the socket thread instead of the Tk thread")
    yield

    call("sounds", {"command": "sounds"})
    yield from _settled(replies, "sounds")
    listed = [entry["name"] for entry in replies["sounds"].get("sounds", [])]
    board.check(listed == [s["name"] for s in app.sounds],
                f"the board it listed is not the board: {listed}")
    yield

    # Playing by name, checked through the app's own count rather than
    # through the audio device, which a test machine may not have.
    airhorn = app.sounds[0]
    before = int(airhorn.get("plays") or 0)
    call("play", {"command": "play", "sound": "airhorn"})  # case does not matter
    yield from _settled(replies, "play")
    board.check(replies["play"].get("ok") and replies["play"].get("index") == 0,
                f"play by name failed: {replies['play']}")
    board.check(int(airhorn.get("plays") or 0) == before + 1,
                "a remote play did not go through the board's own play path")
    board.shot("remote-played")
    yield

    # Two sounds of one name is an error that says what to do, not a
    # guess at which of them was meant.
    app.sounds[1]["name"] = "Airhorn"
    app._refresh_sound_list()
    yield
    call("ambiguous", {"command": "play", "sound": "Airhorn"})
    yield from _settled(replies, "ambiguous")
    message = replies["ambiguous"].get("error", "")
    board.check(not replies["ambiguous"].get("ok") and "index" in message,
                f"an ambiguous name was not refused: {replies['ambiguous']}")
    yield

    # ... and the index still works, which is the way out the error names.
    call("by_index", {"command": "play", "index": 1})
    yield from _settled(replies, "by_index")
    board.check(replies["by_index"].get("ok") and replies["by_index"]["index"] == 1,
                f"play by index failed: {replies['by_index']}")
    board.check(int(app.sounds[1].get("plays") or 0) == 1,
                "play by index played something else")
    yield

    # An index that is not on the board, and a command that does not
    # exist: both answered, neither fatal.
    call("missing", {"command": "play", "index": 99})
    yield from _settled(replies, "missing")
    board.check(not replies["missing"].get("ok"),
                f"index 99 was accepted: {replies['missing']}")
    call("nonsense", {"command": "explode"})
    yield from _settled(replies, "nonsense")
    board.check(not replies["nonsense"].get("ok"),
                f"an unknown command was accepted: {replies['nonsense']}")
    call("garbage", raw=b"not json at all\n")
    yield from _settled(replies, "garbage")
    board.check(not replies["garbage"].get("ok"),
                f"a line that is not JSON was accepted: {replies['garbage']}")
    yield

    # A volume set from outside has to move the slider too, or the
    # window shows a setting the board is not using.
    call("volume", {"command": "volume", "value": 55})
    yield from _settled(replies, "volume")
    slider = app._volume_rows["sound_volume"][0]
    board.check(app.config["sound_volume"] == 55 and int(slider.get()) == 55,
                f"volume: config {app.config['sound_volume']}, slider {slider.get()}")
    yield

    # Switching profile from outside is the same switch the menu makes.
    app.config["profiles"].append({"name": "Second", "sounds": []})
    call("profile", {"command": "profile", "name": "Second"})
    yield from _settled(replies, "profile")
    board.check(app.config["active_profile"] == "Second",
                f"the profile did not switch: {replies['profile']}")
    board.check(app.profile_var.get() == "Second",
                "the profile menu still shows the old profile")
    board.shot("remote-profile")
    yield

    call("back", {"command": "profile", "name": "Default"})
    yield from _settled(replies, "back")
    board.check(app.config["active_profile"] == "Default",
                "the profile did not switch back")
    yield

    # Stop everything, then ask what is playing.
    call("stop_all", {"command": "stop_all"})
    yield from _settled(replies, "stop_all")
    call("status", {"command": "status"})
    yield from _settled(replies, "status")
    status = replies["status"]
    board.check(status.get("ok") and status.get("profile") == "Default"
                and status.get("volume") == 55 and status.get("playing") == [],
                f"status is wrong after a stop: {status}")
    yield

    # Moving the port moves a channel that is already running, rather
    # than saving a number that only means anything at the next start.
    moved = _free_port()
    app.remote_port_entry.delete(0, "end")
    app.remote_port_entry.insert(0, str(moved))
    app._on_remote_port()
    yield
    call("old_port", {"command": "ping"})
    yield from _settled(replies, "old_port")
    board.check(not replies["old_port"].get("ok"),
                f"the old port is still answering: {replies['old_port']}")
    port = moved
    call("new_port", {"command": "ping"})
    yield from _settled(replies, "new_port")
    board.check(replies["new_port"].get("ok"),
                f"the channel did not move to {moved}: {replies['new_port']}")
    yield

    # And the switch in Settings is what stops it listening, which is
    # the only way anyone will ever turn this off.
    app.remote_checkbox.deselect()
    app._on_toggle_remote()
    yield
    board.check(app.remote_server is None and not app.config["remote_control"],
                "switching the remote control off left it running")
    call("after_stop", {"command": "ping"})
    yield from _settled(replies, "after_stop")
    board.check(not replies["after_stop"].get("ok"),
                f"the channel answered after it was stopped: {replies['after_stop']}")
    board.check(all(threads),
                f"{threads.count(False)} of {len(threads)} commands ran off "
                f"the Tk thread")
    yield


remote_control.settings = {"remote_control": True, "remote_port": _free_port()}


# Wide enough that both control rows fit on one line, which is the
# layout that a row failing to place its children hides in: when the row
# wraps, a second <Configure> arrives and lays out whatever was missing.
nothing_is_clipped.geometry = "1000x760+40+40"

def _menu_entry(board, open_menu, label):
    """Open a sound's menu the way a right-click does and invoke one of
    its entries. tk_popup is swapped for a recorder, so no menu is ever
    posted for the harness to have to dismiss."""
    import tkinter as tk
    posted = []
    real = tk.Menu.tk_popup
    tk.Menu.tk_popup = lambda menu, *a, **k: posted.append(menu)
    try:
        open_menu()
    finally:
        tk.Menu.tk_popup = real
    if not posted:
        board.fail("the sound menu did not open")
        return False
    menu = posted[-1]
    entries = {menu.entrycget(i, "label"): i for i in range(menu.index("end") + 1)
               if menu.type(i) not in ("separator", "tearoff")}
    if label not in entries:
        board.fail(f"the sound menu has no {label!r}: {list(entries)}")
        return False
    menu.invoke(entries[label])
    return True


def preview_sound(board):
    """Preview, from a row and from a tile, plays to the monitor alone.

    The runner has no sound card, so both streams are stand-ins: all the
    engine checks when it queues a clip is whether each one exists.
    Nothing mixes, so a clip stays queued until it is stopped.
    """
    import time
    import types
    from soundboard.sound_list import PREVIEW_KEY
    app = board.app
    anchor = types.SimpleNamespace(x_root=0, y_root=0)  # where a right-click landed
    engine = app.audio_engine
    # The device watchdog reads .active once a second.
    engine.output_stream = types.SimpleNamespace(active=True)
    engine.monitor_stream = types.SimpleNamespace(active=True)
    try:
        for view in ("List", "Grid"):
            app._on_view_change(view)
            yield
            target = app.sounds[1]
            plays = int(target.get("plays") or 0)
            opened = _menu_entry(
                board, lambda: app._show_sound_menu(anchor, 1, from_tile=view == "Grid"),
                "Preview")
            yield
            if not opened:
                return
            deadline = time.monotonic() + 5
            while PREVIEW_KEY not in engine.active_keys() and time.monotonic() < deadline:
                yield
            board.check(PREVIEW_KEY in engine.active_keys(),
                        f"{view}: Preview did not reach the monitor")
            board.check(not any(s.key == PREVIEW_KEY for s in engine._active_sounds),
                        f"{view}: Preview went down the cable")
            board.check(app._sound_key(target) not in engine.active_keys(),
                        f"{view}: a preview showed the row as playing")
            board.check(int(target.get("plays") or 0) == plays,
                        f"{view}: a preview counted as a play")
            _menu_entry(board, lambda: app._show_sound_menu(anchor, 1), "Stop preview")
            yield
            board.check(PREVIEW_KEY not in engine.active_keys(),
                        f"{view}: Stop preview left it playing")
    finally:
        engine.stop_all()
        engine.output_stream = engine.monitor_stream = None


SCENARIOS = {
    "board_opens": board_opens,
    "nothing_is_clipped": nothing_is_clipped,
    "settings_button": settings_button,
    "settings_tab_round_trip": settings_tab_round_trip,
    "search_and_clear": search_and_clear,
    "organising_the_board": organising_the_board,
    "colours_and_defaults": colours_and_defaults,
    "remote_control": remote_control,
    "preview_sound": preview_sound,
}
