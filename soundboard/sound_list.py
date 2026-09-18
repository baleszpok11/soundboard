"""The board itself: the sound list/grid, its toolbar and the buttons
below it, plus adding, removing and playing sounds."""

import filecmp
import os
import random
import shutil
import sys
import textwrap
import time
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
import soundfile as sf

from .audio_engine import RECORD_MAX_S, REPLAY_S, SAMPLE_RATE
from .config import (
    NEW_SOUND_VOLUME,
    SOUNDS_DIR,
    unique_profile_name,
    ensure_sounds_dir,
    resolve_sound_path,
    same_file,
    sound_paths,
    sanitize_filename,
    save_config,
    unique_path,
)
from .dialogs import (
    TextDialog,
    VolumeDialog,
    ask_yes_no,
    ask_yes_no_cancel,
    error,
    info,
    warn,
)
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
RECORD_POLL_MS = 200  # how often the Record button's elapsed time is redrawn
AUDIO_EXTENSIONS = (".wav", ".flac", ".ogg", ".mp3")
IMPORT_REPORT_NAMES = 10  # names listed before the rest are counted


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
        self.list_frame = ctk.CTkScrollableFrame(parent, fg_color=COLOR_BG)
        self.list_frame.pack(fill="both", expand=True, padx=2, pady=(0, GAP))
        self._grid_columns = 0
        # add="+" matters: CTkScrollableFrame binds <Configure> on this same
        # frame to refresh the canvas scrollregion. Replacing that binding
        # leaves the region empty, and an empty region means the canvas
        # believes everything fits - no scrollbar, no wheel, and every row
        # past the first screenful unreachable.
        self.list_frame.bind("<Configure>", self._on_list_resize, add="+")
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

    def _poll_playing(self):
        """Mirror what the engine is playing onto the board."""
        playing = self.audio_engine.active_keys()
        for entry in self._playing_widgets.values():
            self._show_playing(entry, playing.get(entry["key"]))
        self._playing_poll = self.root.after(PLAYING_POLL_MS, self._poll_playing)

    @staticmethod
    def _show_playing(entry, fraction):
        progress, stop = entry["progress"], entry["stop"]
        idle = progress.cget("fg_color")
        if fraction is None:
            progress.configure(progress_color=idle)
            progress.set(0)
            if stop is not None and stop.winfo_manager():
                stop.pack_forget()
            return
        progress.configure(progress_color=COLOR_ORANGE)
        progress.set(fraction)
        if stop is not None and not stop.winfo_manager():
            stop.pack(side="left", padx=3, before=entry["before"])

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
        width = self.list_frame.winfo_width()
        return max(1, width // 190) if width > 1 else 4

    def _on_list_resize(self, _event):
        if self.config["sound_view"] == "grid" and self._grid_column_count() != self._grid_columns:
            self._refresh_sound_list()

    def _visible_sounds(self):
        """(index, sound) pairs matching the search box."""
        query = self._search_text
        return [
            (i, s) for i, s in enumerate(self.sounds)
            if not query or query in s["name"].lower()
        ]

    def _refresh_sound_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()
        self._playing_widgets = {}  # rebuilt below; the poll reads it
        visible = self._visible_sounds()
        if not visible:
            text = "No sounds match your search." if self.sounds else "No sounds yet. Click Add sound or use the Download tab."
            ctk.CTkLabel(self.list_frame, text=text, text_color=COLOR_TEXT_DIM).pack(pady=20)
        elif self.config["sound_view"] == "grid":
            self._build_sound_grid(visible)
        else:
            for idx, sound in visible:
                self._build_sound_row(idx, sound)
        if hasattr(self, "editor_sound_menu"):
            self._refresh_editor_sound_list()
        if hasattr(self, "export_summary"):
            self._update_export_summary()

    def _build_sound_grid(self, visible):
        columns = self._grid_column_count()
        self._grid_columns = columns
        for column in range(columns):
            self.list_frame.grid_columnconfigure(column, weight=1, uniform="sound")
        for position, (idx, sound) in enumerate(visible):
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
                lines.append("  ".join(p for p in (hotkey, loop, group) if p) or " ")
            # A cell, not a bare button, so the tile can carry a progress
            # bar under it the way list rows do.
            cell = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            cell.grid(row=position // columns, column=position % columns, sticky="ew", padx=4, pady=4)
            button = ctk.CTkButton(
                cell, text="\n".join(lines), height=84,
                command=lambda s=sound: self.play_sound(s),
                fg_color=COLOR_ORANGE if enabled and not missing else COLOR_ROW,
                hover_color=COLOR_ORANGE_HOVER,
                text_color=COLOR_ON_ACCENT if enabled and not missing else (COLOR_ERROR if missing else COLOR_TEXT_DIM),
            )
            button.pack(fill="x")
            menu = lambda e, i=idx: self._show_sound_menu(e, i, from_tile=True)
            button.bind("<Button-3>", menu)
            button.bind("<Button-2>" if sys.platform == "darwin" else "<Control-Button-1>", menu)
            progress = ctk.CTkProgressBar(
                cell, height=3, corner_radius=0, fg_color=COLOR_SURFACE, progress_color=COLOR_SURFACE,
            )
            progress.set(0)
            progress.pack(fill="x", pady=(2, 0))
            # Tiles are a single button, so stopping is done from the menu.
            self._playing_widgets[idx] = {
                "key": self._sound_key(sound), "progress": progress,
                "stop": None, "before": None,
            }

    def _build_sound_row(self, idx, sound):
        resolved_path = resolve_sound_path(sound["path"])
        # The outer frame carries the drag target and the progress bar; the
        # inner one keeps the controls on a single line.
        outer = ctk.CTkFrame(
            self.list_frame, fg_color=COLOR_SURFACE, corner_radius=RADIUS_CONTROL,
            border_width=CARD_BORDER, border_color=COLOR_BORDER,
        )
        outer.pack(fill="x", pady=3, padx=2)
        outer.sound_index = idx
        row = ctk.CTkFrame(outer, fg_color="transparent")
        row.pack(fill="x")

        handle = ctk.CTkLabel(row, text="::", width=16, text_color=COLOR_TEXT_DIM, cursor="fleur")
        handle.pack(side="left", padx=(8, 0))
        handle.bind("<ButtonPress-1>", lambda e, i=idx: self._start_drag(i))
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
        if sound.get("enabled", True):
            checkbox.select()
        else:
            checkbox.deselect()
        checkbox.configure(command=lambda i=idx, cb=checkbox: self._on_toggle_sound(i, cb))
        checkbox.pack(side="left", padx=(6, 4))

        missing = not os.path.exists(resolved_path)
        name = sound["name"]
        if len(name) > 40:  # long titles would push the buttons out of the row
            name = name[:37] + "..."
        # Name and status are two labels, not one string: the name is what
        # the eye looks for, and running them together at one weight made
        # the hotkey and the warnings compete with it.
        names = ctk.CTkFrame(row, fg_color="transparent")
        names.pack(side="left", fill="x", expand=True, padx=GAP)
        label = ctk.CTkLabel(
            names, text=name, anchor="w", font=font("body_bold"),
            text_color=COLOR_ERROR_TEXT if missing else COLOR_TEXT,
        )
        label.pack(fill="x")
        meta = [sound.get("hotkey") or "no hotkey"]
        clips = len(sound_paths(sound))
        if clips > 1:
            meta.append(f"{clips} clips, random")
        if sound.get("loop"):
            meta.append("loop")
        if missing:
            meta.append("file missing")
        meta_label = ctk.CTkLabel(
            names, text="  -  ".join(meta), anchor="w", font=font("small"),
            text_color=COLOR_ERROR_TEXT if missing else COLOR_TEXT_DIM,
        )
        meta_label.pack(fill="x")
        for widget in (label, meta_label):
            widget.bind("<Double-Button-1>", lambda e, i=idx: self.rename_sound(i))

        volume_label = ctk.CTkLabel(
            row, text=f"{sound['volume']}%", width=42,
            font=font("small"), text_color=COLOR_TEXT_DIM,
        )
        volume_slider = ctk.CTkSlider(
            row, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, width=100,
            command=lambda value, s=sound, lbl=volume_label: self._on_sound_volume(s, lbl, value),
        )
        volume_slider.set(sound["volume"])
        volume_slider.pack(side="left", padx=(3, 0))
        volume_label.pack(side="left", padx=(0, 3))

        ctk.CTkButton(
            row, text="Play", width=64, font=font("body_bold"),
            command=lambda s=sound: self.play_sound(s),
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        ).pack(side="left", padx=3)
        # Built now, shown only while this sound is playing, so an idle
        # board isn't a wall of dead buttons.
        stop = ctk.CTkButton(
            row, text="Stop", width=60,
            command=lambda s=sound: self.stop_sound(s),
            fg_color=COLOR_ERROR, hover_color=COLOR_ERROR_HOVER, text_color=COLOR_ON_ERROR,
        )
        hotkey_button = ctk.CTkButton(
            row, text="Hotkey", width=70,
            command=lambda i=idx: self.set_hotkey(i),
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        hotkey_button.pack(side="left", padx=3)
        more = ctk.CTkButton(
            row, text="More", width=60,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        more.configure(command=lambda i=idx, b=more: self._show_sound_menu(None, i, anchor=b))
        more.pack(side="left", padx=(3, 8))

        progress = ctk.CTkProgressBar(
            outer, height=3, corner_radius=0,
            fg_color=COLOR_SURFACE, progress_color=COLOR_SURFACE,
        )
        progress.set(0)
        progress.pack(fill="x", padx=8, pady=(0, 4))
        self._playing_widgets[idx] = {
            "key": self._sound_key(sound), "progress": progress,
            "stop": stop, "before": hotkey_button,
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

    def _build_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        frame.pack(side="bottom", fill="x", padx=2, pady=(GAP, 2))
        ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        ).pack(side="left")
        ctk.CTkButton(
            frame, text="Add folder", command=self.add_folder,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="left", padx=(8, 0))
        self.record_button = ctk.CTkButton(
            frame, text="Record", width=90, command=self.toggle_record,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.record_button.pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            frame, text=f"Save last {REPLAY_S}s", width=110, command=self.save_replay,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            frame, text="Stop all", command=self.audio_engine.stop_all,
            fg_color=COLOR_ERROR, hover_color=COLOR_ERROR_HOVER, text_color=COLOR_ON_ERROR,
        ).pack(side="left", padx=(8, 0))
        self.stop_hotkey_button = ctk.CTkButton(
            frame, text="", command=self.set_stop_hotkey,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.stop_hotkey_button.pack(side="left", padx=(8, 0))
        self._update_stop_hotkey_button()
        ctk.CTkButton(
            frame, text="Report a bug", width=110, command=self.report_bug,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="right", padx=(8, 0))
        self.dropout_label = ctk.CTkLabel(frame, text="", text_color=COLOR_TEXT_DIM)
        self.dropout_label.pack(side="right")
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
        added, skipped, failed = 0, [], []
        for path in paths:
            try:
                stored_path = self._import_into_sounds_dir(path)
            except OSError as e:
                failed.append(f"{os.path.basename(path)}: {e}")
                continue
            if self._find_sound(resolve_sound_path(stored_path)) is not None:
                skipped.append(os.path.basename(path))
                continue
            self.sounds.append(self._new_sound_entry(
                os.path.splitext(os.path.basename(path))[0], stored_path))
            added += 1
        if added:
            save_config(self.config)
            self._refresh_sound_list()
        self._report_import(added, skipped, failed)

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
        self.sounds.append(self._new_sound_entry(name, stored_path, source))
        save_config(self.config)
        self._refresh_sound_list()

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

    # -- playback -----------------------------------------------------------

    def play_sound(self, sound):
        key = self._sound_key(sound)
        # The key is the entry, not the file that happens to come up, so
        # stopping, looping and the playing indicator keep working for a
        # group whichever member is playing.
        self.audio_engine.play(
            resolve_sound_path(self._pick_clip(sound, key)),
            gain=sound.get("volume", 100) / 100, loop=sound.get("loop", False), key=key,
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
