# Soundboard

Cross-platform Python soundboard that mixes the mic with sound clips into a
virtual audio cable.

## Layout
- `soundboard.py` - entry point only (startup, error dialogs)
- `app.py` - the `Soundboard` window, built from mixins: `devices.py`,
  `mic_hotkeys.py`, `sound_list.py`, `download_tab.py`, `editor_tab.py`
- Support modules: `audio_engine.py`, `hotkeys.py` (listener and platform
  quirks), `dialogs.py`, `downloader.py` (yt-dlp), `config.py`, `theme.py`

## Stack notes
- Windows: device lists filtered to one host API (WASAPI with
  auto_convert, falling back to MME; choice saved as config "host_api")
- Linux: sounddevice doesn't bundle PortAudio (libportaudio2); a missing
  library is caught at import and shown as a startup error. PulseAudio
  sinks aren't listed by name, so routing is done in pavucontrol
- Devices: "Refresh devices" reinitializes PortAudio (sd._terminate /
  sd._initialize) to rescan. Streams with no callbacks for 2 s count as
  lost; a lost output is auto-reconnected every 5 s for 2 min
- macOS hotkeys: pin_macos_keyboard_layout() reads the keyboard layout on
  the main thread before starting pynput (pynput reads it on its listener
  thread, which current macOS kills the process for). Missing Input
  Monitoring/Accessibility permission is detected and shown as a warning
- Errors: Tk callback errors and startup failures show a dialog and append
  to soundboard_error.log; config writes are atomic
- JSON file for config (no database); devices stored by name, sounds by
  filename relative to Sounds/
- PyInstaller (standalone executables; onefile on Windows/Linux, onedir
  .app on macOS), built via GitHub Actions CI; needs
  --collect-data customtkinter --collect-all yt_dlp --collect-all
  imageio_ffmpeg, plus NSMicrophoneUsageDescription on macOS
- macOS dev: use Homebrew python@3.12 + python-tk@3.12 (Apple's CLT
  Python ships Tk 8.5, which renders blank windows)

## Constraints
- Everything free and open-source. No paywalled dependencies or services.
- Keep code changes minimal and focused on the requested feature.
- No emojis in code or comments.
- Keep explanations brief.
- Only work on Linux-specific bugs or packaging when explicitly asked.
  Report them and move on otherwise.

## Workflow
- One branch per issue off an up-to-date master: `issue-<number>-<short-slug>`
  (non-issue work: `fix-<slug>` / `feature-<slug>`).
- Open a PR into master with `Fixes #<number>` in the body, so merging
  closes the issue. Don't close issues by hand or push to master directly.
- Squash-merge (`gh pr merge --squash`); the repo deletes the source branch
  on merge. Merge only when the user says so.
- Releases: tag master (`vX.Y.Z`) after merging; CI builds and publishes.
