"""The Import / Export tab: share a board as links rather than audio.

An export lists what the board plays and where each clip came from. An
import fetches each link itself, so nothing is copied between people and
the file stays small.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yt_dlp

from . import board_file
from .config import APP_VERSION, resolve_sound_path, save_config, unique_profile_name
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
FILE_TYPES = [("Soundboard board", "*" + BOARD_SUFFIX), ("All files", "*.*")]


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
            text=("Saves a file listing your sounds and the links they came from. "
                  "The audio isn't included - whoever opens it downloads their own copy."),
            text_color=COLOR_TEXT_DIM, anchor="w", justify="left",
        )).pack(fill="x", padx=8, pady=(2, 6))

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
            text=("Opens a board file someone shared and downloads each clip from its "
                  "original link. Check the links below before importing."),
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

        self._import_profiles = None
        self._import_cancel = None
        self.import_button.configure(state="disabled")
        self._update_export_summary()

    # -- export ---------------------------------------------------------

    def _export_profiles(self):
        if self.export_scope.get() == ALL_PROFILES:
            return self.config["profiles"]
        return [self.profile]

    def _update_export_summary(self):
        _data, skipped = board_file.build(self._export_profiles())
        shareable = sum(len(p["sounds"]) for p in self._export_profiles()) - len(skipped)
        text = f"{shareable} sound(s) can be shared."
        color = COLOR_TEXT
        if skipped:
            names = ", ".join(f"'{name}'" for _profile, name in skipped[:6])
            more = f" and {len(skipped) - 6} more" if len(skipped) > 6 else ""
            text += (f"\n{len(skipped)} can't be, because they weren't downloaded from a link: "
                     f"{names}{more}.")
            color = COLOR_TEXT_DIM
        self.export_summary.configure(text=text, text_color=color)
        self.export_button.configure(state="normal" if shareable else "disabled")

    def export_board(self):
        data, skipped = board_file.build(self._export_profiles(), APP_VERSION)
        default = self.profile["name"] if self.export_scope.get() == ACTIVE_ONLY else "soundboard"
        path = filedialog.asksaveasfilename(
            title="Export board", defaultextension=BOARD_SUFFIX,
            initialfile=default + BOARD_SUFFIX, filetypes=FILE_TYPES,
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(board_file.dumps(data))
        except OSError as e:
            messagebox.showerror("Export failed", str(e))
            return
        note = f"\n\n{len(skipped)} sound(s) were left out; only downloaded clips can be shared." if skipped else ""
        messagebox.showinfo("Exported", f"Saved to:\n{path}{note}")

    # -- import ---------------------------------------------------------

    def _choose_board_file(self):
        path = filedialog.askopenfilename(title="Open board file", filetypes=FILE_TYPES)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                profiles = board_file.parse(f.read())
        except (OSError, board_file.BoardFileError) as e:
            self._set_import_preview("")
            self.import_file_label.configure(text="No file chosen.", text_color=COLOR_TEXT_DIM)
            self._import_profiles = None
            self.import_button.configure(state="disabled")
            messagebox.showerror("Can't open that file", str(e))
            return
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

    def _set_import_preview(self, text):
        self.import_preview.configure(state="normal")
        self.import_preview.delete("1.0", tk.END)
        self.import_preview.insert("1.0", text)
        self.import_preview.configure(state="disabled")

    def start_import(self):
        if not self._import_profiles:
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
        self.import_button.configure(state="normal" if self._import_profiles else "disabled")

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
