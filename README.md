<img src="assets/icon.png" width="96" align="left" alt="Soundboard icon">

# Soundboard

<br clear="left">

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
- Profiles: keep separate boards for different games or groups and switch
  between them, each with its own sounds and hotkeys
- **Import / Export** tab: share a board as a small file of links, not
  audio - whoever opens it downloads their own copy of each clip
- Mixes your real microphone with soundboard clips in real time, so
  people hear both at once — no separate mixer app needed
- Choose which microphone (input) and which virtual cable (output) are used
- Level meters under both device pickers, plus a "Test" button that sends
  a tone to the output, so you can see your routing works without asking
  someone if they can hear you
- "Hear soundboard" toggle plays clips on your own speakers/headphones
  too — turning it off doesn't affect what others hear through the
  virtual cable
- Re-triggering a sound that's still playing restarts it
- Loop a sound so it keeps repeating, for music beds and ambience
- See what's playing on the board, and stop a single sound without
  stopping everything
- "Stop all" button and optional hotkey
- Volume sliders for the mic, the whole soundboard, and each sound, plus
  a limiter so loud moments get quieter instead of distorting
- **Download** tab: grab audio from YouTube, TikTok, Instagram and other
  sites (via yt-dlp) as MP3, straight into your board, with progress,
  cancel, and optional start/end times
- **Sound Editor** tab: trim a clip's start/end, boost or cut bass, set
  volume or normalize, fade in/out, change pitch and speed independently
  of each other, choose whether loud edits stay clean or clip, and pile
  on effects (treble, mid, drive, robot, echo, stutter, width,
  telephone, reverse), with a waveform view, preview and undo
- Checks for new releases and installs them for you (Windows and Linux;
  on macOS it downloads and you drag it over)
- Optionally start with the computer and keep running in the tray /
  menu bar when the window is closed, so hotkeys still work
- Follows each platform's design language: the system font, control
  density and corner radius of macOS on a Mac and of Fluent on Windows,
  in light or dark, following the desktop's own setting unless you pick
  one under **Appearance**
- **Record** button: capture a clip straight from your microphone into
  the board, without leaving the app
- Add sounds in bulk: pick several files at once, or point it at a
  folder and take every clip in it
- All sounds live in one `Sounds/` folder
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

Requirements: Python 3.9+ with a current Tk. On macOS, the Python that
ships with Apple's Command Line Tools has an outdated Tk that shows a
blank window; use Homebrew instead (`brew install python@3.12 python-tk@3.12`).

0. Press the **?** at the top right, on the tab strip's row: it opens a
   step-by-step setup guide for your system, with the same instructions
   as below and screenshots of the app. It can stay open while you
   follow along.
1. Set up a virtual audio device for your OS (see below).
2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

3. Run:

   ```
   python main.py
   ```

### Virtual audio device by platform

The app mixes your microphone and your sound clips itself, so you only
need a virtual cable to carry that combined audio into Discord/games —
no separate OS-level mixer or loopback routing is required.

