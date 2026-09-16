# Soundboard

Open-source, cross-platform (Windows, macOS, Linux) soundboard. Plays
audio files to a chosen output device — typically a virtual audio cable —
so your sounds get picked up as a microphone input in Discord, games, or
any other voice app.

## Features

- Assign any audio file (wav, flac, ogg, mp3) to a button
- Play sounds on click or via a global hotkey, even while unfocused
- Choose which output device sounds are routed to
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

1. Set up a virtual audio device for your OS (see below) so it can be
   selected both here and as your microphone in Discord/games.
2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Run:

   ```
   python soundboard.py
   ```

### Virtual audio device by platform

**Windows** — install [VB-CABLE](https://vb-audio.com/Cable/) (free) or the
open-source [VirtualAudioCable by frgnca](https://github.com/frgnca/VirtualAudioCable).
Select the cable's input as the output device here, and its output as your
microphone in Discord/games. To also send your real mic through, use your
OS's "listen to this device" / an audio mixer to combine your microphone
and the soundboard into the same cable input, or use a tool like Voicemeeter.

**macOS** — install [BlackHole](https://github.com/ExistentialAudio/BlackHole)
(free, open-source). Select BlackHole as the output device here, and as
the microphone in Discord/games. To mix your real mic with the soundboard,
create a Multi-Output Device and an Aggregate Device in Audio MIDI Setup
combining your microphone and BlackHole, or use BlackHole's companion app
[Loopback](https://rogueamoeba.com/loopback/) (paid) / a free routing app.

**Linux** — no install needed; PulseAudio/PipeWire can create a null sink:

```
pactl load-module module-null-sink sink_name=soundboard sink_properties=device.description=Soundboard
```

Select "Soundboard" (monitor) as the output device here, and as the
microphone in Discord/games. To mix in your real mic, combine both sources
into the sink with:

```
pactl load-module module-loopback source=<your-mic-source> sink=soundboard
```

(list sources with `pactl list short sources`). Use `pavucontrol` for a
GUI to manage inputs/outputs instead of the CLI commands above.

## Usage

- Pick the output device from the dropdown (your virtual cable's input).
- "Add sound" to pick an audio file (wav, flac, ogg, mp3).
- "Hotkey" to assign a global hotkey (e.g. `<ctrl>+<alt>+1`) that plays it
  from anywhere, even while the app is unfocused.
- "Play" to trigger a sound manually.
- "Remove" to delete a sound from the board.
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

- Global hotkeys are handled via `pynput`. On Linux with Wayland, global
  hotkey capture may not work depending on your compositor (X11 works).
  On macOS, grant Accessibility permissions to your terminal/app when
  prompted for hotkeys to register.
- In Discord, set your input device to the virtual cable's output/monitor
  side so played sounds come through as your mic.

## License

[MIT](LICENSE)
