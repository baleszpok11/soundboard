# Soundboard

Open-source Windows soundboard in Python. Plays audio files to a chosen
output device (virtual audio cable) so sounds can be picked up as a
microphone in Discord/games.

## Stack
- Python, tkinter (GUI)
- sounddevice + soundfile (playback)
- keyboard (global hotkeys)
- JSON file for config (no database)

## Constraints
- Everything free and open-source. No paywalled dependencies or services.
- Keep code changes minimal and focused on the requested feature.
- No emojis in code or comments.
- Keep explanations brief.

## Files
- soundboard.py — main app
- requirements.txt — pip dependencies
- README.md — setup and usage instructions
- soundboard_config.json — generated at runtime, holds device + sound/hotkey mappings
