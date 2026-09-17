"""The Download tab: fetching a clip with yt-dlp and adding it to the board."""

import contextlib
import glob
import os
import sys
import threading
import time
import webbrowser

import customtkinter as ctk
import imageio_ffmpeg
import yt_dlp
import yt_dlp.version

from .config import SOUNDS_DIR, ensure_sounds_dir
from .downloader import (
    DOWNLOADER_STALE_DAYS,
    RELEASES_URL,
    TrimAudioPP,
    downloader_age_days,
    format_seconds,
    parse_time,
    source_for,
    update_hint,
)
from .theme import (
    COLOR_BG,
    COLOR_ERROR,
    COLOR_ORANGE,
    COLOR_ORANGE_HOVER,
    COLOR_ROW,
    COLOR_SURFACE,
    COLOR_TEXT,
    COLOR_TEXT_DIM,
)


class DownloadMixin:
    """Download tab UI and its worker thread. Expects `root` from
    Soundboard, and `_find_sound`/`_add_sound_entry` from SoundListMixin."""

    def _build_download_tab(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE)
        frame.pack(fill="x", padx=4, pady=4)

        ctk.CTkLabel(
            frame, text="Video/clip URL (YouTube, TikTok, Instagram, ...):", text_color=COLOR_TEXT
        ).pack(anchor="w", padx=8, pady=(8, 2))
        self.download_url_entry = ctk.CTkEntry(
            frame,
            placeholder_text="https://...",
            fg_color=COLOR_ROW,
            text_color=COLOR_TEXT,
            border_color=COLOR_ORANGE,
        )
        self.download_url_entry.pack(fill="x", padx=8, pady=(0, 8))

        times = ctk.CTkFrame(frame, fg_color="transparent")
        times.pack(anchor="w", padx=8, pady=(0, 8))
        entries = []
        for label, placeholder in (("Start:", "0:00"), ("End:", "end")):
            ctk.CTkLabel(times, text=label, text_color=COLOR_TEXT).pack(side="left", padx=(0, 4))
            entry = ctk.CTkEntry(
                times, width=80, placeholder_text=placeholder,
                fg_color=COLOR_ROW, text_color=COLOR_TEXT, border_color=COLOR_ORANGE,
            )
            entry.pack(side="left", padx=(0, 12))
            entries.append(entry)
        self.download_start_entry, self.download_end_entry = entries
        ctk.CTkLabel(
            times, text="(optional, e.g. 1:30)", text_color=COLOR_TEXT_DIM,
        ).pack(side="left")

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.pack(anchor="w", padx=8, pady=(0, 8))
        self.download_button = ctk.CTkButton(
            buttons, text="Download as MP3", command=self._start_download,
            fg_color=COLOR_ORANGE, hover_color=COLOR_ORANGE_HOVER, text_color=COLOR_BG,
        )
        self.download_button.pack(side="left")
        self.download_cancel_button = ctk.CTkButton(
            buttons, text="Cancel", command=self._cancel_download,
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )

        self.download_progress = ctk.CTkProgressBar(
            frame, progress_color=COLOR_ORANGE, fg_color=COLOR_ROW,
        )
        self.download_status = ctk.CTkLabel(
            frame, text="", text_color=COLOR_TEXT, anchor="w", justify="left", wraplength=800,
        )
        self.download_status.pack(fill="x", padx=8, pady=(0, 8))
        self._download_cancel = None
        self.download_update_button = ctk.CTkButton(
            frame, text="Open releases page", command=lambda: webbrowser.open(RELEASES_URL),
            fg_color=COLOR_ROW, hover_color=COLOR_SURFACE, text_color=COLOR_ORANGE,
            border_width=1, border_color=COLOR_ORANGE,
        )

        version_text = f"Downloader: yt-dlp {yt_dlp.version.__version__}"
        age = downloader_age_days()
        if age is not None and age > DOWNLOADER_STALE_DAYS:
            version_text += f" ({age} days old; if downloads fail, {update_hint()})"
        ctk.CTkLabel(
            frame, text=version_text, text_color=COLOR_TEXT_DIM, anchor="w", justify="left", wraplength=800,
        ).pack(fill="x", padx=8, pady=(0, 8))

        ctk.CTkLabel(
            parent,
            text=(
                "Downloads are saved into the Sounds folder and added to your board "
                "automatically. Only download content you have the right to use - "
                "this may be against the source site's terms of service."
            ),
            text_color=COLOR_TEXT_DIM,
            anchor="w",
            justify="left",
            wraplength=480,
        ).pack(fill="x", padx=12, pady=(0, 8))

    def _start_download(self):
        url = self.download_url_entry.get().strip()
        if not url:
            return
        try:
            start = parse_time(self.download_start_entry.get())
            end = parse_time(self.download_end_entry.get())
            if start is not None and end is not None and end <= start:
                raise ValueError("The end time must be after the start time.")
        except ValueError as e:
            self.download_status.configure(text=str(e), text_color=COLOR_ERROR)
            return
        self.download_update_button.pack_forget()
        self.download_button.configure(state="disabled")
        self.download_cancel_button.configure(state="normal")
        self.download_cancel_button.pack(side="left", padx=(8, 0))
        self.download_progress.set(0)
        self.download_progress.pack(fill="x", padx=8, pady=(0, 8), before=self.download_status)
        self.download_status.configure(text="Starting download...", text_color=COLOR_TEXT)
        self._download_cancel = cancel = threading.Event()
        threading.Thread(
            target=self._download_worker, args=(url, start, end, cancel), daemon=True
        ).start()

    def _cancel_download(self):
        if self._download_cancel is not None:
            self._download_cancel.set()
            self.download_cancel_button.configure(state="disabled")
            self.download_status.configure(text="Cancelling...", text_color=COLOR_TEXT)

    def _download_worker(self, url, start, end, cancel):
        partial_files = set()
        last_update = [0.0]

        def report(fraction, text):
            now = time.monotonic()
            if fraction is not None and 0 < fraction < 1 and now - last_update[0] < 0.1:
                return
            last_update[0] = now
            self.root.after(0, lambda: self._on_download_progress(cancel, fraction, text))

        def on_download(d):
            if d["status"] == "downloading":
                partial_files.add(d["tmpfilename"])
            elif d["status"] == "finished" and partial_files:
                partial_files.add(d["filename"])
            if cancel.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if d["status"] != "downloading":
                return
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                report(min(done / total, 1.0), f"Downloading... {done / total:.0%} of {total / 1e6:.1f} MB")
            else:
                report(None, f"Downloading... {done / 1e6:.1f} MB")

        def on_postprocess(d):
            if d["status"] != "started":
                return
            if cancel.is_set():
                # Only files this download created are removed.
                if partial_files:
                    partial_files.add(d["info_dict"]["filepath"])
                raise yt_dlp.utils.DownloadCancelled()
            text = "Trimming..." if d["postprocessor"] == "TrimAudio" else "Converting to MP3..."
            report(1.0, text)

        try:
            ensure_sounds_dir()
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            # The id keeps different videos with the same title apart; yt-dlp
            # would otherwise reuse the existing file. A time range gets its own name.
            suffix = ""
            if start is not None or end is not None:
                suffix = f" {format_seconds(start or 0)}-{format_seconds(end) if end is not None else 'end'}"
            outtmpl = os.path.join(SOUNDS_DIR, "%(title).100s [%(id)s]" + suffix + ".%(ext)s")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "ffmpeg_location": ffmpeg_path,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "progress_hooks": [on_download],
                "postprocessor_hooks": [on_postprocess],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if suffix:
                    ydl.add_post_processor(TrimAudioPP(ydl, start, end), when="post_process")
                info = ydl.extract_info(url, download=False, process=False)
                message = None
                if info.get("is_live") or info.get("live_status") == "is_live":
                    message = "Live streams can't be downloaded."
                elif start and info.get("duration") and start >= info["duration"]:
                    message = "The start time is past the end of the video."
                if message:
                    self.root.after(0, lambda: self._on_download_error(message, hint=False))
                    return
                info = ydl.process_ie_result(info, download=True)
                base = ydl.prepare_filename(info)
                final_path = os.path.splitext(base)[0] + ".mp3"
                title = info.get("title") or os.path.splitext(os.path.basename(final_path))[0]
        except Exception as e:
            if cancel.is_set():
                for path in partial_files:
                    for leftover in [path, path.removesuffix(".part") + ".ytdl", *glob.glob(glob.escape(path) + "-Frag*")]:
                        with contextlib.suppress(OSError):
                            os.remove(leftover)
                self.root.after(0, self._on_download_cancelled)
            else:
                message = str(e)
                self.root.after(0, lambda: self._on_download_error(message))
            return
        source = source_for(url, start, end)
        self.root.after(0, lambda: self._on_download_done(final_path, title, source))

    def _on_download_progress(self, cancel, fraction, text):
        if cancel is not self._download_cancel or cancel.is_set():
            return
        if fraction is None:
            self.download_progress.configure(mode="indeterminate")
            self.download_progress.start()
        else:
            if self.download_progress.cget("mode") != "determinate":
                self.download_progress.stop()
                self.download_progress.configure(mode="determinate")
            self.download_progress.set(fraction)
        self.download_status.configure(text=text, text_color=COLOR_TEXT)
        if fraction == 1.0:
            # Conversion can't be interrupted cleanly.
            self.download_cancel_button.configure(state="disabled")

    def _finish_download(self):
        self._download_cancel = None
        self.download_button.configure(state="normal")
        self.download_cancel_button.pack_forget()
        self.download_progress.stop()
        self.download_progress.configure(mode="determinate")
        self.download_progress.pack_forget()

    def _on_download_cancelled(self):
        self._finish_download()
        self.download_status.configure(text="Download cancelled.", text_color=COLOR_TEXT_DIM)

    def _on_download_error(self, message, hint=True):
        self._finish_download()
        if not hint:
            self.download_status.configure(text=message, text_color=COLOR_ERROR)
            return
        self.download_status.configure(
            text=(
                f"Download failed: {message}\n\nIf the link works in your browser, the built-in "
                f"downloader (yt-dlp {yt_dlp.version.__version__}) may be outdated, since sites "
                f"change often. To update, {update_hint()}."
            ),
            text_color=COLOR_ERROR,
        )
        if getattr(sys, "frozen", False):
            self.download_update_button.pack(anchor="w", padx=8, pady=(0, 8), after=self.download_status)

    def _on_download_done(self, path, title, source=None):
        self._finish_download()
        for entry in (self.download_url_entry, self.download_start_entry, self.download_end_entry):
            entry.delete(0, "end")
        existing = self._find_sound(path)
        if existing is not None:
            self.download_status.configure(
                text=f"Already on your board as '{existing['name']}'.", text_color=COLOR_ORANGE
            )
            return
        self.download_status.configure(text=f"Saved: {os.path.basename(path)}", text_color=COLOR_ORANGE)
        # Keep where it came from and how it was trimmed, so the same clip
        # can be rebuilt from a shared board file.
        self._add_sound_entry(title, os.path.basename(path), source)
