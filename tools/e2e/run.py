"""Run the end-to-end scenarios against a real Soundboard window.

    python tools/e2e/run.py                 # all of them
    python tools/e2e/run.py board_opens     # one, by name
    python tools/e2e/run.py --list

Needs a display. On Linux that can be a virtual one:

    xvfb-run -a -s "-screen 0 1100x900x24" python tools/e2e/run.py

On macOS the windows appear on screen for a few seconds, which is
normal - the app is really running.

Each scenario runs in a subprocess of its own. Tk does not reliably
survive a second root window in one interpreter, and a scenario that
crashes the toolkit would otherwise take the rest of the run with it.
Exit code is the number of scenarios that failed, so CI can gate on it.

Nothing here touches your own config or Sounds/: the harness points the
app at a temporary directory before importing anything that reads them.
"""

import os
import subprocess
import sys

SCENARIO_TIMEOUT_S = 90

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def run_one(name):
    """Run a single scenario in a subprocess and report what it found.

    Under a timeout, because a layout that cannot settle does not crash -
    it sits there redrawing, and without this the whole run waits for a
    window nobody is looking at. A scenario that hangs is a failure like
    any other, and a loud one.
    """
    try:
        result = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", name],
            cwd=ROOT, capture_output=True, text=True, timeout=SCENARIO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 3, (f"  timed out after {SCENARIO_TIMEOUT_S}s - the window never "
                   f"settled (a layout that reflows itself in a loop does this)")
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output


def child(name):
    """The subprocess side: build a window, run one scenario, report."""
    sys.path.insert(0, HERE)
    from harness import SHOT_DIR, Board
    from scenarios import SCENARIOS

    if name not in SCENARIOS:
        print(f"unknown scenario {name!r}")
        return 2
    # A scenario can pin the window it wants. Layout bugs are often
    # width-dependent - a row that fails to lay out at one size quietly
    # rights itself at another - so the size is part of the test.
    scenario = SCENARIOS[name]
    board = Board(geometry=getattr(scenario, "geometry", None) or Board.GEOMETRY,
                  # A setting the app reads at startup has to be in the
                  # file before it opens (`scenario.settings`).
                  settings=getattr(scenario, "settings", None))
    try:
        failures = board.run(scenario)
    except Exception as error:
        print(f"  the scenario raised {type(error).__name__}: {error}")
        print(f"  screenshots: {SHOT_DIR}")
        return 2
    for failure in failures:
        print(f"  {failure}")
    if failures:
        # What the app tried to say in a message box, which the harness
        # answered rather than let it stop the run. Often the whole
        # explanation: a machine with no sound card fails a scenario
        # about playing and says so here.
        for _, title, message in board.dialogs:
            print(f"  a message box said {title}: {message}")
        print(f"  screenshots: {SHOT_DIR}")
    return 1 if failures else 0


def main():
    sys.path.insert(0, HERE)
    # The scenarios import from the app package (the only way to import
    # it - bare imports make a second, unrelated copy of every class),
    # and listing them imports the module here as well as in the child.
    sys.path.insert(0, ROOT)
    args = sys.argv[1:]
    if args and args[0] == "--child":
        return child(args[1])

    from scenarios import SCENARIOS
    if args and args[0] == "--list":
        print("\n".join(SCENARIOS))
        return 0

    names = args or list(SCENARIOS)
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {', '.join(unknown)}")
        print(f"known: {', '.join(SCENARIOS)}")
        return 2

    failed = []
    for name in names:
        print(f"{name} ... ", end="", flush=True)
        code, output = run_one(name)
        print("FAIL" if code else "ok")
        if code:
            failed.append(name)
            print(output.rstrip())
    print()
    print(f"{len(names) - len(failed)}/{len(names)} scenarios passed")
    if failed:
        print(f"failed: {', '.join(failed)}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
