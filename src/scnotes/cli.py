"""scnotes CLI. Hotkeys call `toggle`, `grab` and `annotate`; `gui` opens the control window."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import core


def main(argv=None):
    p = argparse.ArgumentParser(prog="scnotes", description="Narrated screen recording with timed screenshots for AI notes")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gui", help="open the control window")
    sub.add_parser("setup", help="add noise suppression + webcam background blur in OBS (idempotent)")
    s = sub.add_parser("start", help="start a session")
    s.add_argument("title")
    sub.add_parser("toggle", help="start (auto title) or stop")
    g = sub.add_parser("grab", help="screenshot the screen source now")
    g.add_argument("--note")
    a = sub.add_parser("annotate", help="region grab with Flameshot's editor (highlight, arrows)")
    a.add_argument("--note")
    sub.add_parser("stop", help="stop recording")
    b = sub.add_parser("build", help="transcribe and write notes.md with screenshots inline")
    b.add_argument("dir", nargs="?", type=Path)
    sub.add_parser("status", help="current session as JSON")
    sub.add_parser("hotkeys", help="install KDE global shortcuts")
    sub.add_parser("config", help="print config path and values")
    args = p.parse_args(argv)

    try:
        if args.cmd == "gui":
            from .gui import run
            return run()
        if args.cmd == "setup":
            print("\n".join(core.ensure_filters(core.load_config())))
        elif args.cmd == "start":
            print(core.start(args.title))
        elif args.cmd == "toggle":
            print(core.toggle())
        elif args.cmd in ("grab", "annotate"):
            print(json.dumps(core.grab(annotate=args.cmd == "annotate", note=args.note)))
        elif args.cmd == "stop":
            print(core.stop())
        elif args.cmd == "build":
            print(core.build(args.dir))
        elif args.cmd == "status":
            d = core.current_session()
            print(json.dumps({"dir": str(d), **core.read_session(d), "grabs": len(core.read_grabs(d))} if d else {}, indent=2))
        elif args.cmd == "hotkeys":
            print("\n".join(core.install_hotkeys()))
        elif args.cmd == "config":
            if not core.CONFIG_PATH.exists():
                core.save_config(core.load_config())
            print(core.CONFIG_PATH)
            print(json.dumps(core.load_config(), indent=2))
    except core.ScnotesError as e:
        core.notify(f"Error: {e}")
        sys.exit(f"scnotes: {e}")


if __name__ == "__main__":
    main()
