"""What the harness actually checks.

Each scenario is a generator taking a Board. It yields between steps, and
every yield gives Tk a tick to lay out whatever the last step changed.
Add one by writing the function and listing it in SCENARIOS.
"""

import customtkinter as ctk


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


# Wide enough that both control rows fit on one line, which is the
# layout that a row failing to place its children hides in: when the row
# wraps, a second <Configure> arrives and lays out whatever was missing.
nothing_is_clipped.geometry = "1000x760+40+40"

SCENARIOS = {
    "board_opens": board_opens,
    "nothing_is_clipped": nothing_is_clipped,
    "settings_button": settings_button,
    "settings_tab_round_trip": settings_tab_round_trip,
    "search_and_clear": search_and_clear,
    "organising_the_board": organising_the_board,
    "colours_and_defaults": colours_and_defaults,
}
