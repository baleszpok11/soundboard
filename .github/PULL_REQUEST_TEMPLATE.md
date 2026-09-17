## What this changes

<!-- What it does and why. If it closes an issue, put "Fixes #<number>"
     here so merging closes it. -->

Fixes #

## How it was checked

<!-- Which OS, which devices, what you did in the app. Imports and a
     linter are not enough for audio, hotkeys, the tray or autostart.
     For a bug fix, confirm it fails without the change. -->

- OS tested on:
- Not tested on:

## Anything a reviewer should look at twice

<!-- Delete the ones that do not apply. These are the changes that go
     wrong quietly or late, so they get named rather than found. -->

- [ ] Changes the config schema, or how existing config is read
- [ ] Touches packaging, the build workflow, or what a release publishes
- [ ] Changes a default, or removes existing behaviour
- [ ] Touches files outside `Sounds/`
- [ ] Touches the relay, or anything near a token
- [ ] Changes the UI (if so, re-run `tools/make_tutorial_shots.py`)
- [ ] Changes setup instructions (if so, update the README and
      `tutorial.py` together)
- [ ] None of the above
