# Security Policy

## Supported versions

Soundboard ships as a rolling release: only the
[latest release](../../releases/latest) is supported. The app checks for
updates on startup and can install them itself on Windows and Linux, so
please reproduce anything you find on the newest version before
reporting it.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private reporting instead:
[Report a vulnerability](../../security/advisories/new). It is visible
only to the maintainer until a fix is released.

Include what you would in any bug report - OS, app version, and the steps
you took - plus what an attacker would gain. You should get a first reply
within a week. If a fix is needed, it goes out as a normal tagged
release, and the advisory is published once it is available.

Please do not test against anyone else's machine, and do not include
someone else's data in a report.

## What is in scope

Soundboard is a desktop app with no accounts and no server-side state, so
the interesting parts are the places it takes in something from outside:

- **Board files** (`.sbboard`). These carry links, not audio - opening
  one makes the app download clips. Anything that lets a board file write
  outside `Sounds/`, run a command, or fetch something other than the
  audio it claims is in scope.
- **Downloads.** Clip fetching goes through yt-dlp and ffmpeg. Report
  path traversal, or a filename that escapes the sounds folder, here;
  report bugs in yt-dlp or ffmpeg themselves to those projects.
- **Bug reports.** A report is assembled locally and shown to you in full
  before anything is sent. Anything that puts data in a report that the
  preview does not show is in scope, as is anything in the relay in
  [relay/](../relay).
- **Updates.** Releases are downloaded over HTTPS from the GitHub
  releases API and replace the running executable. Anything that lets a
  different binary take that path is in scope.
- **Config handling**, including the migration that copies a board from
  beside the executable into the per-user data folder.

## What is not in scope

- Global hotkeys are captured process-wide by design; that is the feature.
  Likewise, the app mixes your microphone into an output device you pick.
- macOS builds are ad-hoc signed and not notarized, and Windows builds
  are unsigned, so both show an OS warning on first run. This is a
  consequence of publishing free builds without a paid developer
  certificate, and is documented in the README rather than treated as a
  vulnerability.
- Anything requiring an attacker who already has code execution or
  filesystem access on your machine. At that point they can edit
  `soundboard_config.json` directly.
- Vulnerabilities in dependencies that are already public, unless
  Soundboard uses them in a way that makes things meaningfully worse.
