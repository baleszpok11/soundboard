"""The Import / Export tab: moving a board between machines, and the
backups that make one recoverable.

A board travels in one of two files. A .sbboard lists what the board
plays and where each clip came from, and an import fetches each link
itself, so nothing is copied between people and the file stays small. A
.sbpack carries the audio as well, which is bigger but is the only way
to move clips that were recorded, edited or added from disk and have no
link to rebuild them from.

Backups sit here too, because restoring one is the same job from the
other direction: getting a board back that is no longer on screen.
"""

import os
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog

from .dialogs import ask_yes_no, error, info
import customtkinter as ctk
import yt_dlp

from . import backups, board_file, bundle
from .config import (
    APP_VERSION,
    SOUNDS_DIR,
    ensure_sounds_dir,
    resolve_sound_path,
    save_config,
    sound_paths,
    unique_profile_name,
)
from .downloader import ClipUnavailable, fetch_clip
from .theme import (
    COLOR_ACCENT_TEXT,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_ON_ACCENT,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_ROW_HOVER,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
    wrap_to_width,
)

ACTIVE_ONLY = "Active profile"
ALL_PROFILES = "All profiles"
AS_NEW = "A new profile"
INTO_CURRENT = "The current profile"
BOARD_SUFFIX = ".sbboard"
LINKS_ONLY = "Links only"
WITH_AUDIO = "With audio"
BOARD_TYPES = [("Soundboard board", "*" + BOARD_SUFFIX), ("All files", "*.*")]
BUNDLE_TYPES = [("Soundboard bundle", "*" + bundle.SUFFIX), ("All files", "*.*")]
FILE_TYPES = [("Soundboard board or bundle", "*" + BOARD_SUFFIX + " *" + bundle.SUFFIX),
              ("Soundboard board", "*" + BOARD_SUFFIX),
              ("Soundboard bundle", "*" + bundle.SUFFIX),
              ("All files", "*.*")]
NO_BACKUPS = "No backups yet"


def _size(byte_count):
    """A size someone can read at a glance: a board of two test clips is
    not "0 MB"."""
    if byte_count >= 1024 ** 2:
        return f"{byte_count / (1024 ** 2):.0f} MB"
    return f"{byte_count / 1024:.0f} KB"


