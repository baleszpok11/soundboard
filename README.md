# Soundboard

Open-source, cross-platform (Windows, macOS, Linux) soundboard. Continuously
mixes your microphone with triggered sound clips and sends the combined
audio to a chosen output device — typically a virtual audio cable — so
your voice and your sounds reach Discord, games, or any other voice app
together, from one virtual microphone.

## Features

- Assign any audio file (wav, flac, ogg, mp3) to a button
- Play sounds on click or via a global hotkey, even while unfocused
- Enable/disable each sound's hotkey independently with a checkbox,
  without removing it from the board
- Mixes your real microphone with soundboard clips in real time, so
  people hear both at once — no separate mixer app needed
- Choose which microphone (input) and which virtual cable (output) are used
- Settings persist automatically between runs
- Runs from source (Python) or as a standalone executable — no Python
  required on the machine you run it on
- 100% free and open-source, no paywalled dependencies or services

## Download

Grab a pre-built executable from the [Releases](../../releases) page —
no Python or setup required, just download and run. Windows, macOS, and
Linux builds are published automatically for each tagged version (see
[Building a standalone executable](#building-a-standalone-executable)).

## Running from source

Requirements: Python 3.9+.

1. Set up a virtual audio device for your OS (see below).
2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Run:

   ```
   python soundboard.py
   ```

### Virtual audio device by platform

The app mixes your microphone and your sound clips itself, so you only
need a virtual cable to carry that combined audio into Discord/games —
no separate OS-level mixer or loopback routing is required.

**Windows** — install [VB-CABLE](https://vb-audio.com/Cable/) (free) or the
open-source [VirtualAudioCable by frgnca](https://github.com/frgnca/VirtualAudioCable).

**macOS** — install [BlackHole](https://github.com/ExistentialAudio/BlackHole)
(free, open-source).

**Linux** — no install needed; PulseAudio/PipeWire can create a null sink:

```
pactl load-module module-null-sink sink_name=soundboard sink_properties=device.description=Soundboard
```

(use `pavucontrol` for a GUI alternative to the CLI command above.)

In all three cases: pick your real microphone as the **input** device and
the virtual cable as the **output** device in Soundboard, then select the
virtual cable as your microphone in Discord/games.

## Usage

- **Microphone (input):** your real microphone — this is what gets mixed
  with sound clips and sent onward.
- **Virtual mic output:** the virtual cable that Discord/games should use
  as their microphone input. Pick `(none)` to disable a side if you don't
  need it (e.g. no mic passthrough).
- "Add sound" to pick an audio file (wav, flac, ogg, mp3).
- The checkbox next to each sound toggles it on/off — unchecked sounds
  keep their hotkey assignment but won't respond to it until re-enabled.
- "Hotkey" to assign a global hotkey (e.g. `<ctrl>+<alt>+1`) that plays it
  from anywhere, even while the app is unfocused.
- "Play" to trigger a sound manually (works even if it's unchecked).
- "Remove" to delete a sound from the board.
- A sound listed in red with "(file missing)" points at a file that's
  been moved or deleted since it was added.
- Settings are saved automatically to `soundboard_config.json`, next to
  the script (or next to the executable, when run as a build).

## Building a standalone executable

Install build dependencies and run PyInstaller:

```
pip install -r build-requirements.txt
pyinstaller --onefile --windowed soundboard.py
```

The executable is written to `dist/`. Build on each target OS to get a
native executable for it (PyInstaller does not cross-compile).

### Publishing a release

Pushing a version tag builds Windows, macOS, and Linux executables in CI
and publishes them as downloadable zips on the repo's [Releases](../../releases)
page (no need to check out the code or run Python):

```
git tag v0.1.0
git push origin v0.1.0
```

See `.github/workflows/build.yml`. Note: the repo must be public (or the
downloader needs read access) for others to reach the Releases page.

## Notes

- Audio is mixed at a fixed 48000 Hz, stereo. If a device doesn't support
  that, opening it will show an error — pick a different device or check
  its properties in your OS's sound settings.
- Global hotkeys are handled via `pynput`. On Linux with Wayland, global
  hotkey capture may not work depending on your compositor (X11 works).
  On macOS, grant Accessibility permissions to your terminal/app when
  prompted for hotkeys to register.
- Sound files are referenced by their absolute path in
  `soundboard_config.json`; moving or renaming a sound file after adding
  it will show it as missing in the list.

## License

[MIT](LICENSE)
