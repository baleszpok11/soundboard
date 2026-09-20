"""The board itself: the sound list/grid, its toolbar and the buttons
below it, plus adding, removing and playing sounds."""

import filecmp
import os
import random
import shutil
import sys
import textwrap
import threading
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
import soundfile as sf

from .audio_engine import RECORD_MAX_S, REPLAY_S, SAMPLE_RATE, Fades
from .config import (
    NEW_SOUND_VOLUME,
    SOUNDS_DIR,
    unique_profile_name,
    ensure_sounds_dir,
    resolve_sound_path,
    same_file,
    set_sound_loudness,
    sound_fades,
    sound_loudness,
    sound_paths,
    sanitize_filename,
    save_config,
    unique_path,
)
from .dialogs import (
    FadeDialog,
    TextDialog,
    VolumeDialog,
    ask_yes_no,
    ask_yes_no_cancel,
    error,
    info,
    warn,
)
from .dsp import match_gain, measure_file
from .flow_row import FlowRow
from .virtual_list import VirtualList
from .theme import (
    CARD_BORDER,
    COLOR_BG,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_ERROR_HOVER,
    COLOR_ERROR_TEXT,
    COLOR_ON_ACCENT,
    COLOR_ON_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    GAP,
    RADIUS_CONTROL,
    VOLUME_MAX,
    font,
    resolve,
)

PLAYING_POLL_MS = 100  # how often the board re-reads what the engine is playing
PAUSE_TEXT, RESUME_TEXT = "Pause", "Resume"
ROW_PAD_X, ROW_PAD_Y = 2, 3  # gap around a list row, was its pack padding
TILE_PAD = 4  # gap around a grid tile, was its grid padding
RECORD_POLL_MS = 200  # how often the Record button's elapsed time is redrawn
_length_cache = {}  # (path, mtime, size) -> "m:ss", so a redraw doesn't re-read every file
AUDIO_EXTENSIONS = (".wav", ".flac", ".ogg", ".mp3")
IMPORT_REPORT_NAMES = 10  # names listed before the rest are counted


