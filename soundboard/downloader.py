"""yt-dlp helpers: version age, time parsing and the trimming post-processor."""

import contextlib
import datetime
import glob
import os
import sys
import time

import imageio_ffmpeg
import yt_dlp
import yt_dlp.postprocessor
import yt_dlp.version

from .config import SOUNDS_DIR, ensure_sounds_dir

RELEASES_URL = "https://github.com/baleszpok11/soundboard/releases/latest"
DOWNLOADER_STALE_DAYS = 60  # sites change often; older yt-dlp versions start failing


def downloader_age_days():
    """Days since the bundled yt-dlp was released (its version is a date),
    or None if the version can't be parsed."""
    try:
        released = datetime.datetime.strptime(yt_dlp.version.__version__[:10], "%Y.%m.%d").date()
    except ValueError:
        return None
    return (datetime.date.today() - released).days


def update_hint():
    if getattr(sys, "frozen", False):
        return "get the latest Soundboard release"
    return "run: pip install -U yt-dlp"


def parse_time(text):
    """Seconds from "90", "1:30", "1:02:03" or "1m30s"; None if empty.
    Raises ValueError for anything else."""
    text = text.strip()
    if not text:
        return None
    seconds = yt_dlp.utils.parse_duration(text)
    if seconds is None or seconds < 0:
        raise ValueError(f"Can't read the time '{text}'. Use m:ss, h:mm:ss or seconds.")
    return float(seconds)


def source_for(url, start, end):
    """Where a clip came from, kept on the board entry so it can be shared
    as a link and rebuilt. Times are only recorded when the clip was
    actually trimmed."""
    source = {"url": url}
    if start is not None:
        source["start"] = start
    if end is not None:
        source["end"] = end
    return source


def format_seconds(seconds):
    """Filename-safe time label like 1m05s."""
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m{secs:04.1f}s".replace(".0s", "s")


class TrimAudioPP(yt_dlp.postprocessor.FFmpegPostProcessor):
    """Cuts the extracted MP3 to [start, end] after conversion."""

    def __init__(self, downloader, start, end):
        super().__init__(downloader)
        self._start = start
        self._end = end

    def run(self, info):
        path = info["filepath"]
        opts = []
        if self._start:
            opts += ["-ss", str(self._start)]
        if self._end is not None:
            opts += ["-to", str(self._end)]
        temp = yt_dlp.utils.prepend_extension(path, "temp")
        self.real_run_ffmpeg([(path, [])], [(temp, opts + ["-c:a", "libmp3lame", "-b:a", "192k"])])
        os.replace(temp, path)
        return [], info


class ClipUnavailable(Exception):
    """The clip can't be fetched at all - a live stream, or a start time
    past the end. Distinct from a download that simply failed, because
    retrying or updating yt-dlp won't help."""


def fetch_clip(url, start=None, end=None, on_progress=None, cancel=None):
    """Download one clip into Sounds/ as MP3, trimmed to [start, end].

    Returns (path, title). Runs on the caller's thread and reports
    through on_progress(fraction, text) - fraction is None when the total
    size is unknown. Raises ClipUnavailable when the clip can't be
    fetched, yt_dlp.utils.DownloadCancelled once `cancel` is set, and
    whatever yt-dlp raised otherwise.
    """
    partial_files = set()
    last_update = [0.0]

    def report(fraction, text):
        if on_progress is None:
            return
        now = time.monotonic()
        # Progress fires per chunk; mid-download updates are throttled so
        # the caller isn't flooded.
        if fraction is not None and 0 < fraction < 1 and now - last_update[0] < 0.1:
            return
        last_update[0] = now
        on_progress(fraction, text)

    def cancelled():
        return cancel is not None and cancel.is_set()

    def on_download(d):
        if d["status"] == "downloading":
            partial_files.add(d["tmpfilename"])
        elif d["status"] == "finished" and partial_files:
            partial_files.add(d["filename"])
        if cancelled():
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
        if cancelled():
            # Only files this download created are removed.
            if partial_files:
                partial_files.add(d["info_dict"]["filepath"])
            raise yt_dlp.utils.DownloadCancelled()
        report(1.0, "Trimming..." if d["postprocessor"] == "TrimAudio" else "Converting to MP3...")

    ensure_sounds_dir()
    # The id keeps different videos with the same title apart; yt-dlp would
    # otherwise reuse the existing file. A time range gets its own name.
    suffix = ""
    if start is not None or end is not None:
        suffix = f" {format_seconds(start or 0)}-{format_seconds(end) if end is not None else 'end'}"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(SOUNDS_DIR, "%(title).100s [%(id)s]" + suffix + ".%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
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
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if suffix:
                ydl.add_post_processor(TrimAudioPP(ydl, start, end), when="post_process")
            info = ydl.extract_info(url, download=False, process=False)
            if info.get("is_live") or info.get("live_status") == "is_live":
                raise ClipUnavailable("Live streams can't be downloaded.")
            if start and info.get("duration") and start >= info["duration"]:
                raise ClipUnavailable("The start time is past the end of the video.")
            info = ydl.process_ie_result(info, download=True)
            base = ydl.prepare_filename(info)
            path = os.path.splitext(base)[0] + ".mp3"
            title = info.get("title") or os.path.splitext(os.path.basename(path))[0]
    except Exception:
        if cancelled():
            _remove_partials(partial_files)
        raise
    return path, title


def _remove_partials(paths):
    for path in paths:
        for leftover in [path, path.removesuffix(".part") + ".ytdl",
                         *glob.glob(glob.escape(path) + "-Frag*")]:
            with contextlib.suppress(OSError):
                os.remove(leftover)
