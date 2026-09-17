"""The board itself: the sound list/grid, its toolbar and the buttons
below it, plus adding, removing and playing sounds."""

import filecmp
import os
import shutil
import sys
import textwrap
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .config import (
    SOUNDS_DIR,
    unique_profile_name,
    ensure_sounds_dir,
    resolve_sound_path,
    same_file,
    sanitize_filename,
    save_config,
    unique_path,
)
from .dialogs import TextDialog
from .theme import (
    COLOR_BG,
    COLOR_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    VOLUME_MAX,
)

PLAYING_POLL_MS = 100  # how often the board re-reads what the engine is playing


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
            fg_color=COLOR_ROW, button_color=COLOR_ORANGE,
            button_hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_ROW,
        )
        self.profile_menu.pack(side="left")
        manage = ctk.CTkButton(
            bar, text="Manage", width=80,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
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
            resolve_sound_path(s["path"]) for s in self.sounds if s.get("enabled", True)
        )

    def _on_profile_change(self, name):
        if name != self.config["active_profile"]:
            self._switch_profile(name)

    def _show_profile_menu(self, anchor):
        menu = tk.Menu(
            self.root, tearoff=0, bg=COLOR_ROW, fg=COLOR_TEXT,
            activebackground=COLOR_ORANGE, activeforeground=COLOR_BG,
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
        if not messagebox.askyesno(
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
            fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_ORANGE,
        )
        self.search_entry.pack(side="left", fill="x", expand=True)
        self._search_text = ""
        self.search_entry.bind("<KeyRelease>", self._on_search)
        self.view_switch = ctk.CTkSegmentedButton(
            toolbar, values=["List", "Grid"], command=self._on_view_change,
            selected_color=COLOR_ORANGE, selected_hover_color=COLOR_ORANGE_HOVER,
            unselected_color=COLOR_ROW, unselected_hover_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
        )
        self.view_switch.set("Grid" if self.config["sound_view"] == "grid" else "List")
        self.view_switch.pack(side="left", padx=(8, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color=COLOR_SURFACE,
            label_text="Sounds",
            label_text_color=COLOR_ORANGE,
        )
        self.list_frame.pack(fill="both", expand=True, padx=4, pady=6)
        self._grid_columns = 0
        self.list_frame.bind("<Configure>", self._on_list_resize)
        self._drag_from = None
        self._playing_widgets = {}
        self._refresh_sound_list()
        self._poll_playing()

    @staticmethod
    def _sound_key(sound):
        """How the engine identifies clips from this entry's file."""
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
                lines.append("  ".join(p for p in (hotkey, loop) if p) or " ")
            # A cell, not a bare button, so the tile can carry a progress
            # bar under it the way list rows do.
            cell = ctk.CTkFrame(self.list_frame, fg_color="transparent")
            cell.grid(row=position // columns, column=position % columns, sticky="ew", padx=4, pady=4)
            button = ctk.CTkButton(
                cell, text="\n".join(lines), height=84,
                command=lambda s=sound: self.play_sound(s),
                fg_color=COLOR_ORANGE if enabled and not missing else COLOR_ROW,
                hover_color=COLOR_ORANGE_HOVER,
                text_color=COLOR_BG if enabled and not missing else (COLOR_ERROR if missing else COLOR_TEXT_DIM),
            )
            button.pack(fill="x")
            menu = lambda e, i=idx: self._show_sound_menu(e, i, with_hotkey=True)
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
        outer = ctk.CTkFrame(self.list_frame, fg_color=COLOR_ROW, border_width=1, border_color=COLOR_ROW)
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
            checkmark_color=COLOR_BG,
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
        label_text = f"{name}  [{sound.get('hotkey') or 'no hotkey'}]"
        if sound.get("loop"):
            label_text += "  (loop)"
        if missing:
            label_text += "  (file missing)"
        label = ctk.CTkLabel(
            row,
            text=label_text,
            anchor="w",
            text_color=COLOR_ERROR if missing else COLOR_TEXT,
        )
        label.pack(side="left", fill="x", expand=True, padx=4)
        label.bind("<Double-Button-1>", lambda e, i=idx: self.rename_sound(i))

        volume_label = ctk.CTkLabel(row, text=f"{sound['volume']}%", width=42, text_color=COLOR_TEXT_DIM)
        volume_slider = ctk.CTkSlider(
            row, from_=0, to=VOLUME_MAX, number_of_steps=VOLUME_MAX, width=100,
            command=lambda value, s=sound, lbl=volume_label: self._on_sound_volume(s, lbl, value),
            fg_color=COLOR_SURFACE, progress_color=COLOR_ORANGE,
            button_color=COLOR_ORANGE, button_hover_color=COLOR_ORANGE_HOVER,
        )
        volume_slider.set(sound["volume"])
        volume_slider.pack(side="left", padx=(3, 0))
        volume_label.pack(side="left", padx=(0, 3))

        ctk.CTkButton(
            row, text="Play", width=60,
            command=lambda s=sound: self.play_sound(s),
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left", padx=3)
        # Built now, shown only while this sound is playing, so an idle
        # board isn't a wall of dead buttons.
        stop = ctk.CTkButton(
            row, text="Stop", width=60,
            command=lambda s=sound: self.stop_sound(s),
            fg_color=COLOR_ERROR, hover_color="#cc4444", text_color=COLOR_BG,
        )
        hotkey_button = ctk.CTkButton(
            row, text="Hotkey", width=70,
            command=lambda i=idx: self.set_hotkey(i),
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        hotkey_button.pack(side="left", padx=3)
        more = ctk.CTkButton(
            row, text="More", width=60,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        more.configure(command=lambda i=idx, b=more: self._show_sound_menu(None, i, anchor=b))
        more.pack(side="left", padx=(3, 8))

        progress = ctk.CTkProgressBar(
            outer, height=3, corner_radius=0, fg_color=COLOR_ROW, progress_color=COLOR_ROW,
        )
        progress.set(0)
        progress.pack(fill="x", padx=8, pady=(0, 4))
        self._playing_widgets[idx] = {
            "key": self._sound_key(sound), "progress": progress,
            "stop": stop, "before": hotkey_button,
        }

    def _show_sound_menu(self, event, index, anchor=None, with_hotkey=False):
        menu = tk.Menu(
            self.root, tearoff=0, bg=COLOR_ROW, fg=COLOR_TEXT,
            activebackground=COLOR_ORANGE, activeforeground=COLOR_BG,
        )
        count = len(self.sounds)
        sound = self.sounds[index]
        if with_hotkey:
            menu.add_command(label="Set hotkey...", command=lambda: self.set_hotkey(index))
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
            row.configure(border_color=COLOR_ORANGE)

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

    def _on_sound_volume(self, sound, label, value):
        sound["volume"] = int(round(value))
        label.configure(text=f"{sound['volume']}%")
        self._save_config_soon()

    def _build_controls(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_BG)
        frame.pack(fill="x", padx=4, pady=(6, 4))
        ctk.CTkButton(
            frame, text="Add sound", command=self.add_sound,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        ).pack(side="left")
        ctk.CTkButton(
            frame, text="Stop all", command=self.audio_engine.stop_all,
            fg_color=COLOR_ERROR, hover_color="#cc4444", text_color=COLOR_BG,
        ).pack(side="left", padx=(8, 0))
        self.stop_hotkey_button = ctk.CTkButton(
            frame, text="", command=self.set_stop_hotkey,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )
        self.stop_hotkey_button.pack(side="left", padx=(8, 0))
        self._update_stop_hotkey_button()
        ctk.CTkButton(
            frame, text="Report a bug", width=110, command=self.report_bug,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
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
        path = filedialog.askopenfilename(
            title="Choose audio file",
            filetypes=[("Audio files", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")],
        )
        if not path:
            return
        stored_path = self._import_into_sounds_dir(path)
        existing = self._find_sound(resolve_sound_path(stored_path))
        if existing is not None:
            messagebox.showinfo("Already added", f"This file is already on your board as '{existing['name']}'.")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        self._add_sound_entry(name, stored_path)

    def _find_sound(self, path):
        """The board entry that plays this file, if any."""
        for sound in self.sounds:
            if same_file(resolve_sound_path(sound["path"]), path):
                return sound
        return None

    def _add_sound_entry(self, name, stored_path, source=None):
        """`source` is where a downloaded clip came from, so the board can
        be shared as links rather than audio. Files picked from disk and
        clips saved by the editor have none, and aren't shareable."""
        entry = {"name": name, "path": stored_path, "hotkey": None, "enabled": True,
                 "volume": 100, "loop": False}
        if source is not None:
            entry["source"] = source
        self.sounds.append(entry)
        save_config(self.config)
        self._refresh_sound_list()

    def remove_sound(self, index):
        sound = self.sounds[index]
        path = resolve_sound_path(sound["path"])
        shared = any(
            same_file(resolve_sound_path(other["path"]), path)
            for i, other in enumerate(self.sounds) if i != index
        )
        # Only offer to delete files the app owns that nothing else uses.
        delete_file = False
        if not shared and os.path.isfile(path) and same_file(os.path.dirname(path), SOUNDS_DIR):
            answer = messagebox.askyesnocancel(
                "Remove sound",
                f"Remove '{sound['name']}' from the board?\n\n"
                f"Yes: also delete {os.path.basename(path)} from the Sounds folder.\n"
                "No: keep the file.",
            )
            if answer is None:
                return
            delete_file = answer
        self.audio_engine.stop_key(self._sound_key(sound))
        del self.sounds[index]
        save_config(self.config)
        self._refresh_sound_list()
        self._apply_hotkeys()
        if delete_file:
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Could not delete file", str(e))

    # -- playback -----------------------------------------------------------

    def play_sound(self, sound):
        path = resolve_sound_path(sound["path"])
        self.audio_engine.play(
            path, gain=sound.get("volume", 100) / 100, loop=sound.get("loop", False),
        )

    def _on_playback_error(self, message):
        # Called from the audio worker thread.
        self.root.after(0, lambda: messagebox.showerror("Playback error", message))
