# Soundboard

Cross-platform Python soundboard that mixes the mic with sound clips into a
virtual audio cable.

## Layout
- `main.py` - entry point only (startup, error dialogs), at the repo root
- `soundboard/` - everything else, imported with relative imports
  - `app.py` - the `Soundboard` window, built from mixins: `devices.py`,
    `mic_hotkeys.py`, `sound_list.py`, `download_tab.py`, `editor_tab.py`
  - Support modules: `audio_engine.py`, `dsp.py` (offline clip processing
    for the editor: filters, phase vocoder), `hotkeys.py` (listener and
    platform quirks), `dialogs.py`, `downloader.py` (yt-dlp), `config.py`,
    `theme.py`, `tray.py`, `autostart.py`, `bug_report.py`
- Config, `Sounds/` and `assets/` live at the repo root, not in the
  package: `config.py` anchors them one level up from itself

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
- Editor DSP lives in dsp.py, not audio_engine.py: it works on a whole
  clip and can take a second, while the engine has to fill a block
  before the sound card asks again. Pitch and speed are separate there
  (phase vocoder + inverse resample); the editor's Tape toggle bypasses
  both and uses one plain resample, which sounds better than a round
  trip through the vocoder
- Editor effects are declared as data in EFFECT_SLIDERS / EFFECT_TOGGLES
  and built in a loop, so a new one needs a row in the table and a call
  in _apply_effects; reset, undo and enable/disable all walk the tables.
  The editor's controls sit in a CTkScrollableFrame with the buttons
  packed side="bottom" first - pack them after and the scrolling frame
  takes the whole cavity and leaves them nothing
- Errors: Tk callback errors and startup failures show a dialog and append
  to soundboard_error.log; config writes are atomic
- Sharing: board_file.py reads/writes .sbboard files (links, not audio);
  only sounds with a "source" can travel. downloader.fetch_clip() is the
  one download path, used by both the Download tab and import
- Bug reports: the dialog offers "Report bug"; reports go to the relay in
  relay/ (REPORT_URL in bug_report.py), falling back to a prefilled issue
  URL. Never put a GitHub token in the app - it can be extracted
- JSON file for config (no database); devices stored by name, sounds by
  filename relative to Sounds/
- Profiles: config["profiles"] is a list of {name, sounds}, addressed by
  name through config["active_profile"]; use active_profile()/
  profile_sounds() or the Soundboard.profile/.sounds properties rather
  than reaching into the list. Only the active profile's hotkeys are
  registered. A pre-profile config migrates into one named Default
- PyInstaller (standalone executables; onefile on Windows/Linux, onedir
  .app on macOS), built via GitHub Actions CI; needs
  --collect-data customtkinter --collect-all yt_dlp --collect-all
  imageio_ffmpeg, plus NSMicrophoneUsageDescription on macOS. pynput picks
  its backend with a runtime import, so each build names its own
  (--hidden-import pynput.keyboard._xorg/_win32/_darwin and the matching
  pynput.mouse one); --collect-all pynput is not enough, it copies the
  backend as a data file that never gets analysed, so Xlib is left out
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
  on merge.
- Releases: bump `APP_VERSION` in config.py, then tag master (`vX.Y.Z`)
  after merging; CI builds and publishes.

## Merging without asking
Merge your own PR without waiting, but only when every one of these holds:
- It does what an issue asked for, or fixes a bug inside that scope.
- Its behaviour was checked by running it, not by imports and pyflakes
  alone: exercise the real code path, and prove a fix fails without it.
- CI is green where CI runs at all (build.yml only runs on tags and
  manual dispatch, so most PRs have no checks - absence of red is not
  evidence).
- A mistake can be undone by a later PR, without anyone losing data.

Stop and ask when any of these is true, however small the diff looks:
- It changes the config schema, or how existing config is read. Getting
  this wrong empties someone's board with no error.
- It touches packaging, the build workflow, or what a release publishes.
  Those only misbehave at release time, long after merging.
- It changes a default, removes behaviour, or alters what the app does to
  files outside `Sounds/`.
- It touches the relay, or anything near a token.
- It couldn't be verified here. Tray, autostart and real audio devices
  need Windows or macOS; say so rather than merging on a guess.
- You had to choose between designs and weren't sure. Merging is not how
  to settle that.

Tagging and publishing releases stays the user's call, always.

After a merge, say what went in and which of the rules above let it
through.
