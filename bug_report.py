"""Building and sending bug reports.

Nothing leaves the app until the user presses Send: the report window
shows exactly what would go. Creating an issue needs a GitHub token, and
anything shipped inside the app can be extracted, so the token lives in a
small relay (see relay/) instead. With no relay configured, reports fall
back to opening a prefilled issue in the browser.
"""

import getpass
import hashlib
import json
import os
import platform
import sys
import urllib.parse
import urllib.request

from config import APP_DIR, APP_VERSION

REPORT_URL = ""  # the relay's /report endpoint; blank disables sending
ISSUE_URL = "https://github.com/baleszpok11/soundboard/issues/new"
LOG_LIMIT = 16 * 1024
URL_BODY_LIMIT = 6000  # a prefilled issue URL has to stay under about 8 KB
SEND_TIMEOUT_S = 10
TRACEBACK_MARKER = "Traceback (most recent call last):"


def sending_available():
    return bool(REPORT_URL)


def last_error():
    """The most recent traceback from the error log, scrubbed and capped,
    or "" when there is nothing to report."""
    path = os.path.join(APP_DIR, "soundboard_error.log")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    if TRACEBACK_MARKER in text:
        text = TRACEBACK_MARKER + text.rsplit(TRACEBACK_MARKER, 1)[1]
    text = text.strip()
    if len(text) > LOG_LIMIT:
        text = "...\n" + text[-LOG_LIMIT:]
    return scrub(text)


def scrub(text):
    """Drop the parts of a path that name the person running the app."""
    if not text:
        return text
    home = os.path.expanduser("~")
    for path in (os.path.realpath(home), home):
        text = text.replace(path, "~")
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if user:
        text = text.replace(user, "<user>")
    return text


def environment(config=None, host_api=None):
    """What was running, with no file names in it."""
    lines = [
        f"- Soundboard: {APP_VERSION}",
        f"- OS: {platform.platform()}",
        f"- Python: {platform.python_version()}",
        f"- Build: {'executable' if getattr(sys, 'frozen', False) else 'from source'}",
    ]
    if host_api:
        lines.append(f"- Host API: {host_api}")
    if config is not None:
        lines.append(f"- Input device: {config.get('input_device') or 'none'}")
        lines.append(f"- Output device: {config.get('output_device') or 'none'}")
        lines.append(f"- Sounds on board: {len(config.get('sounds', []))}")
    return "\n".join(scrub(line) for line in lines)


def error_hash(error_text):
    """Short hash of a traceback, so the relay can spot repeats. Line
    numbers and addresses are kept out of it."""
    if not error_text:
        return ""
    lines = [line.strip() for line in error_text.splitlines() if "line " not in line]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:8]


def build(description, contact, error_text, env):
    parts = [
        "**What I was doing**",
        description.strip() or "(not described)",
        "",
        f"**Contact**: {contact.strip() or 'not given'}",
        "",
        "**Environment**",
        env,
    ]
    if error_text:
        parts += ["", "**Error**", "```", error_text, "```"]
    return "\n".join(parts)


def title_for(description, error_text):
    first = (description.strip().splitlines() or [""])[0][:70] or "Bug report"
    digest = error_hash(error_text)
    return f"{first} [{digest}]" if digest else first


def send(title, body, error_text):
    """POST the report to the relay. Raises on failure; returns the URL of
    the issue it created."""
    payload = json.dumps({
        "title": title,
        "body": body,
        "error_hash": error_hash(error_text),
        "app_version": APP_VERSION,
    }).encode("utf-8")
    request = urllib.request.Request(
        REPORT_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Soundboard-Client": APP_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_S) as response:
        answer = json.loads(response.read().decode("utf-8"))
    return answer.get("url", "")


def browser_url(title, body):
    """A prefilled "new issue" page, for when there is no relay. The body
    is trimmed so the URL stays within what browsers accept."""
    if len(body) > URL_BODY_LIMIT:
        body = body[:URL_BODY_LIMIT] + "\n\n(report trimmed; the full text is on your clipboard)"
    query = urllib.parse.urlencode({"title": title, "body": body, "labels": "user-report"})
    return f"{ISSUE_URL}?{query}"
