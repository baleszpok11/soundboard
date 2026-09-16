# Soundboard

Open-source Windows soundboard. Plays audio files to a chosen output device
(e.g. a virtual audio cable) so sounds can be picked up as a microphone in
Discord/games.

## Setup

1. Install a virtual audio cable (e.g. VB-CABLE, free) so Windows exposes a
   device you can select both here and as your microphone in Discord/games.
2. Install Python 3.
3. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

4. Run:

   ```
   python soundboard.py
   ```

## Usage

- Pick the output device from the dropdown (your virtual cable's input).
- "Add sound" to pick an audio file (wav, flac, ogg, mp3).
- "Hotkey" to assign a global hotkey (e.g. `ctrl+alt+1`) that plays it from
  anywhere, even while the app is unfocused.
- "Play" to trigger a sound manually.
- Settings are saved automatically to `soundboard_config.json`.

## Notes

- Global hotkeys (via the `keyboard` library) require running as
  administrator on Windows.
- In Discord, set your input device to the virtual cable's output side so
  played sounds come through as your mic.
