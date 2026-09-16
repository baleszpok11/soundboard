---
name: macos-test-build
description: Build, install and open a local "Soundboard Test" macOS app from the current code (or several unmerged branches combined) so the user can test hotkeys, permissions and UI with a real keyboard. Use when the user asks for a test build, wants to try a change on their Mac, or when a change needs manual testing that synthetic events can't cover.
---

# macOS test build

Builds `Soundboard Test.app` next to the real app, installs it in
/Applications and opens it. It shares the macOS app's data folder,
`~/Documents/Soundboard`, but never touches the project's
`soundboard_config.json` or `Sounds/`.

## Why it's done this way

- **onedir, never onefile.** A onefile `.app` starts a BackgroundOnly
  launcher plus an unregistered child that owns the window: the window may
  never show, and the app can't be found under Input Monitoring.
- **Install in /Applications.** Apps run from the scratchpad (a hidden temp
  path) didn't register with Input Monitoring reliably.
- **Separate name.** "Soundboard Test" keeps the test permission and Dock
  entry apart from a real Soundboard install.
- **Absolute paths.** PyInstaller resolves `--add-data`/`--icon` against
  `--specpath`, so relative paths break.

## Steps

1. **Pick the code.**
   - Current branch: build from a copy of the checkout (the working tree may
     change while the build runs):
     `SRC=$SCRATCH/testbuild-src; rm -rf "$SRC"; mkdir -p "$SRC"; git archive HEAD | tar -x -C "$SRC"`
     (add uncommitted changes with `cp soundboard.py "$SRC"/` if needed).
   - Several unmerged branches: `git worktree add -b test-combined "$SCRATCH/testbuild" master`,
     merge each branch there, resolve conflicts, run the tests on the result,
     then use that worktree as `SRC`. Remove the worktree and the branch
     afterwards (`git worktree remove --force`, `git branch -D test-combined`).
2. **Build** (`$SCRATCH` = the session scratchpad; `PROJ` = repo root):
   ```
   OUT="$SCRATCH/pyitest"; rm -rf "$OUT"; mkdir -p "$OUT"
   "$PROJ/.venv/bin/pyinstaller" --noconfirm --onedir --windowed --name "Soundboard Test" \
     --icon "$SRC/assets/icon.icns" --add-data "$SRC/assets/icon.png:assets" \
     --collect-data customtkinter --collect-all yt_dlp --collect-all imageio_ffmpeg \
     --distpath "$OUT/dist" --workpath "$OUT/build" --specpath "$OUT" \
     "$SRC/soundboard.py" > "$OUT/log.txt" 2>&1
   APP="$OUT/dist/Soundboard Test.app"
   plutil -insert NSMicrophoneUsageDescription -string "Soundboard uses your microphone." "$APP/Contents/Info.plist"
   codesign --force --deep -s - "$APP" && codesign -v "$APP"
   ```
   Takes about a minute; run it in the background. Check `log.txt` has no
   `DEPRECATION` line about onefile.
3. **Install and open:**
   ```
   pkill -f "Soundboard Test.app/Contents/MacOS/Soundboard Test"
   rm -rf "/Applications/Soundboard Test.app"
   cp -R "$APP" /Applications/ && open "/Applications/Soundboard Test.app"
   ```
   Confirm with `lsappinfo list | grep -A4 '"Soundboard Test"'`: expect
   one process with `type="Foreground"`.
4. **Tell the user about permissions.** Every rebuild gets a new ad-hoc
   signature, so macOS may keep an Input Monitoring entry that no longer
   matches:
   - System Settings > Privacy & Security > Input Monitoring: turn
     **Soundboard Test** off and on (or remove it with **-** and add it
     again with **+** > Applications > Soundboard Test).
   - Quit (Cmd+Q) and reopen the app; the red hotkey warning should be gone.
   - Only Ctrl-free hotkeys depend on the macOS canonical fix; a Ctrl combo
     is the safest first test.
5. **Give a numbered test plan** for what changed: exact keys to press,
   what should appear, and what counts as a failure. Ask for screenshots of
   anything odd.

## Gotchas

- `screencapture` returns a black image while the screen is locked or
  asleep; don't read that as a broken UI.
- The terminal can't post synthetic key events (no Accessibility), so real
  keypresses can only come from the user.
- A crash shows "Python quit unexpectedly"; look for the newest
  `~/Library/Logs/DiagnosticReports/Python-*.ips` or `Soundboard Test-*.ips`
  and read the faulting thread.
- When done, the user can delete `/Applications/Soundboard Test.app` and its
  Input Monitoring entry.
