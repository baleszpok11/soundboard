# Soundboard

Open-source cross-platform (Windows, macOS, Linux) soundboard in Python.
Plays audio files to a chosen output device (virtual audio cable) so
sounds can be picked up as a microphone in Discord/games.

## Stack
- Python, tkinter (GUI)
- sounddevice + soundfile (playback)
- pynput (global hotkeys, cross-platform)
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
