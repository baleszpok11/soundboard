"""A row of controls that wraps onto another line when it runs out of width.

pack() cannot wrap. It hands each slave the width it asks for, in order,
and whatever is left over goes to the last ones - so a full row does not
overflow visibly, it quietly destroys its own end. The board's bottom
row wants 919px; the app's default window gives it 856 and clips "Report
a bug" to "ort a", and at the 640px minimum from main.py "Stop all" is
cut to 84px while "Set stop hotkey" and "Report a bug" collapse to a
single pixel each. A control nobody can see is worse than a wrapped one,
and Stop all is the button people reach for in a hurry.

So the children here are placed rather than packed, across as many lines
as the width needs, and the frame asks for the height those lines came
to - which is what makes the packer above leave room for the second one.

Items keep the left/right split they had under pack, but only while the
row fits on one line, which is where that distinction reads as intended.
Once it wraps, everything is laid out in order from the left: a button
pinned to the right of a line it shares with nothing else just looks
lost.
"""

import customtkinter as ctk

GAP = 8       # between two controls on the same line
LINE_GAP = 6  # between one line and the next


class FlowRow(ctk.CTkFrame):
    """Add controls with add(); they are laid out on <Configure>."""

    def __init__(self, master, gap=GAP, line_gap=LINE_GAP, **kwargs):
        super().__init__(master, **kwargs)
        self._items = []  # (widget, side, gap before it on its line)
        self._gap = gap
        self._line_gap = line_gap
        self._width = 0
        self._pending = None
        # The height is ours to report, so Tk must not take it from the
        # children instead.
        self.pack_propagate(False)
        self.bind("<Configure>", self._resized)

    def add(self, widget, side="left", gap=None):
        """`gap` overrides the space before this control, for the one
        case where a checkbox wants more air than a button does."""
        self._items.append((widget, side,
                            self._gap if gap is None else gap))
        # A child added after a reflow would otherwise never be laid out
        # at all. _resized only reflows when the width changes, and the
        # width does not change just because the row gained a child - so
        # whatever was added after the first <Configure> stays unplaced,
        # invisible, and not even mapped.
        #
        # That is not a corner case: building a CTk widget can pump the
        # event loop - a CTkOptionMenu builds a dropdown of its own - so
        # the first <Configure> can arrive between two add() calls and
        # take the row's width with one child in it.
        self._reflow_soon()
        return widget

    def _reflow_soon(self):
        """Once per idle pass, however many children are added."""
        if self._pending is None:
            self._pending = self.after_idle(self._reflow_now)

    def _reflow_now(self):
        self._pending = None
        if self.winfo_exists():
            self.reflow()

    def _resized(self, event):
        # Only a width change can alter the layout, and reflowing sets a
        # height, which comes back here as another <Configure>.
        if event.width == self._width:
            return
        self._width = event.width
        self.reflow()

    def _line_height(self):
        """Only ever used as spacing: a CTk widget carries its own height
        and raises if place() is handed one."""
        return max((widget.winfo_reqheight() for widget, _, _ in self._items),
                   default=1)

    def _needed(self):
        """What one line would have to be for everything to fit on it."""
        total = 0
        for index, (widget, _, gap) in enumerate(self._items):
            total += widget.winfo_reqwidth() + (gap if index else 0)
        return total

    def reflow(self):
        if not self._items:
            return
        width = self.winfo_width()
        if width <= 1:
            return  # not laid out yet; the <Configure> that gives it a
            # width will bring us straight back
        height = self._line_height()
        if self._needed() <= width:
            self._one_line(width, height)
        else:
            height = self._wrapped(width, height)
        if self.winfo_reqheight() != height:
            self.configure(height=height)

    def _one_line(self, width, height):
        """The layout the row had under pack, while it still fits."""
        x = 0
        for index, (widget, side, gap) in enumerate(self._items):
            if side == "right":
                continue
            x += gap if index else 0
            widget.place(x=x, y=0)
            x += widget.winfo_reqwidth()
        right = width
        for widget, side, gap in reversed(self._items):
            if side != "right":
                continue
            right -= widget.winfo_reqwidth()
            widget.place(x=right, y=0)
            right -= gap

    def _wrapped(self, width, height):
        """Everything from the left, onto as many lines as it takes."""
        x, y = 0, 0
        for widget, _, gap in self._items:
            asked = widget.winfo_reqwidth()
            step = asked + (gap if x else 0)
            if x and x + step > width:
                x, y = 0, y + height + self._line_gap
                step = asked
            widget.place(x=x + (step - asked), y=y)
            x += step
        return y + height
