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
    board.expect_rows_full_width()
    board.check(
        len(board.rows_on_screen) > 0,
        "the scroller built no rows at all for a board that has sounds")
    yield


def nothing_is_clipped(board):
    """Every control on the board tab got at least the width it asked
    for. A clipped button still works; it just cannot be read."""
    board.shot("clipping")
    for widget in _descendants(board.board_tab):
        if isinstance(widget, (ctk.CTkButton, ctk.CTkLabel)):
            board.expect_fits(widget, _labelled(widget, type(widget).__name__))
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


SCENARIOS = {
    "board_opens": board_opens,
    "nothing_is_clipped": nothing_is_clipped,
    "settings_button": settings_button,
    "settings_tab_round_trip": settings_tab_round_trip,
    "search_and_clear": search_and_clear,
}
