# Soundboard

Open-source cross-platform (Windows, macOS, Linux) soundboard. Plays audio
files to a chosen output device (e.g. a virtual audio cable) so sounds can
be picked up as a microphone in Discord/games.

## Setup

1. Set up a virtual audio device for your OS (see below) so it can be
   selected both here and as your microphone in Discord/games.
2. Install Python 3.
3. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

4. Run:

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
- Settings are saved automatically to `soundboard_config.json`.

## Building a standalone executable

Install build dependencies and run PyInstaller:

```
pip install -r build-requirements.txt
pyinstaller --onefile --windowed soundboard.py
```

The executable is written to `dist/`. Build on each target OS to get a
native executable for it (PyInstaller does not cross-compile). Pre-built
Windows, macOS, and Linux executables are also produced automatically by
CI on tagged releases — see `.github/workflows/build.yml`.

## Notes

- Global hotkeys are handled via `pynput`. On Linux with Wayland, global
  hotkey capture may not work depending on your compositor (X11 works).
  On macOS, grant Accessibility permissions to your terminal/app when
  prompted for hotkeys to register.
- In Discord, set your input device to the virtual cable's output/monitor
  side so played sounds come through as your mic.