def _clip_length(path):
    """A clip's length as m:ss, or None when the file is missing or not
    audio we can read. soundfile reads the header only, and the answer is
    cached: the list is rebuilt on every search keystroke."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    if key not in _length_cache:
        try:
            with sf.SoundFile(path) as clip:
                seconds = len(clip) / clip.samplerate
        except Exception:
            _length_cache[key] = None
        else:
            _length_cache[key] = f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    return _length_cache[key]


def _name_list(names):
    shown = "\n".join(names[:IMPORT_REPORT_NAMES])
    rest = len(names) - IMPORT_REPORT_NAMES
    return f"{shown}\nand {rest} more" if rest > 0 else shown


class SoundListMixin:
    """The Soundboard tab's sound list and sound management. Expects
    `config`, `audio_engine` and `root` from Soundboard."""

    # -- profiles -------------------------------------------------------

    def _build_profile_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        bar.pack(fill="x", padx=4, pady=(6, 0))
        ctk.CTkLabel(bar, text="Profile:", text_color=COLOR_TEXT).pack(side="left", padx=(4, 6))
        self.profile_var = tk.StringVar(value=self.config["active_profile"])
        self.profile_menu = ctk.CTkOptionMenu(
            bar, variable=self.profile_var, values=self._profile_names(),
            command=self._on_profile_change,
            fg_color=COLOR_ROW, text_color=COLOR_TEXT,
        )
        self.profile_menu.pack(side="left")
        manage = ctk.CTkButton(
            bar, text="Manage", width=80,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        manage.configure(command=lambda b=manage: self._show_profile_menu(b))
        manage.pack(side="left", padx=(8, 0))

    def _profile_names(self):
        return [p["name"] for p in self.config["profiles"]]

    def _refresh_profile_menu(self):
        self.profile_menu.configure(values=self._profile_names())
        self.profile_var.set(self.config["active_profile"])

    def _switch_profile(self, name):
        """Move the board to another profile: stop what the old one was
        playing, then rebuild the list, hotkeys and cache around the new
        sounds."""
        # A clip from the old board would otherwise keep playing with
        # nothing on screen to stop it, since its row is gone.
        self.audio_engine.stop_all()
        self.config["active_profile"] = name
        save_config(self.config)
        self.search_entry.delete(0, "end")
        self._search_text = ""
        self._refresh_profile_menu()
        self._refresh_sound_list()
        self._apply_hotkeys()
        if self.config.get("match_levels"):
            self.measure_board()
        self.audio_engine.preload(
            resolve_sound_path(path) for s in self.sounds if s.get("enabled", True)
            for path in sound_paths(s)
        )

    def _on_profile_change(self, name):
        if name != self.config["active_profile"]:
            self._switch_profile(name)

    def _show_profile_menu(self, anchor):
        menu = tk.Menu(
            self.root, tearoff=0, bg=resolve(COLOR_SURFACE), fg=resolve(COLOR_TEXT),
            activebackground=resolve(COLOR_ORANGE), activeforeground=resolve(COLOR_ON_ACCENT),
            borderwidth=0, activeborderwidth=0,
        )
        menu.add_command(label="New profile...", command=self.new_profile)
        menu.add_command(label="Rename...", command=self.rename_profile)
        menu.add_command(label="Duplicate", command=self.duplicate_profile)
        menu.add_separator()
        menu.add_command(
            label="Delete", command=self.delete_profile,
            state="normal" if len(self.config["profiles"]) > 1 else "disabled",
        )
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height())
        finally:
            menu.grab_release()

    def _ask_profile_name(self, title, current=""):
        name = TextDialog(self.root, title, "Profile name:", current).get()
        if not name or not name.strip():
            return None
        return unique_profile_name(name, set(self._profile_names()) - {current})

    def new_profile(self):
        name = self._ask_profile_name("New profile")
        if name is None:
            return
        self.config["profiles"].append({"name": name, "sounds": []})
        self._switch_profile(name)

    def rename_profile(self):
        current = self.config["active_profile"]
        name = self._ask_profile_name("Rename profile", current)
        if name is None or name == current:
            return
        self.profile["name"] = name
        self.config["active_profile"] = name
        save_config(self.config)
        self._refresh_profile_menu()

    def duplicate_profile(self):
        name = self._ask_profile_name("Duplicate profile", self.config["active_profile"])
        if name is None:
            return
        # Copy each entry, or the two profiles would share sound dicts and
        # renaming one would rename the other.
        copied = [dict(sound) for sound in self.sounds]
        self.config["profiles"].append({"name": name, "sounds": copied})
        self._switch_profile(name)

    def delete_profile(self):
        if len(self.config["profiles"]) <= 1:
            return  # the board always has one profile
        profile = self.profile
        if not ask_yes_no(self.root,
            "Delete profile",
            f"Delete the profile '{profile['name']}' and its {len(profile['sounds'])} sound(s) "
            "from the board?\n\nThe sound files themselves are not deleted.",
        ):
            return
        self.config["profiles"].remove(profile)
        self._switch_profile(self._profile_names()[0])

    # -- sound list -----------------------------------------------------

    def _build_sound_list(self, parent):
        self._build_profile_bar(parent)
        toolbar = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        toolbar.pack(fill="x", padx=4, pady=(6, 0))
        # No textvariable: CTkEntry hides its placeholder when one is set.
        self.search_entry = ctk.CTkEntry(
            toolbar, placeholder_text="Search sounds...",
            fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_BORDER,
        )
        self.search_entry.pack(side="left", fill="x", expand=True)
        self._search_text = ""
        self.search_entry.bind("<KeyRelease>", self._on_search)
        self.view_switch = ctk.CTkSegmentedButton(
            toolbar, values=["List", "Grid"], command=self._on_view_change, selected_hover_color=COLOR_ORANGE_HOVER, unselected_hover_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
        )
        self.view_switch.set("Grid" if self.config["sound_view"] == "grid" else "List")
        self.view_switch.pack(side="left", padx=(8, 0))

        # No label bar: it was a full-width strip of chrome restating the
        # name of the tab it sits in. The rows read as cards against the
        # tab's own background instead.
        # Only the rows that fit on screen are ever built; see
        # virtual_list.py for why a scrollable frame cannot be used here.
        self.list_frame = VirtualList(
            parent, self._show_sound_window, on_resize=self._on_list_resize,
            fg_color=COLOR_BG)
        self.list_frame.pack(fill="both", expand=True, padx=2, pady=(0, GAP))
        self._grid_columns = 0
        self.empty_label = ctk.CTkLabel(self.list_frame, text="", text_color=COLOR_TEXT_DIM)
        self._row_pool = []
        self._tile_pool = []
        self._visible = []  # what the search leaves, shared with the window callback
        self._row_pitch = 0
        self._tile_pitch = 0
        self._drag_from = None
        self._playing_widgets = {}
        self._last_clip = {}  # group key -> the member that played last
        self._refresh_sound_list()
        self._poll_playing()

    @staticmethod
    def _sound_key(sound):
        """How the engine identifies what this entry plays. A group is
        keyed by its first clip, so the key survives its members
        changing under it."""
        return os.path.abspath(resolve_sound_path(sound["path"]))

    def stop_sound(self, sound):
        self.audio_engine.stop_key(self._sound_key(sound))

    def _update_playing(self):
        """Mirror what the engine is playing onto the board."""
        playing = self.audio_engine.active_keys()
        paused = self.audio_engine.paused_keys()
        for entry in self._playing_widgets.values():
            self._show_playing(entry, playing.get(entry["key"]),
                               entry["key"] in paused)
        if hasattr(self, "pause_button"):
            self.pause_button.configure(text=RESUME_TEXT if paused else PAUSE_TEXT)

    def _poll_playing(self):
        self._update_playing()
        self._playing_poll = self.root.after(PLAYING_POLL_MS, self._poll_playing)

    @staticmethod
    def _show_playing(entry, fraction, paused=False):
        progress, stop = entry["progress"], entry["stop"]
        idle = progress.cget("fg_color")
        if fraction is None:
            progress.configure(progress_color=idle)
            progress.set(0)
            if stop is not None and stop.winfo_manager():
                stop.pack_forget()
            return
        # A held clip is still playing as far as the row is concerned -
        # its Stop button stays, and the bar keeps its place - but the
        # colour says it is not moving.
        progress.configure(progress_color=COLOR_TEXT_DIM if paused else COLOR_ORANGE)
        progress.set(fraction)
        if stop is not None and not stop.winfo_manager():
            stop.pack(side="left", padx=3, before=entry["before"])

    def _bind_seek(self, progress, slot):
        """Dragging a row's own progress bar moves the clip playing under
        it. The bar is already there and already says where the clip is,
        so it is the control rather than a second widget beside it."""
        progress.configure(cursor="hand2")
        progress.bind("<Button-1>", lambda e: self._seek_from_bar(slot, progress, e))
        progress.bind("<B1-Motion>", lambda e: self._seek_from_bar(slot, progress, e))

    def _seek_from_bar(self, slot, progress, event):
        sound = slot["sound"]
        if sound is None:
            return
        key = self._sound_key(sound)
        if self.audio_engine.playback_progress(key) is None:
            return  # nothing playing here: the bar is not a play button
        width = max(1, progress.winfo_width())
        fraction = min(1.0, max(0.0, event.x / width))
        self.audio_engine.seek_key(key, fraction)
        # Straight away rather than at the next poll, so the bar follows
        # the pointer while it is being dragged.
        progress.set(fraction)

    def _on_search(self, _event=None):
        text = self.search_entry.get().strip().lower()
        if text != self._search_text:
            self._search_text = text
            self._refresh_sound_list()

    def _on_view_change(self, value):
        self.config["sound_view"] = value.lower()
        save_config(self.config)
        self._refresh_sound_list()

    def _grid_column_count(self):
        width = self.list_frame.viewport_width()
        return max(1, width // 190) if width > 1 else 4

    def _on_list_resize(self):
        """The canvas changed width: the cells have to be re-measured
        before the scroller works out what is on screen. Called from the
        scroller's own <Configure>, which then syncs."""
        self._set_list_shape()

    def _visible_sounds(self):
        """(index, sound) pairs matching the search box."""
        query = self._search_text
        return [
            (i, s) for i, s in enumerate(self.sounds)
            if not query or query in s["name"].lower()
        ]

    def _refresh_sound_list(self):
        """Take what the search leaves and hand it to the scroller. The
        widgets are not built here: the scroller calls back with the
        slice that is actually on screen, which is all that gets built."""
        self._visible = self._visible_sounds()
        if self._visible:
            if self.empty_label.winfo_manager():
                self.empty_label.place_forget()
        else:
            self.empty_label.configure(text=(
                "No sounds match your search." if self.sounds
                else "No sounds yet. Click Add sound or use the Download tab."))
            if not self.empty_label.winfo_manager():
                self.empty_label.place(relx=0.5, y=20, anchor="n")
        # A shorter list can leave the view scrolled past its own end,
        # which would come back as an empty board.
        self.list_frame.scroll_to_top()
        self._set_list_shape()
        self.list_frame.sync(force=True)
        if hasattr(self, "editor_sound_menu"):
            self._refresh_editor_sound_list()
        if hasattr(self, "export_summary"):
            self._update_export_summary()

    def _set_list_shape(self):
        """Tell the scroller how many cells there are and how big one is.
        A cell is measured from a real slot rather than assumed, so a
        change of font or theme metric moves the rows with it."""
        grid = self.config["sound_view"] == "grid"
        # The width is read after the measuring pass, not before it.
        # _pitch_of() calls update_idletasks(), which is where Tk
        # delivers the canvas's first <Configure> - and that re-enters
        # this method through _on_list_resize with the real width. A
        # width read before the measurement is stale by the time it is
        # used, and overwrites the good one with the 1 an unmapped
        # canvas reports, leaving every row a pixel wide.
        if grid:
            pitch_y = self._tile_pitch or self._measure_tile()
            self._grid_columns = self._grid_column_count()
            width = max(1, self.list_frame.viewport_width())
            self.list_frame.set_content(
                len(self._visible), self._grid_columns, width // self._grid_columns, pitch_y)
        else:
            pitch_y = self._row_pitch or self._measure_row()
            width = max(1, self.list_frame.viewport_width())
            self.list_frame.set_content(len(self._visible), 1, width, pitch_y)

    @staticmethod
    def _pitch_of(widget, pad):
        """A cell's height, measured from a real one.

        The idle pass matters: a CTkFrame starts out asking for its
        default 200px and only shrinks to what its children need once Tk
        has run the geometry propagation, so measuring before that spaces
        the whole list six rows apart.
        """
        widget.update_idletasks()
        return widget.winfo_reqheight() + 2 * pad

    def _measure_row(self):
        if not self._row_pool:
            self._row_pool.append(self._make_row())
        self._row_pitch = self._pitch_of(self._row_pool[0]["outer"], ROW_PAD_Y)
        return self._row_pitch

    def _measure_tile(self):
        if not self._tile_pool:
            self._tile_pool.append(self._make_tile())
        self._tile_pitch = self._pitch_of(self._tile_pool[0]["cell"], TILE_PAD)
        return self._tile_pitch

    def _show_sound_window(self, first, needed):
        """The scroller's callback: bind `needed` slots to the sounds
        starting at `first` and put them where those items belong. Every
        other slot is hidden rather than destroyed."""
        grid = self.config["sound_view"] == "grid"
        pool = self._tile_pool if grid else self._row_pool
        make = self._make_tile if grid else self._make_row
        bind = self._bind_tile if grid else self._bind_row
        key = "cell" if grid else "outer"
        pad = TILE_PAD if grid else ROW_PAD_X
        pad_y = TILE_PAD if grid else ROW_PAD_Y
        self._playing_widgets = {}  # rebound below; the poll reads it
        while len(pool) < needed:
            pool.append(make())
        window = self._visible[first:first + needed]
        for position, (slot, (idx, sound)) in enumerate(zip(pool, window)):
            bind(slot, idx, sound)
            self.list_frame.place_slot(slot[key], slot["item"], first + position,
                                       pad_x=pad, pad_y=pad_y)
            slot["mapped"] = True
        for slot in pool[len(window):]:
            if slot["mapped"]:
                self.list_frame.hide(slot["item"])
                slot["mapped"] = False
        # The other view's slots stay built but off screen.
        for slot in (self._row_pool if grid else self._tile_pool):
            if slot["mapped"]:
                self.list_frame.hide(slot["item"])
                slot["mapped"] = False
        # Without this a rebound slot would show the previous sound's
        # progress and Stop button until the next poll.
        self._update_playing()

    def _make_tile(self):
        """One reusable tile. The callbacks read the slot's binding when
        they fire rather than capturing an index, so rebinding the slot to
        another sound is enough to retarget them."""
        slot = {"idx": None, "sound": None, "mapped": False}
        # A cell, not a bare button, so the tile can carry a progress
        # bar under it the way list rows do.
        cell = ctk.CTkFrame(self.list_frame.canvas, fg_color="transparent")
        button = ctk.CTkButton(
            cell, text="", height=84,
            command=lambda: self.play_sound(slot["sound"]),
            hover_color=COLOR_ORANGE_HOVER,
        )
        button.pack(fill="x")
        menu = lambda e: self._show_sound_menu(e, slot["idx"], from_tile=True)
        button.bind("<Button-3>", menu)
        button.bind("<Button-2>" if sys.platform == "darwin" else "<Control-Button-1>", menu)
        # Taller than the 3px it used to draw: it is a control now, and
        # three pixels is not something to catch with a pointer.
        progress = ctk.CTkProgressBar(
            cell, height=6, corner_radius=0, fg_color=COLOR_SURFACE, progress_color=COLOR_SURFACE,
        )
        progress.set(0)
        progress.pack(fill="x", pady=(2, 0))
        self._bind_seek(progress, slot)
        slot.update(cell=cell, button=button, progress=progress,
                    item=self.list_frame.attach(cell))
        return slot

    def _bind_tile(self, slot, idx, sound):
        slot["idx"], slot["sound"] = idx, sound
        missing = not os.path.exists(resolve_sound_path(sound["path"]))
        enabled = sound.get("enabled", True)
        lines = textwrap.wrap(sound["name"], 20)[:2] or [""]
        if len(textwrap.wrap(sound["name"], 20)) > 2:
            lines[-1] = lines[-1][:17] + "..."
        if missing:
            lines.append("(file missing)")
        else:
            hotkey = sound.get("hotkey") or ""
            loop = "loop" if sound.get("loop") else ""
            clips = len(sound_paths(sound))
            group = f"{clips} clips" if clips > 1 else ""
            length = _clip_length(resolve_sound_path(sound["path"])) or ""
            lines.append("  ".join(p for p in (hotkey, length, loop, group) if p) or " ")
        slot["button"].configure(
            text="\n".join(lines),
            fg_color=COLOR_ORANGE if enabled and not missing else COLOR_ROW,
            text_color=COLOR_ON_ACCENT if enabled and not missing else (COLOR_ERROR if missing else COLOR_TEXT_DIM),
        )
        # Tiles are a single button, so stopping is done from the menu.
        self._playing_widgets[idx] = {
            "key": self._sound_key(sound), "progress": slot["progress"],
            "stop": None, "before": None,
        }

    def _make_row(self):
        """One reusable list row, built empty; see _make_tile on why the
        callbacks go through the slot."""
        slot = {"idx": None, "sound": None, "mapped": False}
        # The outer frame carries the drag target and the progress bar; the
        # inner one keeps the controls on a single line.
        outer = ctk.CTkFrame(
            self.list_frame.canvas, fg_color=COLOR_SURFACE, corner_radius=RADIUS_CONTROL,
            border_width=CARD_BORDER, border_color=COLOR_BORDER,
        )
        outer.sound_index = None
        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x")

        handle = ctk.CTkLabel(row, text="::", width=16, text_color=COLOR_TEXT_DIM, cursor="fleur")
        handle.pack(side="left", padx=(8, 0))
        handle.bind("<ButtonPress-1>", lambda e: self._start_drag(slot["idx"]))
        handle.bind("<B1-Motion>", self._on_drag)
        handle.bind("<ButtonRelease-1>", self._end_drag)

        checkbox = ctk.CTkCheckBox(
            row,
            text="",
            width=24,
            fg_color=COLOR_ORANGE,
            hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT,
        )
        checkbox.configure(command=lambda: self._on_toggle_sound(slot["idx"], checkbox))
        checkbox.pack(side="left", padx=(6, 4))

        # Name and status are two labels, not one string: the name is what
        # the eye looks for, and running them together at one weight made
        # the hotkey and the warnings compete with it.
        names = ctk.CTkFrame(row, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True, padx=GAP)
        label = ctk.CTkLabel(names, text="", anchor="w", font=font("body_bold"))
        label.pack(fill="x")
        meta_label = ctk.CTkLabel(names, text="", anchor="w", font=font("small"))
        meta_label.pack(fill="x")
        for widget in (label, meta_label):
            widget.bind("<Double-Button-1>", lambda e: self.rename_sound(slot["idx"]))

        volume_label = ctk.CTkLabel(
            row, text="", width=42,
            font=font("small"), text_color=COLOR_TEXT_DIM,
        )
        volume_slider = ctk.CTkSlider(
            row, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, width=100,
            command=lambda value: self._on_sound_volume(slot["sound"], volume_label, value),
        )
        volume_slider.pack(side="left", padx=(3, 0))
        volume_label.pack(side="left", padx=(0, 3))

        ctk.CTkButton(
            row, text="Play", width=64, font=font("body_bold"),
            command=lambda: self.play_sound(slot["sound"]),
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        ).pack(side="left", padx=3)
        # Built now, shown only while this sound is playing, so an idle
        # board isn't a wall of dead buttons.
        stop = ctk.CTkButton(
            row, text="Stop", width=60,
            command=lambda: self.stop_sound(slot["sound"]),
            fg_color=COLOR_ERROR, hover_color=COLOR_ERROR_HOVER, text_color=COLOR_ON_ERROR,
        )
        hotkey_button = ctk.CTkButton(
            row, text="Hotkey", width=70,
            command=lambda: self.set_hotkey(slot["idx"]),
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        hotkey_button.pack(side="left", padx=3)
        more = ctk.CTkButton(
            row, text="More", width=60,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        more.configure(command=lambda: self._show_sound_menu(None, slot["idx"], anchor=more))
        more.pack(side="left", padx=(3, 8))

        progress = ctk.CTkProgressBar(
            outer, height=6, corner_radius=0,
            fg_color=COLOR_SURFACE, progress_color=COLOR_SURFACE,
        )
        progress.set(0)
        progress.pack(fill="x", padx=8, pady=(0, 4))
        self._bind_seek(progress, slot)
        slot.update(
            outer=outer, checkbox=checkbox, label=label, meta=meta_label,
            volume=volume_slider, volume_label=volume_label, stop=stop,
            hotkey=hotkey_button, progress=progress,
            item=self.list_frame.attach(outer),
        )
        return slot

    def _bind_row(self, slot, idx, sound):
        slot["idx"], slot["sound"] = idx, sound
        slot["outer"].sound_index = idx
        resolved_path = resolve_sound_path(sound["path"])
        missing = not os.path.exists(resolved_path)
        if sound.get("enabled", True):
            slot["checkbox"].select()
        else:
            slot["checkbox"].deselect()
        name = sound["name"]
        if len(name) > 40:  # long titles would push the buttons out of the row
            name = name[:37] + "..."
        slot["label"].configure(
            text=name, text_color=COLOR_ERROR_TEXT if missing else COLOR_TEXT)
        meta = [sound.get("hotkey") or "no hotkey"]
        length = _clip_length(resolved_path)
        if length is not None:
            meta.append(length)
        clips = len(sound_paths(sound))
        if clips > 1:
            meta.append(f"{clips} clips, random")
        if sound.get("loop"):
            meta.append("loop")
        if missing:
            meta.append("file missing")
        slot["meta"].configure(
            text="  -  ".join(meta),
            text_color=COLOR_ERROR_TEXT if missing else COLOR_TEXT_DIM)
        slot["volume"].set(sound["volume"])
        slot["volume_label"].configure(text=f"{sound['volume']}%")
        self._playing_widgets[idx] = {
            "key": self._sound_key(sound), "progress": slot["progress"],
            "stop": slot["stop"], "before": slot["hotkey"],
        }

    def _show_sound_menu(self, event, index, anchor=None, from_tile=False):
        menu = tk.Menu(
            self.root, tearoff=0, bg=resolve(COLOR_SURFACE), fg=resolve(COLOR_TEXT),
            activebackground=resolve(COLOR_ORANGE), activeforeground=resolve(COLOR_ON_ACCENT),
            borderwidth=0, activeborderwidth=0,
        )
        count = len(self.sounds)
        sound = self.sounds[index]
        # A tile is one button: the controls a list row shows inline have
        # nowhere to live but this menu.
        if from_tile:
            menu.add_command(label="Set hotkey...", command=lambda: self.set_hotkey(index))
            menu.add_command(label="Volume...", command=lambda: self.set_volume(index))
        menu.add_command(
            label="Stop", command=lambda: self.stop_sound(sound),
            state="normal" if self._sound_key(sound) in self.audio_engine.active_keys() else "disabled",
        )
        # Held on self so the variable outlives the menu that reads it.
        self._loop_var = tk.BooleanVar(value=sound.get("loop", False))
        menu.add_checkbutton(
            label="Loop", variable=self._loop_var,
            command=lambda: self._set_loop(index, self._loop_var.get()),
        )
        menu.add_command(label="Fades...", command=lambda: self.set_fades(index))
        menu.add_command(label="Re-measure loudness",
                         command=lambda: self.remeasure_sound(index))
        menu.add_command(label="Rename...", command=lambda: self.rename_sound(index))
        menu.add_command(label="Add clips...", command=lambda: self.add_clips_to_group(index))
        paths = sound_paths(sound)
        if len(paths) > 1:
            remove = tk.Menu(menu, tearoff=0)
            for path in paths:
                remove.add_command(
                    label=os.path.basename(path),
                    command=lambda p=path: self.remove_clip_from_group(index, p),
                )
            menu.add_cascade(label="Remove one clip", menu=remove)
            # Held on self so the submenu outlives the call that built it.
            self._group_menu = remove
        menu.add_command(label="Move up", command=lambda: self.move_sound(index, index - 1),
                         state="normal" if index > 0 else "disabled")
        menu.add_command(label="Move down", command=lambda: self.move_sound(index, index + 1),
                         state="normal" if index < count - 1 else "disabled")
        menu.add_separator()
        menu.add_command(label="Remove", command=lambda: self.remove_sound(index))
        if anchor is not None:
            x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height()
        else:
            x, y = event.x_root, event.y_root
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_loop(self, index, loop):
        sound = self.sounds[index]
        sound["loop"] = bool(loop)
        save_config(self.config)
        # Reach the running clip too, so switching looping off stops the
        # repeat instead of waiting for someone to hit Stop.
        self.audio_engine.set_loop(self._sound_key(sound), sound["loop"])
        self._refresh_sound_list()

    def rename_sound(self, index):
        sound = self.sounds[index]
        name = TextDialog(self.root, "Rename sound", "Name:", sound["name"]).get()
        if not name or name == sound["name"]:
            return
        sound["name"] = name
        save_config(self.config)
        self._refresh_sound_list()

    def move_sound(self, index, new_index):
        sounds = self.sounds
        if not (0 <= index < len(sounds) and 0 <= new_index < len(sounds)) or index == new_index:
            return
        sounds.insert(new_index, sounds.pop(index))
        save_config(self.config)
        self._refresh_sound_list()

    # Drag the "::" handle onto another row to move a sound there.
    def _start_drag(self, index):
        # Indices only line up with rows when the list isn't filtered.
        self._drag_from = None if self._search_text else index
        self._drag_target = None

    def _row_at(self, x_root, y_root):
        widget = self.root.winfo_containing(x_root, y_root)
        while widget is not None and not hasattr(widget, "sound_index"):
            widget = widget.master
        return widget

    def _on_drag(self, event):
        if self._drag_from is None:
            return
        row = self._row_at(event.x_root, event.y_root)
        if row is self._drag_target:
            return
        if self._drag_target is not None and self._drag_target.winfo_exists():
            self._drag_target.configure(border_color=COLOR_ROW)
        self._drag_target = row
        if row is not None:
            row.configure(border_color=COLOR_BORDER)

    def _end_drag(self, _event):
        target, source = self._drag_target, self._drag_from
        self._drag_from = self._drag_target = None
        if source is not None and target is not None:
            self.move_sound(source, target.sound_index)

    def _on_toggle_sound(self, index, checkbox):
        sound = self.sounds[index]
        sound["enabled"] = bool(checkbox.get())
        if not sound["enabled"]:
            # Unchecking is the obvious "make this stop" gesture, so don't
            # leave a clip that's already playing running to the end.
            self.audio_engine.stop_key(self._sound_key(sound))
        save_config(self.config)
        self._apply_hotkeys()

    def set_volume(self, index):
        """The grid's way into the volume the list rows show on a slider."""
        sound = self.sounds[index]

        def apply(percent):
            sound["volume"] = percent
            self._save_config_soon()

        VolumeDialog(self.root, sound["name"], sound.get("volume", 100), apply)

    def _on_sound_volume(self, sound, label, value):
        sound["volume"] = int(round(value))
        label.configure(text=f"{sound['volume']}%")
        self._save_config_soon()

    def _build_transport(self, parent):
        """Pause, step through the board and play it as a list.

        Its own row above the rest: the controls row was already as wide
        as the window, and these belong together anyway.
        """
        # Wraps for the same reason the controls row does: these seven
        # want 671px and the 640px minimum window leaves 596.
        frame = FlowRow(parent, fg_color=COLOR_BG)
        frame.pack(side="bottom", fill="x", padx=2, pady=(GAP, 0))
        flat = dict(fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
                    border_width=1, border_color=COLOR_BORDER)
        self.pause_button = frame.add(ctk.CTkButton(
            frame, text=PAUSE_TEXT, width=90, command=self.toggle_pause, **flat))
        frame.add(ctk.CTkButton(
            frame, text="Previous", width=80, command=self.play_previous, **flat))
        frame.add(ctk.CTkButton(
            frame, text="Next", width=70, command=self.play_next, **flat))
        self.continuous_checkbox = ctk.CTkCheckBox(
            frame, text="Continuous", command=self._on_toggle_continuous,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            checkmark_color=COLOR_ON_ACCENT, text_color=COLOR_TEXT,
        )
        if self.config.get("continuous_play"):
            self.continuous_checkbox.select()
        frame.add(self.continuous_checkbox, gap=12)

        # The keys sit on the right of the same row, in the order the
        # buttons on the left are in.
        self.prev_hotkey_button = ctk.CTkButton(
            frame, text="", width=110, command=self.set_prev_hotkey, **flat)
        self.next_hotkey_button = ctk.CTkButton(
            frame, text="", width=110, command=self.set_next_hotkey, **flat)
        self.pause_hotkey_button = ctk.CTkButton(
            frame, text="", width=110, command=self.set_pause_hotkey, **flat)
        for button in (self.pause_hotkey_button, self.next_hotkey_button,
                       self.prev_hotkey_button):
            frame.add(button, side="right")
        self._update_transport_hotkey_buttons()

    def _on_toggle_continuous(self):
        self.config["continuous_play"] = bool(self.continuous_checkbox.get())
        save_config(self.config)

    def _update_transport_hotkey_buttons(self):
        for key, button, label in (
            ("pause_hotkey", self.pause_hotkey_button, "Pause"),
            ("next_hotkey", self.next_hotkey_button, "Next"),
            ("prev_hotkey", self.prev_hotkey_button, "Previous"),
        ):
            hotkey = self.config.get(key)
            button.configure(text=f"{label}: {hotkey}" if hotkey else f"{label} key")

    def _set_transport_hotkey(self, key, title, owner):
        hotkey = self._ask_hotkey(title, self.config.get(key), owner=owner)
        if hotkey is None:
            return
        self.config[key] = hotkey or None
        save_config(self.config)
        self._update_transport_hotkey_buttons()
        self._apply_hotkeys()

    def set_pause_hotkey(self):
        self._set_transport_hotkey("pause_hotkey", "Pause hotkey", "pause")

    def set_next_hotkey(self):
        self._set_transport_hotkey("next_hotkey", "Play next hotkey", "next")

    def set_prev_hotkey(self):
        self._set_transport_hotkey("prev_hotkey", "Play previous hotkey", "prev")

    # -- transport --------------------------------------------------------

    def toggle_pause(self):
        """Hold everything that is playing, or let it go on. One button
        for the lot: a board plays a clip or two at a time, and a pause
        per row would be a control on every row for the rare case."""
        paused = bool(self.audio_engine.paused_keys())
        self.audio_engine.pause_all(not paused)
        self._update_playing()

    def _playable_order(self):
        """The entries Next and Previous walk: this profile's, in board
        order, skipping the ones that are switched off or whose file is
        gone."""
        return [i for i, sound in enumerate(self.sounds)
                if sound.get("enabled", True)
                and os.path.exists(resolve_sound_path(sound["path"]))]

    def play_next(self, step=1, wrap=True):
        """Play the entry after the one that played last. Returns whether
        anything was played."""
        order = self._playable_order()
        if not order:
            return False
        current = self._transport_position(order)
        if current is None:
            index = order[0] if step > 0 else order[-1]
        else:
            position = current + step
            if not wrap and not 0 <= position < len(order):
                return False
            index = order[position % len(order)]
        # The one that was playing stops as this one starts, which is
        # what next means; anything else the board is playing is left
        # alone.
        if self._transport_key is not None:
            self.audio_engine.stop_key(self._transport_key)
        self.play_sound(self.sounds[index])
        return True

    def play_previous(self):
        return self.play_next(-1)

    def _transport_position(self, order):
        """Where the last played entry sits in `order`, or None when it
        is not in it any more - deleted, switched off, or from another
        profile."""
        if self._transport_key is None:
            return None
        for position, index in enumerate(order):
            if self._sound_key(self.sounds[index]) == self._transport_key:
                return position
        return None

    def _on_clip_finished(self, key):
        """The engine calling from the audio thread: hand it straight
        over rather than touching Tk here."""
        self.root.after(0, self._clip_finished, key)

    def _clip_finished(self, key):
        if not self.config.get("continuous_play"):
            return
        # Only the clip the list is walking moves it on. Anything else
        # ending - a one-off pressed by hand, a hotkey - leaves the run
        # where it was.
        if key != self._transport_key:
            return
        # No wrap: running the list means playing it to the end, not
        # round and round until someone stops it.
        self.play_next(1, wrap=False)

    def _build_controls(self, parent):
        # A FlowRow, not a plain frame: these ask for more width than the
        # default window has, and pack answers that by cutting the last
        # of them up rather than by wrapping (see flow_row).
        frame = FlowRow(parent, fg_color=COLOR_BG)
        frame.pack(side="bottom", fill="x", padx=2, pady=(GAP, 2))
        frame.add(ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        ))
        frame.add(ctk.CTkButton(
            frame, text="Add folder", command=self.add_folder,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ))
        self.record_button = frame.add(ctk.CTkButton(
            frame, text="Record", width=90, command=self.toggle_record,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ))
        frame.add(ctk.CTkButton(
            frame, text=f"Save last {REPLAY_S}s", width=110, command=self.save_replay,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ))
        frame.add(ctk.CTkButton(
            frame, text="Stop all", command=self.audio_engine.stop_all,
            fg_color=COLOR_ERROR, hover_color=COLOR_ERROR_HOVER, text_color=COLOR_ON_ERROR,
        ))
        self.stop_hotkey_button = frame.add(ctk.CTkButton(
            frame, text="", command=self.set_stop_hotkey,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ))
        self._update_stop_hotkey_button()
        self.dropout_label = frame.add(ctk.CTkLabel(
            frame, text="", text_color=COLOR_TEXT_DIM), side="right")
        frame.add(ctk.CTkButton(
            frame, text="Report a bug", width=110, command=self.report_bug,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ), side="right")
        self._update_dropout_label()

    # -- sound management -------------------------------------------------

    @staticmethod
    def _import_into_sounds_dir(source_path):
        """Copy an external file into Sounds/ (unless it's already there)
        and return the bare filename to store in config."""
        source_path = os.path.abspath(source_path)
        if same_file(os.path.dirname(source_path), SOUNDS_DIR):
            return os.path.basename(source_path)
        ensure_sounds_dir()
        filename = sanitize_filename(os.path.basename(source_path))
        candidate = os.path.join(SOUNDS_DIR, filename)
        if os.path.isfile(candidate) and filecmp.cmp(source_path, candidate, shallow=False):
            return filename  # same file was imported before
        dest = unique_path(candidate)
        shutil.copy2(source_path, dest)
        return os.path.basename(dest)

    def add_sound(self):
        paths = filedialog.askopenfilenames(
            title="Choose audio files",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if paths:
            self._add_sound_files(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Choose a folder of sounds")
        if not folder:
            return
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError as e:
            error(self.root, "Could not read folder", str(e))
            return
        # Files only: a folder of albums would otherwise pull in a board
        # nobody asked for.
        paths = [os.path.join(folder, name) for name in names
                 if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS
                 and os.path.isfile(os.path.join(folder, name))]
        if not paths:
            info(self.root, "Nothing to add", "That folder has no wav, flac, ogg or mp3 files in it.")
            return
        self._add_sound_files(paths)

    def _add_sound_files(self, paths):
        """Import several files as board entries, writing the config and
        redrawing the list once at the end rather than per file, and
        telling the user what didn't make it instead of stopping."""
        added, skipped, failed = [], [], []
        for path in paths:
            try:
                stored_path = self._import_into_sounds_dir(path)
            except OSError as e:
                failed.append(f"{os.path.basename(path)}: {e}")
                continue
            if self._find_sound(resolve_sound_path(stored_path)) is not None:
                skipped.append(os.path.basename(path))
                continue
            entry = self._new_sound_entry(
                os.path.splitext(os.path.basename(path))[0], stored_path)
            self.sounds.append(entry)
            added.append(entry)
        if added:
            save_config(self.config)
            self._refresh_sound_list()
            self._measure_sounds(added)
        self._report_import(len(added), skipped, failed)

    def _report_import(self, added, skipped, failed):
        if not skipped and not failed:
            return  # the new rows are the confirmation
        lines = [f"Added {added} sound{'s' if added != 1 else ''}."]
        if skipped:
            lines.append(f"\nAlready on the board ({len(skipped)}):\n" + _name_list(skipped))
        if failed:
            lines.append(f"\nCouldn't be added ({len(failed)}):\n" + _name_list(failed))
        show = warn if failed else info
        show(self.root, "Add sounds", "\n".join(lines))

    # -- recording ----------------------------------------------------------

    def toggle_record(self):
        """Record the microphone straight into the board. What is captured
        is the mic alone, before mute and push-to-talk, so a muted mic
        still records and the soundboard's own clips stay out of it."""
        if self.audio_engine.recording_seconds() is not None:
            self._finish_recording()
            return
        if not self.audio_engine.start_recording():
            info(self.root,
                "No microphone",
                "Choose an input device above and wait for it to start "
                "before recording.",
            )
            return
        self._update_record_button()
        self._poll_recording()

    def _poll_recording(self):
        seconds = self.audio_engine.recording_seconds()
        if seconds is None:
            return  # stopped from elsewhere
        if seconds >= RECORD_MAX_S:
            self._finish_recording(capped=True)
            return
        self._update_record_button(seconds)
        self.root.after(RECORD_POLL_MS, self._poll_recording)

    def _update_record_button(self, seconds=0.0):
        if self.audio_engine.recording_seconds() is None:
            self.record_button.configure(
                text="Record", fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER,
                text_color=COLOR_TEXT, border_width=1,
            )
            return
        self.record_button.configure(
            text=f"Stop {int(seconds) // 60}:{int(seconds) % 60:02d}",
            fg_color=COLOR_ERROR, hover_color=COLOR_ERROR_HOVER,
            text_color=COLOR_ON_ERROR, border_width=0,
        )

    def _save_mic_clip(self, data, prefix):
        """Write captured mic audio into Sounds/ and put it on the board.
        Returns the entry's name, or None when the file could not be
        written - the error is already on screen by then."""
        name = time.strftime(f"{prefix} %Y-%m-%d %H.%M.%S")
        try:
            ensure_sounds_dir()
            dest = unique_path(os.path.join(SOUNDS_DIR, sanitize_filename(name + ".wav")))
            sf.write(dest, data, SAMPLE_RATE)
        except OSError as e:
            error(self.root, f"Could not save {prefix.lower()}", str(e))
            return None
        self._add_sound_entry(name, os.path.basename(dest))
        return name

    def save_replay(self):
        """Keep what the microphone heard over the last REPLAY_S, after
        the fact. Called from the button and from its hotkey."""
        if not self.config.get("replay_buffer", True):
            info(
                self.root,
                "Replay is off",
                f"Switch on \"Keep the last {REPLAY_S} seconds\" under the mic "
                "controls, and the last few seconds can be saved after they "
                "happen.",
            )
            return
        data = self.audio_engine.take_replay()
        if data is None:
            info(
                self.root,
                "Nothing to save",
                "There is nothing in the replay buffer yet. It fills while an "
                "input device is running.",
            )
            return
        self._save_mic_clip(data, "Replay")

    def _finish_recording(self, capped=False):
        data = self.audio_engine.stop_recording()
        self._update_record_button()
        if data is None:
            info(self.root,
                "Nothing recorded",
                "The recording was too short to keep. Check that the input "
                "device is the microphone you are speaking into.",
            )
            return
        name = self._save_mic_clip(data, "Recording")
        if name is None:
            return
        if capped:
            info(self.root,
                "Recording stopped",
                f"Recordings stop after {RECORD_MAX_S // 60} minutes. "
                f"'{name}' was added to your board.",
            )

    def add_clips_to_group(self, index):
        """Turn an entry into a random group, or add to one. Triggering it
        then plays one of its clips at random."""
        sound = self.sounds[index]
        chosen = filedialog.askopenfilenames(
            title=f"Add clips to '{sound['name']}'",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if not chosen:
            return
        paths = sound_paths(sound)
        added = 0
        for path in chosen:
            try:
                stored_path = self._import_into_sounds_dir(path)
            except OSError as e:
                error(self.root, "Could not add clip", f"{os.path.basename(path)}: {e}")
                continue
            if stored_path in paths:
                continue
            paths.append(stored_path)
            added += 1
        if not added:
            return
        sound["paths"] = paths
        save_config(self.config)
        self._refresh_sound_list()
        self._measure_sounds([sound])
        self.audio_engine.preload(resolve_sound_path(p) for p in paths)

    def remove_clip_from_group(self, index, path):
        """Take one clip out of a group. The file stays in Sounds/: it may
        be on the board elsewhere, and removing a whole entry is what
        offers to delete files."""
        sound = self.sounds[index]
        paths = [p for p in sound_paths(sound) if p != path]
        if not paths:
            return
        sound["path"] = paths[0]
        if len(paths) > 1:
            sound["paths"] = paths
        else:
            sound.pop("paths", None)
        self._last_clip.pop(self._sound_key(sound), None)
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()

    def _find_sound(self, path):
        """The board entry that plays this file, if any."""
        for sound in self.sounds:
            if any(same_file(resolve_sound_path(p), path) for p in sound_paths(sound)):
                return sound
        return None

    @staticmethod
    def _new_sound_entry(name, stored_path, source=None):
        """`source` is where a downloaded clip came from, so the board can
        be shared as links rather than audio. Files picked from disk and
        clips saved by the editor have none, and aren't shareable."""
        entry = {"name": name, "path": stored_path, "hotkey": None, "enabled": True,
                 "volume": NEW_SOUND_VOLUME, "loop": False}
        if source is not None:
            entry["source"] = source
        return entry

    def _add_sound_entry(self, name, stored_path, source=None):
        entry = self._new_sound_entry(name, stored_path, source)
        self.sounds.append(entry)
        save_config(self.config)
        self._refresh_sound_list()
        self._measure_sounds([entry])

    def remove_sound(self, index):
        sound = self.sounds[index]
        # Only offer to delete files the app owns that nothing else uses.
        # A group is several files, and any of them may be on the board
        # under another entry.
        deletable = [
            path for path in (resolve_sound_path(p) for p in sound_paths(sound))
            if os.path.isfile(path) and same_file(os.path.dirname(path), SOUNDS_DIR)
            and not any(
                any(same_file(resolve_sound_path(p), path) for p in sound_paths(other))
                for i, other in enumerate(self.sounds) if i != index
            )
        ]
        delete_files = False
        if deletable:
            files = (os.path.basename(deletable[0]) if len(deletable) == 1
                     else f"{len(deletable)} files")
            answer = ask_yes_no_cancel(self.root,
                "Remove sound",
                f"Remove '{sound['name']}' from the board?\n\n"
                f"Yes: also delete {files} from the Sounds folder.\n"
                "No: keep the file.",
            )
            if answer is None:
                return
            delete_files = answer
        self.audio_engine.stop_key(self._sound_key(sound))
        self._last_clip.pop(self._sound_key(sound), None)
        del self.sounds[index]
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()
        if delete_files:
            for path in deletable:
                try:
                    os.remove(path)
                except OSError as e:
                    error(self.root, "Could not delete file", str(e))

    def set_fades(self, index):
        """Fade in, fade out and the loop crossfade for one sound. Applies
        as the sliders move, like the volume dialog, and reaches a clip
        that is already playing only the next time it is triggered - an
        envelope cannot be rewritten underneath a clip halfway through."""
        sound = self.sounds[index]

        def changed(key, seconds):
            if seconds:
                sound[key] = seconds
            else:
                sound.pop(key, None)
            self._save_config_soon()

        FadeDialog(self.root, sound["name"], sound_fades(sound),
                   sound.get("loop", False), changed)

    # -- loudness ------------------------------------------------------------

    def _measure_sounds(self, entries, force=False, on_done=None):
        """Measure the clips of these entries and remember the result on
        them, on a worker thread: it decodes whole files, and a folder
        import hands it a hundred at once. The config is written once at
        the end rather than per clip.

        A clip that will not decode, or that holds nothing to measure,
        is left unmeasured and plays untouched - a board is not the
        place to learn that a file is broken, and triggering it says so
        already."""
        jobs = [(sound, path) for sound in entries for path in sound_paths(sound)
                if force or sound_loudness(sound, path) is None]
        if not jobs:
            if on_done is not None:
                on_done([])
            return

        def work():
            measured = []
            for sound, path in jobs:
                try:
                    lufs = measure_file(resolve_sound_path(path))
                except Exception:
                    lufs = None
                measured.append((sound, path, lufs))
            self.root.after(0, self._store_measurements, measured, on_done)

        threading.Thread(target=work, daemon=True).start()

    def _store_measurements(self, measured, on_done=None):
        """Back on the Tk thread: keep what came back. Entries removed
        while the worker was running are written to and then dropped
        with the rest of the entry, which costs nothing."""
        for sound, path, lufs in measured:
            if lufs is not None:
                set_sound_loudness(sound, path, lufs)
        if any(lufs is not None for _, _, lufs in measured):
            save_config(self.config)
        if on_done is not None:
            on_done(measured)

    def measure_board(self):
        """Measure whatever on this board has no measurement yet. Called
        when level matching is switched on and when a profile is opened
        while it is on, so the switch works on boards that were built
        before any of this existed."""
        self._measure_sounds(self.sounds)

    def remeasure_sound(self, index):
        """Re-measure one entry, for a clip that was edited outside the
        app. Unlike the background pass this one was asked for, so it
        says what it found."""
        sound = self.sounds[index]
        self._measure_sounds([sound], force=True,
                             on_done=lambda measured: self._report_measurement(sound, measured))

    def _report_measurement(self, sound, measured):
        lines = [f"{os.path.basename(path)}: "
                 + (f"{lufs:.1f} LUFS" if lufs is not None else "nothing to measure")
                 for _, path, lufs in measured]
        info(self.root, "Measured", f"'{sound['name']}'\n\n" + "\n".join(lines))

    def _clip_gain(self, sound, path):
        """The per-sound volume, with the matching gain under it when
        level matching is on. The volume stays a trim on top: someone
        who has already balanced a board by hand keeps that balance."""
        gain = sound.get("volume", 100) / 100
        if self.config.get("match_levels"):
            gain *= match_gain(sound_loudness(sound, path),
                               self.config.get("reference_lufs"))
        return gain

    # -- playback -----------------------------------------------------------

    def play_sound(self, sound):
        key = self._sound_key(sound)
        # Where Next and Previous carry on from, whatever started this
        # one: a click, a hotkey, or the list running itself.
        self._transport_key = key
        # The key is the entry, not the file that happens to come up, so
        # stopping, looping and the playing indicator keep working for a
        # group whichever member is playing.
        path = self._pick_clip(sound, key)
        self.audio_engine.play(
            resolve_sound_path(path),
            gain=self._clip_gain(sound, path), loop=sound.get("loop", False), key=key,
            fades=Fades(**sound_fades(sound)),
        )

    def _pick_clip(self, sound, key):
        """A random member of a group, never the one that played last
        while there is another to choose, so a pair alternates instead of
        repeating itself."""
        paths = sound_paths(sound)
        if len(paths) == 1:
            return paths[0]
        choices = [p for p in paths if p != self._last_clip.get(key)] or paths
        chosen = random.choice(choices)
        self._last_clip[key] = chosen
        return chosen

    def _on_playback_error(self, message):
        # Called from the audio worker thread.
        self.root.after(0, lambda: error(self.root, "Playback error", message))