class ShareMixin:
    """Import / Export tab. Expects `config`, `root` and the sound list
    helpers from Soundboard."""

    def _build_share_tab(self, parent):
        export = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        export.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(
            export, text="Export", text_color=COLOR_ACCENT_TEXT, anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 0))
        wrap_to_width(ctk.CTkLabel(
            export,
            text=("Saves your board to a file. Links only is small and shares well; "
                  "with audio carries the clips themselves, which is the only way to "
                  "move ones that weren't downloaded from a link."),
            text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
        )).pack(fill="x", padx=8, pady=(2, 6))

        row = ctk.CTkFrame(export, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(row, text="What to include:", text_color=COLOR_TEXT).pack(side="left", padx=(0, 8))
        self.export_format = ctk.CTkSegmentedButton(
            row, values=[LINKS_ONLY, WITH_AUDIO],
            command=lambda _v: self._update_export_summary(),
            selected_hover_color=COLOR_ORANGE_HOVER, unselected_hover_color=COLOR_SURFACE,
            text_color=COLOR_TEXT,
        )
        self.export_format.set(LINKS_ONLY)
        self.export_format.pack(side="left")

        row = ctk.CTkFrame(export, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(row, text="What to export:", text_color=COLOR_TEXT).pack(side="left", padx=(0, 8))
        self.export_scope = ctk.CTkSegmentedButton(
            row, values=[ACTIVE_ONLY, ALL_PROFILES], command=lambda _v: self._update_export_summary(), selected_hover_color=COLOR_ORANGE_HOVER, unselected_hover_color=COLOR_SURFACE, text_color=COLOR_TEXT,
        )
        self.export_scope.set(ACTIVE_ONLY)
        self.export_scope.pack(side="left")

        self.export_summary = wrap_to_width(ctk.CTkLabel(
            export, text="", text_color=COLOR_TEXT, anchor="w", justify="left",
        ))
        self.export_summary.pack(fill="x", padx=8, pady=(0, 6))
        self.export_button = ctk.CTkButton(
            export, text="Export...", command=self.export_board,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
        )
        self.export_button.pack(anchor="w", padx=8, pady=(0, 10))

        imp = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        imp.pack(fill="both", expand=True, padx=4, pady=4)
        ctk.CTkLabel(imp, text="Import", text_color=COLOR_ACCENT_TEXT, anchor="w").pack(
            fill="x", padx=8, pady=(8, 0)
        )
        wrap_to_width(ctk.CTkLabel(
            imp,
            text=("Opens a board file or a bundle someone shared. A board file is "
                  "links, and each clip is downloaded from its own; a bundle already "
                  "holds the audio and is unpacked into your Sounds folder. Check "
                  "what's listed below before importing."),
            text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
        )).pack(fill="x", padx=8, pady=(2, 6))

        row = ctk.CTkFrame(imp, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkButton(
            row, text="Choose file...", command=self._choose_board_file,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="left")
        self.import_file_label = ctk.CTkLabel(
            row, text="No file chosen.", text_color=COLOR_TEXT_DIM, anchor="w",
        )
        self.import_file_label.pack(side="left", padx=(8, 0))

        self.import_preview = ctk.CTkTextbox(
            imp, height=150, fg_color=COLOR_ROW, text_color=COLOR_TEXT, wrap="none",
        )
        self.import_preview.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.import_preview.configure(state="disabled")

        row = ctk.CTkFrame(imp, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(row, text="Add to:", text_color=COLOR_TEXT).pack(side="left", padx=(0, 8))
        self.import_target = ctk.CTkSegmentedButton(
            row, values=[AS_NEW, INTO_CURRENT], selected_hover_color=COLOR_ORANGE_HOVER, unselected_hover_color=COLOR_SURFACE, text_color=COLOR_TEXT,
        )
        self.import_target.set(AS_NEW)
        self.import_target.pack(side="left")

        row = ctk.CTkFrame(imp, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        self.import_button = ctk.CTkButton(
            row, text="Import", command=self.start_import,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_ON_ACCENT,
            text_color_disabled=COLOR_TEXT_DIM,
        )
        self.import_button.pack(side="left")
        self.import_cancel_button = ctk.CTkButton(
            row, text="Cancel", command=self._cancel_import,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        )
        self.import_progress = ctk.CTkProgressBar(
            imp, progress_color=COLOR_ORANGE, fg_color=COLOR_ROW,
        )
        self.import_status = wrap_to_width(ctk.CTkLabel(
            imp, text="", text_color=COLOR_TEXT, anchor="w", justify="left",
        ))
        self.import_status.pack(fill="x", padx=8, pady=(0, 8))

        self._build_backup_section(parent)

        self._import_profiles = None
        self._import_bundle = None
        self._import_cancel = None
        self.import_button.configure(state="disabled")
        self._update_export_summary()

    # -- backups --------------------------------------------------------

    def _build_backup_section(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        frame.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(
            frame, text="Backups", text_color=COLOR_ACCENT_TEXT, anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 0))
        wrap_to_width(ctk.CTkLabel(
            frame,
            text=(f"Soundboard copies your settings file once a day and keeps the last "
                  f"{backups.KEEP}. Restoring one brings back the profiles and sounds it "
                  "held; your devices and other settings are left as they are, and no "
                  "audio file is touched."),
            text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
        )).pack(fill="x", padx=8, pady=(2, 6))

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(0, 6))
        self.backup_var = tk.StringVar(value=NO_BACKUPS)
        self.backup_menu = ctk.CTkOptionMenu(
            row, variable=self.backup_var, values=[NO_BACKUPS], width=320,
            fg_color=COLOR_ROW, button_color=COLOR_ROW, button_hover_color=COLOR_ROW_HOVER,
            text_color=COLOR_TEXT,
        )
        self.backup_menu.pack(side="left")
        ctk.CTkButton(
            row, text="Back up now", command=self.backup_now,
            fg_color=COLOR_ROW, hover_color=COLOR_ROW_HOVER, text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER,
        ).pack(side="left", padx=(8, 0))
        self.restore_button = ctk.CTkButton(
            row, text="Restore board...", command=self.restore_backup,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER,
            text_color=COLOR_ON_ACCENT, text_color_disabled=COLOR_TEXT_DIM,
        )
        self.restore_button.pack(side="left", padx=(8, 0))

        self.backup_status = wrap_to_width(ctk.CTkLabel(
            frame, text="", text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
        ))
        self.backup_status.pack(fill="x", padx=8, pady=(0, 8))
        self._backup_paths = {}
        self._refresh_backup_list()

    def _refresh_backup_list(self, select=None):
        """`select` is a path just written: land on it, since a backup
        taken by hand is the one about to be restored more often than
        not. Otherwise keep whatever was chosen, while it still exists."""
        found = backups.list_backups()
        self._backup_paths = {backups.describe(*item): item[0] for item in found}
        labels = list(self._backup_paths) or [NO_BACKUPS]
        self.backup_menu.configure(values=labels)
        chosen = next((label for label, path in self._backup_paths.items()
                       if select is not None and path == select), None)
        if chosen is not None:
            self.backup_var.set(chosen)
        elif self.backup_var.get() not in labels:
            self.backup_var.set(labels[0])
        self.restore_button.configure(state="normal" if found else "disabled")

    def backup_now(self):
        path = backups.backup_now()
        self._refresh_backup_list(select=path)
        if path is None:
            self.backup_status.configure(
                text="Could not write a backup. Is the settings folder writable?",
                text_color=COLOR_ERROR)
            return
        self.backup_status.configure(text=f"Backed up to {path}", text_color=COLOR_ORANGE)

    def restore_backup(self):
        path = self._backup_paths.get(self.backup_var.get())
        if not path:
            return
        try:
            restored = backups.read_backup(path)
        except backups.BackupError as e:
            error(self.root, "Can't read that backup", str(e))
            return
        profiles = restored["profiles"]
        sounds = sum(len(p["sounds"]) for p in profiles)
        here = sum(len(p["sounds"]) for p in self.config["profiles"])
        if not ask_yes_no(
            self.root, "Restore this board?",
            f"This replaces the {len(self.config['profiles'])} profile(s) and {here} sound(s) "
            f"on this board with the {len(profiles)} profile(s) and {sounds} sound(s) in\n"
            f"{os.path.basename(path)}.\n\n"
            "Your board as it is now is backed up first, and no audio file is touched.",
        ):
            return
        safety = backups.backup_now()
        # Same moves as a profile switch, for the same reason: a clip
        # from the old board would otherwise play on with no row to stop
        # it, and the list, hotkeys and cache all describe sounds that
        # are no longer there.
        self.audio_engine.stop_all()
        self.config["profiles"] = profiles
        self.config["active_profile"] = restored["active_profile"]
        save_config(self.config)
        self.search_entry.delete(0, "end")
        self._search_text = ""
        self._refresh_profile_menu()
        self._refresh_sound_list()
        self._apply_hotkeys()
        if self.config.get("match_levels"):
            self.measure_board()
        self.audio_engine.preload(
            resolve_sound_path(clip) for s in self.sounds if s.get("enabled", True)
            for clip in sound_paths(s)
        )
        self._refresh_backup_list()
        self._update_export_summary()
        self.backup_status.configure(
            text=f"Restored {sounds} sound(s) from {os.path.basename(path)}.",
            text_color=COLOR_ORANGE)
        note = f"\n\nYour previous board was saved as\n{safety}" if safety else ""
        info(self.root, "Restored", f"{len(profiles)} profile(s), {sounds} sound(s).{note}")

    # -- export ---------------------------------------------------------

    def _export_profiles(self):
        if self.export_scope.get() == ALL_PROFILES:
            return self.config["profiles"]
        return [self.profile]

    def _with_audio(self):
        return self.export_format.get() == WITH_AUDIO

    def _update_export_summary(self):
        if self._with_audio():
            self._update_bundle_summary()
            return
        _data, skipped, partial = board_file.build(self._export_profiles())
        shareable = sum(len(p["sounds"]) for p in self._export_profiles()) - len(skipped)
        text = f"{shareable} sound(s) can be shared."
        color = COLOR_TEXT
        if skipped:
            names = ", ".join(f"'{name}'" for _profile, name in skipped[:6])
            more = f" and {len(skipped) - 6} more" if len(skipped) > 6 else ""
            text += (f"\n{len(skipped)} can't be, because they weren't downloaded from a link: "
                     f"{names}{more}.")
            color = COLOR_TEXT_DIM
        if partial:
            text += (f"\n{len(partial)} random group(s) travel as one clip: the rest were "
                     "added from disk, so there's no link to rebuild them from.")
            color = COLOR_TEXT_DIM
        self.export_summary.configure(text=text, text_color=color)
        self.export_button.configure(state="normal" if shareable else "disabled")

    def _update_bundle_summary(self):
        """What a bundle would hold, and how big it would be. The size is
        what makes people pick the other format, so it is on screen
        before the file dialog rather than after the wait."""
        _manifest, members, missing = bundle.build(self._export_profiles(), resolve_sound_path)
        total = 0
        for _arcname, real in members:
            try:
                total += os.path.getsize(real)
            except OSError:
                pass
        sounds = sum(len(p["sounds"]) for p in self._export_profiles()) - len(missing)
        text = (f"{sounds} sound(s), {len(members)} clip file(s), "
                f"about {_size(total)}.")
        color = COLOR_TEXT
        if missing:
            names = ", ".join(f"'{name}'" for _profile, name in missing[:6])
            more = f" and {len(missing) - 6} more" if len(missing) > 6 else ""
            text += (f"\n{len(missing)} can't travel in full, because their file is "
                     f"missing: {names}{more}.")
            color = COLOR_TEXT_DIM
        self.export_summary.configure(text=text, text_color=color)
        self.export_button.configure(state="normal" if members else "disabled")

    def export_board(self):
        if self._with_audio():
            self._export_bundle()
            return
        data, skipped, _partial = board_file.build(self._export_profiles(), APP_VERSION)
        default = self.profile["name"] if self.export_scope.get() == ACTIVE_ONLY else "soundboard"
        path = filedialog.asksaveasfilename(
            title="Export board", defaultextension=BOARD_SUFFIX,
            initialfile=default + BOARD_SUFFIX, filetypes=BOARD_TYPES,
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(board_file.dumps(data))
        except OSError as e:
            error(self.root, "Export failed", str(e))
            return
        note = f"\n\n{len(skipped)} sound(s) were left out; only downloaded clips can be shared." if skipped else ""
        info(self.root, "Exported", f"Saved to:\n{path}{note}")

    def _export_bundle(self):
        default = self.profile["name"] if self.export_scope.get() == ACTIVE_ONLY else "soundboard"
        path = filedialog.asksaveasfilename(
            title="Export board with audio", defaultextension=bundle.SUFFIX,
            initialfile=default + bundle.SUFFIX, filetypes=BUNDLE_TYPES,
        )
        if not path:
            return
        # Copying a board of clips into a zip is seconds of disk, not
        # milliseconds, so it happens off the Tk thread.
        profiles = [{"name": p["name"], "sounds": list(p["sounds"])}
                    for p in self._export_profiles()]
        self.export_button.configure(state="disabled")
        self.export_summary.configure(text=f"Writing {os.path.basename(path)}...",
                                      text_color=COLOR_TEXT)
        threading.Thread(target=self._bundle_export_worker, args=(path, profiles),
                         daemon=True).start()

    def _bundle_export_worker(self, path, profiles):
        try:
            missing = bundle.write(path, profiles, resolve_sound_path, APP_VERSION)
        except (OSError, ValueError) as e:
            self.root.after(0, self._finish_bundle_export, path, None, str(e))
            return
        self.root.after(0, self._finish_bundle_export, path, missing, None)

    def _finish_bundle_export(self, path, missing, failure):
        self._update_export_summary()
        if failure is not None:
            # A part-written zip is not a bundle; leaving it behind would
            # only fail later, in the app that opens it.
            try:
                os.remove(path)
            except OSError:
                pass
            error(self.root, "Export failed", failure)
            return
        note = (f"\n\n{len(missing)} sound(s) could not be included in full; their "
                "files are missing.") if missing else ""
        info(self.root, "Exported", f"Saved to:\n{path}{note}")

    # -- import ---------------------------------------------------------

    def _choose_board_file(self):
        path = filedialog.askopenfilename(title="Open board file", filetypes=FILE_TYPES)
        if not path:
            return
        # By what the file is, not what it is called: a bundle renamed to
        # .sbboard is still a zip, and would otherwise fail as JSON.
        if zipfile.is_zipfile(path):
            self._choose_bundle_file(path)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                profiles = board_file.parse(f.read())
        except (OSError, board_file.BoardFileError) as e:
            self._clear_import(str(e))
            return
        self._import_bundle = None
        self._import_profiles = profiles
        self.import_file_label.configure(text=os.path.basename(path), text_color=COLOR_TEXT)
        # Show every link before anything is fetched: the file came from
        # someone else.
        lines = []
        for profile in profiles:
            lines.append(f"{profile['name']}  ({len(profile['sounds'])} sound(s))")
            for sound in profile["sounds"]:
                lines.append(f"    {sound.get('name', '?')}  <-  {sound['source']['url']}")
        self._set_import_preview("\n".join(lines))
        self.import_button.configure(state="normal")
        self.import_status.configure(
            text=f"{len(board_file.urls(profiles))} clip(s) will be downloaded.",
            text_color=COLOR_TEXT,
        )

    def _choose_bundle_file(self, path):
        """A .sbpack: the clips are in the file, so there is nothing to
        fetch and nothing to check a link against - the list below says
        what would be unpacked instead."""
        try:
            profiles = bundle.read(path)
        except bundle.BundleError as e:
            self._clear_import(str(e))
            return
        self._import_profiles = None
        self._import_bundle = (path, profiles)
        self.import_file_label.configure(text=os.path.basename(path), text_color=COLOR_TEXT)
        lines = []
        for profile in profiles:
            lines.append(f"{profile['name']}  ({len(profile['sounds'])} sound(s))")
            for sound in profile["sounds"]:
                files = ", ".join(os.path.basename(f) for f in sound["files"])
                lines.append(f"    {sound.get('name', '?')}  <-  {files}")
        self._set_import_preview("\n".join(lines))
        self.import_button.configure(state="normal")
        self.import_status.configure(
            text=f"{bundle.clip_count(profiles)} clip(s) will be copied into your Sounds folder.",
            text_color=COLOR_TEXT,
        )

    def _clear_import(self, message):
        self._set_import_preview("")
        self.import_file_label.configure(text="No file chosen.", text_color=COLOR_TEXT_DIM)
        self._import_profiles = None
        self._import_bundle = None
        self.import_button.configure(state="disabled")
        error(self.root, "Can't open that file", message)

    def _set_import_preview(self, text):
        self.import_preview.configure(state="normal")
        self.import_preview.delete("1.0", tk.END)
        self.import_preview.insert("1.0", text)
        self.import_preview.configure(state="disabled")

    def start_import(self):
        if not self._import_profiles and not self._import_bundle:
            return
        self.import_button.configure(state="disabled")
        self.import_cancel_button.configure(state="normal")
        self.import_cancel_button.pack(side="left", padx=(8, 0))
        self.import_progress.set(0)
        self.import_progress.pack(fill="x", padx=8, pady=(0, 6), before=self.import_status)
        self.import_status.configure(text="Starting import...", text_color=COLOR_TEXT)
        # Profiles made by a previous import must not be reused by this one.
        self._import_made = {}
        self._import_cancel = cancel = threading.Event()
        if self._import_bundle:
            path, profiles = self._import_bundle
            threading.Thread(
                target=self._bundle_import_worker,
                args=(path, profiles, self.import_target.get(), cancel),
                daemon=True,
            ).start()
            return
        threading.Thread(
            target=self._import_worker,
            args=(self._import_profiles, self.import_target.get(), cancel),
            daemon=True,
        ).start()

    def _cancel_import(self):
        if self._import_cancel is not None:
            self._import_cancel.set()
            self.import_cancel_button.configure(state="disabled")
            self.import_status.configure(text="Cancelling...", text_color=COLOR_TEXT)

    def _import_worker(self, profiles, target, cancel):
        total = len(board_file.urls(profiles))
        done, added, failures = 0, [], []
        for profile in profiles:
            for sound in profile["sounds"]:
                if cancel.is_set():
                    break
                name = sound.get("name") or "?"
                url = sound["source"]["url"]
                self.root.after(0, lambda n=name, d=done: self.import_status.configure(
                    text=f"Downloading {d + 1} of {total}: {n}", text_color=COLOR_TEXT))
                try:
                    path, title = fetch_clip(
                        url, sound["source"].get("start"), sound["source"].get("end"),
                        None, cancel,
                    )
                    added.append((profile["name"], sound, path, name or title))
                except yt_dlp.utils.DownloadCancelled:
                    break
                except ClipUnavailable as e:
                    failures.append((name, str(e)))
                except Exception as e:
                    # One dead link shouldn't take the rest of the import
                    # down with it.
                    failures.append((name, str(e)))
                done += 1
                self.root.after(0, lambda d=done: self.import_progress.set(d / total if total else 1))
            if cancel.is_set():
                break
        self.root.after(0, lambda: self._finish_import(added, failures, target, cancel))

    def _finish_import(self, added, failures, target, cancel):
        self._import_cancel = None
        self.import_cancel_button.pack_forget()
        self.import_progress.pack_forget()
        self.import_button.configure(
            state="normal" if (self._import_profiles or self._import_bundle) else "disabled")

        for source_profile, sound, path, name in added:
            profile = self._import_destination(target, source_profile)
            if any(os.path.abspath(resolve_sound_path(s["path"])) == os.path.abspath(path)
                   for s in profile["sounds"]):
                continue  # already on this board
            entry = {
                "name": name, "path": os.path.basename(path),
                "hotkey": self._free_hotkey(profile, sound.get("hotkey")),
                "enabled": sound.get("enabled", True),
                "volume": sound.get("volume", 100),
                "loop": sound.get("loop", False),
                "source": dict(sound["source"]),
            }
            profile["sounds"].append(entry)
        save_config(self.config)
        self._refresh_profile_menu()
        self._refresh_sound_list()
        self._apply_hotkeys()

        if cancel.is_set():
            text, color = f"Import cancelled. {len(added)} sound(s) were added first.", COLOR_TEXT_DIM
        elif failures:
            listed = "\n".join(f"  {name}: {why}" for name, why in failures[:8])
            text = (f"Imported {len(added)} sound(s). {len(failures)} could not be downloaded:\n{listed}")
            color = COLOR_ERROR
        else:
            text, color = f"Imported {len(added)} sound(s).", COLOR_ORANGE
        self.import_status.configure(text=text, text_color=color)

    # -- import: a bundle -----------------------------------------------

    def _bundle_import_worker(self, path, profiles, target, cancel):
        """Unpack the clips. One file at a time rather than extractall,
        so each one lands under a name of our choosing and a cancel is
        honoured between clips."""
        total = bundle.clip_count(profiles)
        done, unpacked, failures = 0, [], []
        ensure_sounds_dir()
        try:
            with zipfile.ZipFile(path) as zf:
                for profile in profiles:
                    for sound in profile["sounds"]:
                        if cancel.is_set():
                            break
                        name = sound.get("name") or "?"
                        self.root.after(0, lambda n=name, d=done: self.import_status.configure(
                            text=f"Unpacking {d + 1} of {total}: {n}", text_color=COLOR_TEXT))
                        written = []
                        try:
                            for member in sound["files"]:
                                written.append(bundle.extract(zf, member, SOUNDS_DIR))
                                done += 1
                                self.root.after(0, lambda d=done: self.import_progress.set(
                                    d / total if total else 1))
                        except (OSError, ValueError, zipfile.BadZipFile) as e:
                            # Half a group is not a sound: drop what this
                            # entry wrote and carry on with the next.
                            for stray in written:
                                try:
                                    os.remove(stray)
                                except OSError:
                                    pass
                            failures.append((name, str(e)))
                            continue
                        unpacked.append((profile["name"], sound, written))
                    if cancel.is_set():
                        break
        except (OSError, zipfile.BadZipFile) as e:
            failures.append((os.path.basename(path), str(e)))
        self.root.after(0, lambda: self._finish_bundle_import(unpacked, failures, target, cancel))

    def _finish_bundle_import(self, unpacked, failures, target, cancel):
        self._import_cancel = None
        self.import_cancel_button.pack_forget()
        self.import_progress.pack_forget()
        self.import_button.configure(
            state="normal" if (self._import_profiles or self._import_bundle) else "disabled")

        for source_profile, sound, paths in unpacked:
            profile = self._import_destination(target, source_profile)
            names = [os.path.basename(p) for p in paths]
            entry = {
                "name": sound.get("name") or os.path.splitext(names[0])[0],
                "path": names[0],
                "hotkey": self._free_hotkey(profile, sound.get("hotkey")),
                "enabled": sound.get("enabled", True),
                "volume": sound.get("volume", 100),
                "loop": sound.get("loop", False),
            }
            if len(names) > 1:
                entry["paths"] = names
            for key in ("fade_in", "fade_out", "crossfade"):
                if sound.get(key):
                    entry[key] = sound[key]
            if sound.get("source", {}).get("url"):
                entry["source"] = dict(sound["source"])
            profile["sounds"].append(entry)
        save_config(self.config)
        self._refresh_profile_menu()
        self._refresh_sound_list()
        self._apply_hotkeys()
        if unpacked and self.config.get("match_levels"):
            # These clips arrived with no measurement of their own, and
            # nothing else would measure them until the next start.
            self.measure_board()

        if cancel.is_set():
            text = f"Import cancelled. {len(unpacked)} sound(s) were added first."
            color = COLOR_TEXT_DIM
        elif failures:
            listed = "\n".join(f"  {name}: {why}" for name, why in failures[:8])
            text = f"Imported {len(unpacked)} sound(s). {len(failures)} could not be unpacked:\n{listed}"
            color = COLOR_ERROR
        else:
            text, color = f"Imported {len(unpacked)} sound(s) with their audio.", COLOR_ORANGE
        self.import_status.configure(text=text, text_color=color)

    def _import_destination(self, target, source_profile):
        """Where an imported sound lands. A new profile is made once per
        profile in the file, not once per sound."""
        if target == INTO_CURRENT:
            return self.profile
        existing = getattr(self, "_import_made", {})
        if source_profile in existing:
            return existing[source_profile]
        name = unique_profile_name(source_profile, set(self._profile_names()))
        profile = {"name": name, "sounds": []}
        self.config["profiles"].append(profile)
        existing[source_profile] = profile
        self._import_made = existing
        return profile

    @staticmethod
    def _free_hotkey(profile, hotkey):
        """Drop an imported hotkey that the target profile already uses,
        rather than letting two sounds shadow each other."""
        if not hotkey:
            return None
        if any(s.get("hotkey") == hotkey for s in profile["sounds"]):
            return None
        return hotkey
