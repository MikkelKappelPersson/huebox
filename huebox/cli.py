"""Command-line surface: argparse, dispatch, exit codes (§4, §13.5).

    huebox                TUI when stdout is a terminal, otherwise a preview
    huebox edit [name]    force the interactive editor (a theme if named)
    huebox show [name]    force the static preview
    huebox --dump [name]  print the resolved colours and exit
    huebox new <name>     seed a theme from the terminal (or the ramp), edit it
    huebox list           the library, current theme marked
    huebox use <name>     make a theme current
    huebox import <name>  snapshot the detected terminal into a theme

A name argument means "a theme in ~/.config/huebox"; without one the v1
terminal config is the subject. Pushing a saved theme back to a terminal
arrives with §13.6 — until then `use` and `edit` write truth only.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import namedtuple

from . import __version__, themes
from .color import SLOTS
from .detect import resolve
from .editor import edit
from .formats import FORMAT_NAMES, FORMATS
from .render import render_preview
from .tui import term_size

#: What a command works on: a theme (by name) or a terminal config.
Target = namedtuple("Target", "theme label path slots")
ACTIONS = ("show", "edit", "new", "list", "use", "import")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="huebox",
        description="show and edit your terminal's colour theme")
    parser.add_argument("action", nargs="?", default=None,
                        help=f"one of {', '.join(ACTIONS)}: show a static "
                             "preview (default off a TTY), open the "
                             "interactive editor, or work with the theme "
                             "library")
    parser.add_argument("name", nargs="?", default=None,
                        help="theme name: new/import/use take one, "
                             "edit/show/--dump read one")
    parser.add_argument("-f", "--format", choices=FORMAT_NAMES,
                        help="force a terminal format instead of detecting one")
    parser.add_argument("-c", "--config", help="path to the config file")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing theme (new/import)")
    parser.add_argument("--from", dest="from_fmt", choices=FORMAT_NAMES,
                        metavar="FMT",
                        help="terminal format to read from (import, and the "
                             "seed for new)")
    parser.add_argument("--dump", action="store_true",
                        help="print the resolved colours as key=value and exit")
    parser.add_argument("--formats", action="store_true",
                        help="list supported formats and exit")
    parser.add_argument("--version", action="version",
                        version=f"huebox {__version__}")
    args = parser.parse_args(argv)

    if args.action is not None and args.action not in ACTIONS:
        # `huebox --dump ember` puts the theme in the action slot: one
        # positional that is not a command is a name, so the documented
        # `--dump [name]` / `show [name]` forms work in that order too —
        # but only when the token could legally be a theme name
        if args.name is None and themes.valid_name(args.action):
            args.action, args.name = None, args.action
        else:
            parser.error(f"unknown action {args.action!r} "
                         f"(choose from {', '.join(ACTIONS)})")

    if args.dump and args.action in {"use", "list", "new", "import"}:
        parser.error(f"--dump cannot be combined with {args.action}")

    if args.formats:
        for name in FORMAT_NAMES:
            print(name)
        return 0

    action = args.action
    if action is None:
        # Bare `huebox`: the thing you are working on, edited on a TTY and
        # previewed off one. §13.4 puts the terminal config behind it as the
        # fallback, so this is only different once a theme library exists.
        tty = sys.stdout.isatty() and sys.stdin.isatty()
        return _cmd_view(args, "edit" if tty else "show", follow_current=True)

    try:
        if action == "list":
            return _cmd_list()
        if action == "new":
            return _cmd_new(args)
        if action == "import":
            return _cmd_import(args)
        if action == "use":
            return _cmd_use(args)
        return _cmd_view(args, action)
    except themes.ThemeError as error:      # bad name, no such theme, ...
        return _fail(str(error))


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def _fail(message: str) -> int:
    print(f"huebox: {message}", file=sys.stderr)
    return 1


def _warn(message: str) -> None:
    print(f"huebox: {message}", file=sys.stderr)


def _name_problem(name, verb: str):
    """The complaint for a bad or missing name argument (§13.3), or None."""
    if not name:
        return f"{verb} needs a theme name"
    if not themes.valid_name(name):
        return "invalid theme name"
    return None


# --------------------------------------------------------------------------
# picking what to work on
# --------------------------------------------------------------------------

def _theme(name: str):
    """A theme by name, or None after reporting why not."""
    if not themes.valid_name(name):
        _fail("invalid theme name")
        return None
    warnings: list = []
    try:
        slots = themes.load(name, warnings)
    except themes.ThemeError as error:
        _fail(str(error))
        return None
    for warning in warnings:
        _warn(warning)
    return Target(name, f"theme {name}", themes.theme_path(name), slots)


def _resolve(fmt=None, path=None):
    """`resolve()` plus the one case it hands back without a format.

    `--config` without `--format` infers from the path (§7.1); when the
    path says nothing, say that instead of crashing on a missing reader.
    """
    fmt, path, error = resolve(fmt, path)
    if error:
        return None, None, error
    if not fmt:
        return None, None, ("cannot tell which format this config is - pass "
                            "--format with one of "
                            f"{', '.join(FORMAT_NAMES)}")
    return fmt, path, None


def _terminal(fmt=None, path=None):
    """The v1 subject: the resolved terminal config and its colours."""
    fmt, path, error = _resolve(fmt, path)
    if error:
        _fail(error)
        return None
    slots = FORMATS[fmt]["read"](path)
    if not slots:
        _fail(f"no colours found in {path}")
        return None
    return Target(None, fmt, path, slots)


def _edit_target(args) -> Target:
    """`edit [name]`: a named theme, the current one, or direct mode (§13.4).

    Direct mode is the v1 behaviour, reached only when there is nothing to
    open: no current theme, or a current theme whose file has been deleted
    or renamed behind our back. Both say so, and both are what keeps
    `rm`/`mv` on theme files safe.
    """
    if args.name:
        return _theme(args.name)
    name = themes.current()
    if name is None:
        if themes.list_themes():
            _warn("no current theme set - editing the terminal config "
                  "directly (huebox use <name> to pick one)")
        return _terminal(args.format, args.config)
    if not os.path.isfile(themes.theme_path(name)):
        _warn(f"current theme {name} no longer exists - "
              "editing the terminal config directly")
        return _terminal(args.format, args.config)
    return _theme(name)


# --------------------------------------------------------------------------
# running the editor
# --------------------------------------------------------------------------

def _run_editor(target: Target) -> None:
    """Open the editor; theme mode writes truth, direct mode writes config.

    Theme mode has no `.bak` (huebox owns the file, §14.2) and no push yet
    — the writer returns the status the status bar shows (§13.6 lands the
    terminal side).
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # a pipe or a file: no terminal to drive, and no traceback either
        command = f"huebox edit {target.theme}" if target.theme else "huebox edit"
        _warn(f"not a terminal - run `{command}` in a terminal")
        return

    if target.theme is None:
        write = FORMATS[target.label]["write"]
        edit(target.label, target.path, target.slots, write)
        return

    name = target.theme

    def write(_path, values):
        themes.save(name, values)
        return f"saved {name}"

    edit(f"theme {name}", target.path, target.slots, write,
         backup=False, theme=name)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _cmd_view(args, action: str, follow_current: bool = False) -> int:
    """`show` / `edit` / `--dump` — a theme when named, else the config.

    `follow_current` is the bare-`huebox` default: with no name and no
    command, what you see is the current theme, falling back to the
    terminal config exactly as `edit` does (§13.4). An explicit `show`
    keeps meaning the terminal config (§13.5).
    """
    if args.name:
        target = _theme(args.name)
    elif action == "edit" or follow_current:
        target = _edit_target(args)
    else:
        target = _terminal(args.format, args.config)
    if target is None:
        return 1

    if args.dump:
        print(f"# {target.label} {target.path}")
        for slot in SLOTS:
            if slot in target.slots:
                print(f"{slot}={target.slots[slot]}")
        return 0

    if action == "edit":
        _run_editor(target)
    else:
        cols = term_size()[0] if sys.stdout.isatty() else 96
        print(render_preview(target.label, target.path, target.slots, cols=cols))
    return 0