**Windows** — install [VB-CABLE](https://vb-audio.com/Cable/) (free) or the
open-source [VirtualAudioCable by frgnca](https://github.com/frgnca/VirtualAudioCable).

**macOS** — install [BlackHole](https://github.com/ExistentialAudio/BlackHole)
(free, open-source).

**Windows and macOS:** pick your real microphone as the **input** device
and the virtual cable as the **output** device in Soundboard, then select
the virtual cable as your microphone in Discord/games.

**Linux** — no driver needed, but Soundboard needs the PortAudio library
(the app tells you if it's missing):

```
sudo apt install libportaudio2      # Debian/Ubuntu
sudo dnf install portaudio          # Fedora
sudo pacman -S portaudio            # Arch
```

PulseAudio/PipeWire can create the virtual cable. The first command makes
a "Soundboard" output; the second turns what's played into it into a
"Soundboard_Mic" microphone that Discord/games can select:

```
pactl load-module module-null-sink sink_name=soundboard sink_properties=device.description=Soundboard
pactl load-module module-remap-source master=soundboard.monitor source_name=soundboard_mic source_properties=device.description=Soundboard_Mic
```

These last until you log out; add them to your PulseAudio/PipeWire
startup config to keep them.

Soundboard can't list PulseAudio/PipeWire devices by name, so the routing
is done in `pavucontrol` (install it from your package manager):

1. In Soundboard, set both **Microphone** and **Virtual mic output** to
   `pulse` (or `default` if there's no `pulse`).
2. Play a sound. In pavucontrol's **Playback** tab, find Soundboard's
   stream and switch it to **Soundboard**.
3. In pavucontrol's **Recording** tab, switch Soundboard's stream to your
   real microphone.
4. In Discord/games, select **Soundboard_Mic** as your microphone.

pavucontrol remembers these choices for next time. "Hear soundboard" may
not work on Linux, because its stream can end up routed to Soundboard
too; if so, leave it off.

## Where your board is kept

A built app keeps `soundboard_config.json` and your `Sounds/` folder in
the standard per-user location:

| | |
| --- | --- |
| Windows | `%APPDATA%\Soundboard` |
| macOS | `~/Documents/Soundboard` |
| Linux | `~/.local/share/Soundboard` |

So the executable can be moved, replaced by an update, or run straight
out of a download folder without your board going with it. Running from
source, everything stays in the project folder as before.

Older builds kept both beside the executable. If you have a board there,
it is copied across the first time a newer build starts — copied, not
moved, so the old folder stays exactly as it was until you delete it
yourself.

## Usage

- **Microphone (input):** your real microphone — this is what gets mixed
  with sound clips and sent onward.
- **Virtual mic output:** the virtual cable that Discord/games should use
  as their microphone input. Pick `(none)` to disable a side if you don't
  need it (e.g. no mic passthrough).
- **Level meters:** the thin bar under each device shows its current
  level. The mic meter moves even while muted or while push-to-talk
  isn't held, but goes grey instead of orange - so a mic that isn't
  working looks different from one that's simply muted. A meter turns
  red when the signal is close to clipping.
- **Test:** plays a short tone through the virtual mic output. The output
  meter should move; anyone listening to that cable should hear it. Use
  it to confirm routing before going live.
- **Refresh devices:** rescans your audio devices, e.g. after plugging in
  a headset or installing a virtual cable, without restarting the app.
  A selected device that isn't connected is shown in red. If the virtual
  mic output disconnects while you're using it, the app tries to
  reconnect it for 2 minutes.
- **Hear soundboard:** when checked, clips also play on your system's
  default output (your speakers/headphones) so you know what's being
  triggered. Only clips are sent there, not your mic. If the virtual mic
  output is itself your default output (e.g. no virtual cable installed),
  you'll hear the main mix regardless of this setting.
- "Add sound" to pick an audio file (wav, flac, ogg, mp3); it's copied
  into the `Sounds/` folder.
- The checkbox next to each sound toggles it on/off — unchecked sounds
  keep their hotkey assignment but won't respond to it until re-enabled.
- "Hotkey" to assign a global hotkey that plays it from anywhere, even
  while the app is unfocused. Press the key combination (e.g. Ctrl+Alt+1)
  and click Save; Esc stops recording so you can type it instead (e.g.
  `<ctrl>+<alt>+1`), and Clear removes it. Letter, number and symbol keys
  need a modifier so they don't fire while you type.
- "Play" to trigger a sound manually (works even if it's unchecked). A
  thin bar under the row (or tile) shows how far a playing sound has got,
  and a "Stop" button appears next to Play while it runs. "Stop" is also
  in the right-click/More menu, which is how you stop a sound in Grid
  view.
- "Loop" in the same menu makes a sound repeat until stopped; looping
  sounds are marked "(loop)" on the board. Turning Loop off stops the
  repeat straight away rather than waiting for the current pass to end.
- **Profile:** each profile is its own board - its own sounds, hotkeys,
  order and per-sound volumes. Your devices, mic settings, push-to-talk
  and the mic/soundboard volumes are shared, so switching profiles never
  changes your audio routing. **Manage** creates, renames, duplicates or
  deletes one; the last profile can't be deleted, and deleting one never
  deletes the sound files. Because hotkeys are per profile, the same key
  can play something different in each. Switching stops whatever the old
  profile was playing.
- Type in **Search sounds...** to filter the list. **List/Grid** switches
  between rows and big buttons (click to play; right-click for options).
- Drag a row by its `::` handle to reorder it. **More** has Rename, Move
  up/down and Remove; double-clicking a name also renames it.
- "Remove" (under More) takes a sound off the board and stops it if it's playing. For
  files in `Sounds/`, it asks whether to delete the file too.
- Adding or downloading something that's already on the board doesn't
  create a second entry.
- A sound listed in red with "(file missing)" points at a file that's
  been moved or deleted since it was added.
- Playing a sound that's already playing restarts it from the beginning.
- **Mic volume / Soundboard volume:** 0–200%. Each sound also has its own
  volume slider. A limiter keeps the combined output from distorting.
- New sounds start at 20%, because clips are usually mastered much louder
  than a voice and full volume is how people deafen a call the first time
  they press a button. Turn a sound up once and it stays there. Sounds
  already on your board are not touched. In grid view the slider is under
  "Volume..." in a tile's right-click menu.
- **Mute mic** silences your voice while sounds keep playing; "Set mute
  hotkey" toggles it from anywhere. **Push to talk** only sends your voice
  while the talk key is held (set it with "Set talk key"); sounds always
  play. The label under them shows whether your mic is live.
- **Stop all** silences every playing sound. "Set stop hotkey" assigns a
  global hotkey for it. The app won't let two actions share a hotkey.
- Games that require their own push-to-talk key only send sounds while
  you hold that key; use the game's open-mic/voice-activation mode, or
  hold the key while a sound plays.
- If something goes wrong, the app shows an error and writes details to
  `soundboard_error.log` next to your settings. If the settings file is
  damaged, it's kept as `soundboard_config.json.broken` and the app starts
  with defaults.
- **Others hear their own voices back:** PC audio is getting into your
  virtual mic. Make sure your system's default playback device and
  Discord's **Output Device** are your speakers/headphones, not the
  virtual cable (`CABLE Input`, BlackHole), and that **Microphone** in
  Soundboard is your real mic, not `CABLE Output` or `Stereo Mix`. The
  app shows a red warning when it detects either problem.
- **Download tab:** paste a video/clip URL and click "Download as MP3".
  The audio is saved into `Sounds/` (the file name includes the video's
  ID) and added to the board. To keep only part of a long video, fill in
  Start and/or End (e.g. `1:30`, `1:02:03` or `90`); the time range is
  added to the file name. A progress bar shows the download, and Cancel
  stops it and removes the partial file (conversion to MP3 can't be
  cancelled). Live streams aren't supported. Only download
  content you have the right to use; downloading may be against the
  source site's terms of service. The tab shows the built-in downloader's
  version. Sites change often, so if downloads start failing, get the
  latest release (or run `pip install -U yt-dlp` when running from source).
- **Sound Editor tab:** pick a sound (or browse for any file), drag the
  Start/End sliders to trim it (at least 0.05 s is kept), adjust Bass
  (-36 to +36 dB), click Preview to listen (Stop ends the preview), then
  "Save as new sound" to write a new WAV into `Sounds/` and add it to the
  board. The fades can be as long as half the selection, so a long clip
  can fade for as long as you like.

  **Pitch** shifts the clip in semitones, up to two octaves either way,
  and **Speed** makes it faster or slower, from a quarter speed to four
  times. By default they are independent: a clip can be a fifth higher
  and still last exactly as long, or play at half speed in the original
  key. Tick **Tape** to tie them together the way speeding up or slowing
  down a tape does, which is what the pitch slider used to do on its own
  (+12 st is then twice as fast and half as long).

  Moving pitch and speed apart is done with a phase vocoder, which is
  very good on voices and most soundboard material but smears sharp
  drum hits at extreme settings. Tape mode doesn't use it at all, so
  it stays the cleanest option when you want both to move together.

  Under **Effects**:

  | | |
  | --- | --- |
  | **Treble** / **Mid** | the rest of the tone controls: a high shelf at 4 kHz and a peak at 1 kHz, both -36 to +36 dB |
  | **Drive** | overdrive - pushes the clip into a soft saturation, loud and dirty |
  | **Robot** | ring modulation, a metallic rasp that gets harsher the higher you take it |
  | **Echo** | repeats a quarter of a second apart, each quieter than the last; the clip gets longer so the tail can die away |
  | **Stutter** | holds each slice and plays it twice, so the clip glitches without changing length |
  | **Width** | mono at 0, untouched at 1, exaggerated stereo above that |
  | **Telephone** | keeps only 300-3000 Hz, so it sounds like a phone line |
  | **Reverse** | plays it backwards |

  They stack in a fixed order: tone, then drive and robot, then width and
  stutter, with the echo last so it repeats the finished clip. Everything
  is previewed before you save, and the source file is never touched.

  **Too loud** decides what happens when the settings push the clip past
  full volume, which a big bass or volume boost easily does:

  | | |
  | --- | --- |
  | **Keep clean** | turns the whole clip down just enough to fit, so nothing distorts (the default) |
  | **Let it clip** | shears the peaks off at full scale: loud and nasty |
  | **Soft clip** | bends the peaks over instead, the way a distortion pedal does: loud and dirty, but still musical |

  Clean is the safe one, but it also gives away the loudness: the same
  bass boost comes out roughly three times quieter than letting it clip.
  The original file is never changed, whichever you pick.

### Sharing a board

The **Import / Export** tab shares a board without moving any audio. An
export lists each sound's name, hotkey, volume and the link it was
downloaded from; whoever imports it downloads their own copy from those
links. The file is a few kilobytes, and nothing is redistributed.

- **Export:** choose the active profile or all of them. The summary says
  how many sounds can be shared and names the ones that can't - anything
  added from disk, or saved by the Sound Editor, has no link to rebuild
  it from and is left out.
- **Import:** pick a file and the links it contains are listed *before*
  anything is downloaded, so you can see what a file someone sent you
  would fetch. Choose whether it lands in a new profile or the one
  you're on, then Import. Each clip is downloaded in turn and can be
  cancelled; if a video has been taken down, that one is reported and
  the rest still import. A hotkey already used in the destination
  profile is dropped rather than shadowing the sound that has it.

### Where your data is stored

`soundboard_config.json` and the `Sounds/` folder live:
- next to `main.py` when running from source
- next to the executable on Windows/Linux builds
- in `~/Documents/Soundboard` for the macOS app (macOS asks for
  permission to use Documents on first launch)

## Building a standalone executable

Install build dependencies and run PyInstaller:

```
pip install -r build-requirements.txt

COLLECT="--collect-data customtkinter --collect-all yt_dlp --collect-all imageio_ffmpeg --collect-all pystray"

# Windows
pyinstaller --onefile --windowed --icon assets/icon.ico --add-data "assets/icon.png;assets" $COLLECT --name soundboard main.py

# macOS
pyinstaller --onedir --windowed --icon assets/icon.icns --add-data "assets/icon.png:assets" $COLLECT --name soundboard main.py
plutil -insert NSMicrophoneUsageDescription -string "Soundboard uses your microphone." dist/soundboard.app/Contents/Info.plist
codesign --force --deep -s - dist/soundboard.app

# Linux
pyinstaller --onefile --windowed --add-data "assets/icon.png:assets" $COLLECT --name soundboard main.py
```

The executable is written to `dist/`. On macOS, build with `--onedir`:
a single-file `.app` doesn't open reliably and can't be allowed under
Input Monitoring. Build on each target OS to get a
native executable for it (PyInstaller does not cross-compile). The
`--collect-*` flags bundle CustomTkinter's theme files, yt-dlp's site
extractors and a portable ffmpeg. On macOS, the `plutil` line is required
for microphone access, and the app has to be re-signed after editing it.

The macOS build is not notarized, so on first launch right-click the app
and choose Open.

### Publishing a release

Pushing a version tag builds Windows, macOS, and Linux executables in CI
and publishes them as downloadable zips on the repo's [Releases](../../releases)
page (no need to check out the code or run Python):

```
git tag v0.1.0
git push origin v0.1.0
```

See `.github/workflows/build.yml`. Each release bundles the newest
yt-dlp and notes its version; `.github/workflows/check-ytdlp.yml` runs
weekly and opens an issue when a newer yt-dlp is out, as a reminder to
publish a release. Note: the repo must be public (or the
downloader needs read access) for others to reach the Releases page.

## Reporting a bug

"Report a bug" on the Soundboard tab, and the button on any error dialog,
open a window that shows the whole report before anything happens.
Nothing leaves your computer until you press Send.

A report contains what you type, an optional contact, the most recent
traceback from `soundboard_error.log`, the app version, your OS, the host
API, the names of the selected input and output devices, and how many
sounds are on your board. Folder names are replaced with `~` and no sound
file names are included.

Send needs the relay described in [relay/](relay/). Until one is deployed
it opens GitHub's prefilled "new issue" page in your browser (which needs
a GitHub account) and copies the report to your clipboard. Copy report
does the same without sending anything anywhere.

## Notes

- Audio is mixed at a fixed 48000 Hz. Each device uses its own channel
  count (mono or stereo) and audio is converted between them. If a device
  doesn't support 48000 Hz, opening it will show an error — pick a
  different device or check its properties in your OS's sound settings.
- On Windows, devices are listed once each, using WASAPI (full names,
  lower latency). If a device won't open with WASAPI, the app switches to
  MME (the older Windows audio system) and remembers that choice.
- On first run, the output defaults to a detected virtual cable
  (VB-CABLE, BlackHole) if one is installed.
- Devices are remembered by name, so plugging in new audio devices
  doesn't change your selection.
- Global hotkeys are handled via `pynput`. On Linux with Wayland, global
  hotkey capture may not work depending on your compositor (X11 works).
  On macOS, hotkeys need Input Monitoring permission (System Settings >
  Privacy & Security > Input Monitoring) for Soundboard, or for your
  terminal when running from source. The app asks once and shows a
  warning with an "Open settings" button while hotkeys are blocked;
  restart Soundboard after allowing it. Because the macOS build isn't
  notarized, each new version may need the permission granted again.
- Sounds live in profiles in `soundboard_config.json`. A config from
  before profiles is migrated on first run: everything it had becomes a
  profile called "Default", keeping its sounds, hotkeys and order.
- Sounds are stored in `soundboard_config.json` by filename, relative to
  `Sounds/`, so you can move the whole folder. Sounds added by older
  versions keep their absolute path; moving or renaming those files will
  show them as missing in the list.

## License

[MIT](LICENSE)
