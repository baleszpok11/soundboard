"""yt-dlp helpers: version age, time parsing and the trimming post-processor."""

import datetime
import os
import sys

import yt_dlp
import yt_dlp.postprocessor
import yt_dlp.version

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