def _cmd_new(args) -> int:
    """`new <name>`: seed from the terminal if it has colours, else the ramp."""
    problem = _name_problem(args.name, "new")
    if problem:
        return _fail(problem)

    fmt, path, error = _resolve(args.from_fmt or args.format, args.config)
    # a terminal we cannot read is not an error here: the ramp is the seed —
    # but an explicitly requested source must say why it was not used
    explicit = args.from_fmt or args.format or args.config
    slots = themes.read_terminal(fmt, path) if fmt and path else {}
    if slots:
        source = f"{fmt}:{path}"
    else:
        if explicit:
            reason = error or (f"no colours in {path}" if path
                               else "nothing readable")
            print(f"huebox: {reason} - seeding from the ramp", file=sys.stderr)
        slots, source = dict(themes.RAMP), None    # §13.5 fallback

    themes.create(args.name, slots, source=source, force=args.force)
    themes.set_current(args.name)
    seeded = (f"seeded from {source}" if source
              else "seeded from the built-in ramp")
    # open the file that was just written, so gaps a sparse config left are
    # editable greys rather than absent slots (§13.2)
    _run_editor(Target(args.name, f"theme {args.name}",
                       themes.theme_path(args.name), themes.load(args.name)))
    print(f"  new theme {args.name}  {themes.theme_path(args.name)}")
    print(f"  {seeded}\n")
    return 0


def _cmd_import(args) -> int:
    """`import <name>`: snapshot the detected terminal. Current untouched."""
    problem = _name_problem(args.name, "import")
    if problem:
        return _fail(problem)

    fmt, path, error = _resolve(args.from_fmt, args.config)
    if error:
        return _fail(error)
    slots = themes.read_terminal(fmt, path)
    if not slots:
        return _fail(f"no colours found in {path}")

    themes.create(args.name, slots, source=f"{fmt}:{path}", force=args.force)
    print(f"  imported {args.name}  {themes.theme_path(args.name)}")
    print(f"  from {fmt}:{path}\n")
    return 0


def _cmd_use(args) -> int:
    """`use <name>`: make it current. The push is the next phase (§13.6)."""
    problem = _name_problem(args.name, "use")
    if problem:
        return _fail(problem)
    if not os.path.isfile(themes.theme_path(args.name)):
        return _fail(f"no such theme: {args.name}")

    themes.set_current(args.name)
    print(f"  current theme: {args.name}")
    print("  your terminal is not updated yet - push arrives with "
          "push-on-save\n")
    return 0


def _cmd_list() -> int:
    """`list`: one line per theme, current marked, source and age shown."""
    rows = themes.list_themes()
    if not rows:
        print("  no themes yet - create one with: huebox new <name>")
        return 0
    current = themes.current()
    width = max(len(name) for name, _, _ in rows)
    for name, _path, mtime in rows:
        origin = themes.source_of(name).split(":", 1)[0] or "-"
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        mark = "*" if name == current else " "
        print(f"{mark} {name:<{width}}  {origin:<8}  {stamp}")
    return 0
