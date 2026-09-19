"""A scrolling area that only builds the rows you can see.

CTkScrollableFrame lays out every child it holds, and Tk's cost for that
grows far faster than the number of children: a board of 50 sounds took
three minutes to open, and 200 never finished at all. Suppressing the
frame's scrollregion binding, suppressing the canvas's own resize
binding, and building while the container was unmapped all measured the
same, so the cost is the layout itself and the only cure is to lay out
fewer widgets.

So the rows here are canvas window items rather than packed or placed
children. Both of those feed the geometry manager, which is what grows;
moving an item with coords() does not. The widget count stays at about
what fits on screen whatever the board holds, and the scrollbar still
spans the whole list because the scroll region is set from the item
count rather than from the items that happen to exist.

The owner supplies the widgets. This class only decides which slice of
the list should be on screen and where each slot belongs, and calls back
when that changes.
"""

import tkinter as tk

import customtkinter as ctk

from .mac_scroll import bind_canvas
from .theme import COLOR_BG, register_tk

OVERSCAN = 2  # rows kept past each edge, so a scroll doesn't show a gap


class VirtualList(ctk.CTkFrame):
    """Scrolls `count` items while only `on_window` many widgets exist.

    `on_window(first, needed)` is called whenever the slice on screen
    changes. It must make sure `needed` slots exist and bind them to the
    items starting at `first`, then hand each one to place_slot().
    """

    def __init__(self, master, on_window, on_resize=None, **kwargs):
        super().__init__(master, **kwargs)
        self._on_window = on_window
        self._on_resize = on_resize
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, takefocus=0)
        self._scrollbar = ctk.CTkScrollbar(self, command=self._canvas.yview)
        # Every way of scrolling ends up here - the scrollbar, the
        # wheel, the trackpad, a programmatic yview - so this is the
        # one place the visible slice has to be rechecked.
        self._canvas.configure(yscrollcommand=self._scrolled)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        # A raw canvas cannot hold a (light, dark) pair the way a CTk
        # widget can, so the theme has to repaint it on a mode change.
        register_tk(self._canvas, bg=COLOR_BG)
        # The trackpad needs the same help a CTkScrollableFrame gets
        # (see mac_scroll).
        bind_canvas(self._canvas)
        # The wheel goes through bind_all, filtered to events that
        # happened inside this list - which is what CustomTkinter does
        # for its own scrollable frame, and for the same two reasons. A
        # binding on the canvas alone fires only for events delivered to
        # the canvas: on X11 and macOS that means the gaps between rows,
        # since a row is a widget of its own and Tk does not walk up the
        # widget tree; on Windows it means never, because <MouseWheel>
        # there goes to the widget with keyboard focus and this canvas
        # takes none.
        #
        # Bound once and left, rather than on the way in and out:
        # unbind_all drops every binding for that sequence, CustomTkinter
        # included, so leaving this list would stop the editor's own
        # scrolling frame from answering the wheel.
        # Through the raw canvas: CustomTkinter refuses bind_all on its
        # own widgets, and the binding lands on Tk's "all" tag either
        # way. mac_scroll takes the same route for the trackpad.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind_all(sequence, self._wheel, add="+")
        self._count = 0
        self._columns = 1
        self._pitch = (1, 1)
        self._window = None
        self._syncing = False
        self._canvas.bind("<Configure>", self._resized)

    def _scrolled(self, first, last):
        self._scrollbar.set(first, last)
        self.sync()

    def _inside(self, widget):
        """Whether the event belongs to this list. bind_all is the whole
        application, so a wheel over another scrolling area must not
        move this one as well."""
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _wheel(self, event):
        if not self._canvas.winfo_exists() or not self._inside(event.widget):
            return
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            # Windows reports the wheel in multiples of 120, one notch
            # each; macOS reports small counts already.
            notches = event.delta / 120 if abs(event.delta) >= 120 else event.delta
            delta = -1 if notches > 0 else 1
            delta *= max(1, int(abs(notches)))
        self._canvas.yview_scroll(int(delta), "units")

    def _resized(self, _event):
        """A width change can alter the shape of the content itself - the
        grid fits a different number of columns - so the owner is asked
        to restate it before the slice is worked out."""
        if self._on_resize is not None:
            self._on_resize()
        self.sync()

    @property
    def canvas(self):
        """Parent for the slot widgets, and what mac_scroll drives."""
        return self._canvas

    def viewport_width(self):
        return self._canvas.winfo_width()

    def set_content(self, count, columns, pitch_x, pitch_y):
        """How many items there are and how big a cell is. Re-laying out
        is deferred to sync() so a caller can set all of this at once."""
        self._count = max(0, count)
        self._columns = max(1, columns)
        self._pitch = (max(1, pitch_x), max(1, pitch_y))
        self._window = None  # the slice is meaningless under a new shape

    def rows_needed(self):
        """How many rows of cells the whole list occupies."""
        return (self._count + self._columns - 1) // self._columns

    def sync(self, force=False):
        """Work out which slice belongs on screen and ask for it.

        Reentrancy matters: place_slot() and the scroll region both
        resize things Tk answers with another <Configure>, and this is
        bound to that event.
        """
        if self._syncing:
            return
        if force:
            self._window = None  # the slots need rebinding even if the slice is the same
        self._syncing = True
        try:
            pitch_y = self._pitch[1]
            height = self.rows_needed() * pitch_y
            region = (0, 0, max(1, self._canvas.winfo_width()), max(1, height))
            if self._canvas.cget("scrollregion") != " ".join(str(v) for v in region):
                self._canvas.configure(scrollregion=region)
            if not self._count:
                if self._window != (0, 0):
                    self._window = (0, 0)
                    self._on_window(0, 0)
                return
            viewport = self._canvas.winfo_height() or 1
            rows_on_screen = viewport // pitch_y + 1 + OVERSCAN
            needed = min(rows_on_screen * self._columns, self._count)
            top_row = max(0, int(self._canvas.canvasy(0)) // pitch_y)
            # Never scroll the window past the end of the list, or the
            # last screenful would come up short.
            max_first = max(0, self._count - needed)
            first = min(top_row * self._columns, max_first)
            if self._window != (first, needed):
                self._window = (first, needed)
                self._on_window(first, needed)
        finally:
            self._syncing = False

    def place_slot(self, widget, item, index, pad_x=0, pad_y=0):
        """Put a slot's widget where item `index` belongs. `item` is the
        canvas item id from attach(), kept by the caller."""
        pitch_x, pitch_y = self._pitch
        column, row = index % self._columns, index // self._columns
        self._canvas.coords(item, column * pitch_x + pad_x, row * pitch_y + pad_y)
        self._canvas.itemconfigure(
            item, state="normal", width=max(1, pitch_x - 2 * pad_x))

    def attach(self, widget):
        """Add a slot's widget to the canvas and return its item id."""
        return self._canvas.create_window(0, 0, window=widget, anchor="nw",
                                          state="hidden")

    def hide(self, item):
        self._canvas.itemconfigure(item, state="hidden")

    def scroll_to_top(self):
        self._canvas.yview_moveto(0)
