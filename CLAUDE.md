# Soundboard

Open-source cross-platform (Windows, macOS, Linux) soundboard in Python.
Continuously mixes microphone input with triggered sound clips and sends
the result to a chosen output device (virtual audio cable) so voice and
sounds are picked up together as one microphone in Discord/games.
Optionally mirrors sound clips (not the mic) to a separate local monitor
device with its own mute toggle, so you can hear clips yourself without
affecting what others hear.

## Stack
- Python, CustomTkinter (GUI, orange/black theme)
- sounddevice (input/output streams) + soundfile (decoding) + numpy
  (real-time mixing of mic input and sound clips, simple linear-interp
  resampling to a fixed 48kHz/stereo pipeline)
- pynput (global hotkeys, cross-platform, format like `<ctrl>+<alt>+1`)
- JSON file for config (no database)
- PyInstaller (standalone executables), built via GitHub Actions CI

## Constraints
- Everything free and open-source. No paywalled dependencies or services.
- Keep code changes minimal and focused on the requested feature.
- No emojis in code or comments.
- Keep explanations brief.

## Files
- soundboard.py — main app
- requirements.txt — pip dependencies
- build-requirements.txt — pip dependencies for building executables (pyinstaller)
- README.md — setup and usage instructions
- soundboard_config.json — generated at runtime, holds device + sound/hotkey mappings
- .github/workflows/build.yml — CI matrix build of Windows/macOS/Linux executables
