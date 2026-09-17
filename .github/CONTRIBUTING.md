# Contributing

Thanks for taking an interest in Soundboard. Bug reports, ideas and pull
requests are all welcome.

## Reporting bugs

The easiest way is from inside the app: **Report a bug** on the
Soundboard tab, or the button on any error dialog. It shows you the whole
report before anything is sent, and it fills in your OS, app version,
selected devices and the recent log for you. See
[Reporting a bug](../README.md#reporting-a-bug) for what a report
contains.

Otherwise, open an [issue](../../issues/new/choose). For an audio problem,
always include your OS and version, which input and output devices are
selected, and your virtual audio device (VB-CABLE, BlackHole, PulseAudio
null sink). Most reports that are hard to act on are missing one of those.

## Getting set up

Requirements: Python 3.9+ with a current Tk.

```
pip install -r requirements.txt
python main.py
```

Platform notes that cost people the most time:

- **macOS**: the Python from Apple's Command Line Tools ships Tk 8.5 and
  renders blank windows. Use Homebrew:
  `brew install python@3.12 python-tk@3.12`. Global hotkeys need Input
  Monitoring permission for your terminal when running from source.
- **Linux**: `sounddevice` does not bundle PortAudio - install
  `libportaudio2`. Global hotkeys work on X11; on Wayland it depends on
  your compositor.
- **Windows**: nothing extra.

You need a virtual audio device to hear the output the way other people
would; the [README](../README.md) has the setup for each OS, and the **?**
button in the app walks through it with screenshots.

## Making a change

- One branch per issue, off an up-to-date `master`:
  `issue-<number>-<short-slug>`. For work without an issue,
  `fix-<slug>` or `feature-<slug>`.
- Keep the change focused on one thing. A small diff that does what the
  issue asked is much easier to review than a rewrite that happens to
  include it.
- Match the surrounding code: same naming, same comment density. Comments
  explain why something is done a particular way, not what the line does.
- No emojis in code or comments.
- Everything stays free and open-source. No paywalled dependencies or
  services.

Where things live is described in [CLAUDE.md](../CLAUDE.md), which doubles
as the architecture notes: `main.py` is the entry point, everything else
is in `soundboard/`, and the window is assembled from mixins
(`devices.py`, `mic_hotkeys.py`, `sound_list.py`, `download_tab.py`,
`editor_tab.py`).

Two things that are easy to miss:

- Offline clip processing for the editor belongs in `dsp.py`, not
  `audio_engine.py`. The engine has to fill a block before the sound card
  asks again; `dsp.py` is allowed to take a second over a whole clip.
- Editor effects are declared as data in `EFFECT_SLIDERS` /
  `EFFECT_TOGGLES`. A new one needs a row in the table and a call in
  `_apply_effects` - reset, undo and enable/disable all walk the tables
  themselves.
- The in-app tutorial (`tutorial.py`) covers the same ground as the
  README's virtual-device section. Change both together, and re-run
  `tools/make_tutorial_shots.py` after a UI change so the screenshots
  still show the app that exists.

## Testing

Run it. Audio routing, global hotkeys, the tray icon and autostart cannot
be verified by importing the module - they need a real machine with real
devices, and hotkeys and permissions behave differently on every OS. If
you could only test on one platform, say so in the pull request; that is
useful information, not a failing.

If you are fixing a bug, check that it actually fails without your change
and passes with it.

## Pull requests

Open the pull request into `master` and put `Fixes #<number>` in the body
so merging closes the issue. Describe what you changed, and how you
checked it - which OS, which devices, what you did in the app.

Pull requests are squash-merged, and the branch is deleted on merge.

Changes that need extra care in review, so mention them explicitly:

- Anything that changes the config schema or how existing config is read.
  Getting this wrong empties someone's board with no error.
- Packaging, the build workflow, or what a release publishes. Those only
  misbehave at release time, long after merging.
- Changing a default, removing behaviour, or touching files outside
  `Sounds/`.
- Anything near the relay in [relay/](../relay), or near a token. No
  GitHub token ever goes in the app - it can be extracted from the
  binary.

## Code of Conduct

By participating you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
