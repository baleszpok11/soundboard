# Soundboard

Open-source cross-platform (Windows, macOS, Linux) soundboard in Python.
Continuously mixes microphone input with triggered sound clips and sends
the result to a chosen output device (virtual audio cable) so voice and
sounds are picked up together as one microphone in Discord/games.
A "Hear soundboard" toggle mirrors clips (not the mic) to the system
default output, so you can hear them yourself without affecting what
others hear. Tabs: Soundboard, Download (yt-dlp to MP3), Sound Editor
(trim + bass).

## Stack
- Python, CustomTkinter (GUI, orange/black theme, CTkTabview)
- sounddevice (input/output streams) + soundfile (decoding) + numpy
  (real-time mixing of mic input and sound clips, simple linear-interp
  resampling to 48kHz; channel count negotiated per device, max 2)
- scipy (lfilter for the RBJ low-shelf bass filter)
- yt-dlp + imageio-ffmpeg (downloader; bundled ffmpeg, no system install)
- pynput (global hotkeys, cross-platform, format like `<ctrl>+<alt>+1`)
- Mixing: mic gain + soundboard gain + per-sound gain, then a block-based
  peak limiter (_Limiter) per output
- Windows: device lists filtered to one host API (WASAPI with
  auto_convert, falling back to MME; choice saved as config "host_api")
- Errors: Tk callback errors and startup failures show a dialog and append
  to soundboard_error.log; config writes are atomic
- JSON file for config (no database); devices stored by name, sounds by
  filename relative to Sounds/
- PyInstaller (standalone executables), built via GitHub Actions CI; needs
  --collect-data customtkinter --collect-all yt_dlp --collect-all
  imageio_ffmpeg, plus NSMicrophoneUsageDescription on macOS
- macOS dev: use Homebrew python@3.12 + python-tk@3.12 (Apple's CLT
  Python ships Tk 8.5, which renders blank windows)

## Constraints
- Everything free and open-source. No paywalled dependencies or services.
- Keep code changes minimal and focused on the requested feature.
- No emojis in code or comments.
- Keep explanations brief.

## Files
- soundboard.py — main app
- assets/icon.svg, icon.png, icon.ico, icon.icns — app icon (window icon +
  PyInstaller build icon); source is icon.svg, others are rendered from it
- requirements.txt — pip dependencies
- build-requirements.txt — pip dependencies for building executables (pyinstaller)
- README.md — setup and usage instructions
- soundboard_config.json — generated at runtime, holds device + sound/hotkey mappings
- Sounds/ — generated at runtime, holds all sound files (downloads, edits, imports)
- Runtime data location: next to soundboard.py from source, next to the
  executable in Windows/Linux builds, ~/Documents/Soundboard in the macOS app
- .github/workflows/build.yml — CI matrix build of Windows/macOS/Linux executables
