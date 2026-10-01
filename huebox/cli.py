"""Command-line surface: argparse, dispatch, exit codes (§4).

    huebox              TUI when stdout is a terminal, otherwise a static preview
    huebox edit         force the interactive editor
    huebox show         force the static preview
    huebox --dump       print the resolved colours and exit
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .color import SLOTS
from .detect import resolve
from .editor import edit
from .formats import FORMAT_NAMES, FORMATS
from .render import render_preview
from .tui import term_size


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="huebox",
        description="show and edit your terminal's colour theme")
    parser.add_argument("action", nargs="?", default=None,
                        choices=["show", "edit"],
                        help="show a static preview (default off a TTY), "
                             "or open the interactive editor")
    parser.add_argument("-f", "--format", choices=FORMAT_NAMES,
                        help="force a terminal format instead of detecting one")
    parser.add_argument("-c", "--config", help="path to the config file")
    parser.add_argument("--dump", action="store_true",
                        help="print the resolved colours as key=value and exit")
    parser.add_argument("--formats", action="store_true",
                        help="list supported formats and exit")
    parser.add_argument("--version", action="version",
                        version=f"huebox {__version__}")
    args = parser.parse_args(argv)

    if args.formats:
        for name in FORMAT_NAMES:
            print(name)
        return 0

    fmt, path, error = resolve(args.format, args.config)
    if error:
        print(f"huebox: {error}", file=sys.stderr)
        return 1
    slots = FORMATS[fmt]["read"](path)
    if not slots:
        print(f"huebox: no colours found in {path}", file=sys.stderr)
        return 1

    if args.dump:
        print(f"# {fmt} {path}")
        for name in SLOTS:
            if name in slots:
                print(f"{name}={slots[name]}")
        return 0

    action = args.action
    if action is None:
        action = "edit" if sys.stdout.isatty() and sys.stdin.isatty() else "show"

    if action == "edit":
        edit(fmt, path, slots, FORMATS[fmt]["write"])
    else:
        cols = term_size()[0] if sys.stdout.isatty() else 96
        print(render_preview(fmt, path, slots, cols=cols))
    return 0
