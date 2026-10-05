"""Command-line surface: argparse, dispatch, exit codes (§4, §13.5).

    huebox                TUI when stdout is a terminal, otherwise a preview
    huebox edit [name]    force the interactive editor (a theme if named)
    huebox show [name]    force the static preview
    huebox --dump [name]  print the resolved colours and exit
    huebox new <name>     seed a theme from the terminal (or the ramp), edit it
    huebox list           the library, current theme marked
    huebox use <name>     make a theme current and push it to the terminal
    huebox import <name>  snapshot the detected terminal into a theme

A name argument means "a theme in ~/.config/huebox"; without one the v1
terminal config is the subject. Saving or using a theme writes the truth
file first and pushes it to the terminal second (§13.6, decision 7) - a push
that fails is reported and exits 1, and the truth file stays written.
A ghostty target whose config is organised by theme is pushed as the
terminal's own theme file, `~/.config/ghostty/themes/<name>`, with the
`theme =` pointer repointed at it; a theme's colours never land in a file
that belongs to another theme (§13.6, decision 26).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from collections import namedtuple

from . import __version__, editor, themes
from .color import SLOTS
from .detect import resolve
from .editor import Library
from .formats import FORMAT_NAMES, FORMATS
from .render import render_preview
from .tui import term_size

#: What a command works on: a theme (by name) or a terminal config.
Target = namedtuple("Target", "theme label path slots")
ACTIONS = ("show", "edit", "new", "list", "use", "import")

#: Where a save pushes, from `--to` / `--format` / `--config` / `--no-push`
#: (§13.6). An empty `to` means "the terminal you are in", which only
#: `resolve()` can answer. `ghostty_native` forces the ghostty export — a
#: theme file in `~/.config/ghostty/themes` plus a `theme =` pointer —
#: and `ghostty_in_place` forbids it; with neither, the config decides
#: (decision 26). `reload` asks the terminals that took a push to re-read
#: their config, so a save ends live. It is `False` here so a `PushSpec`
#: built in a test or a library call is inert unless it says otherwise.
PushSpec = namedtuple("PushSpec",
                      "to fmt path no_push ghostty_native ghostty_in_place "
                      "reload",
                      defaults=(False, False, False))

#: The two lines that are advice rather than report. Both are about the
#: terminal, not the theme, so they belong to the caller that knows what it
#: just did.
NO_PUSH_LINE = "no push requested (--no-push) - the terminal is unchanged"
RELOAD_HINT = "reload your terminal to see it (alacritty reloads by itself)"


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
    parser.add_argument("--to", metavar="FMT[,FMT...]",
                        help="push targets for a theme save or `use` "
                             "(default: the terminal you are in)")
    parser.add_argument("--no-push", action="store_true",
                        help="write the theme file only, leave the terminal "
                             "config alone")
    parser.add_argument("--ghostty-native", action="store_true",
                        help="always push ghostty as a theme file: write "
                             "~/.config/ghostty/themes/<name> and point the "
                             "config at it, even where the colours are "
                             "inline (the default when the config already "
                             "uses theme =)")
    parser.add_argument("--ghostty-in-place", action="store_true",
                        help="never export a ghostty theme file: edit the "
                             "file the colours already live in, the way "
                             "kitty and alacritty are always pushed")
    parser.add_argument("--no-reload", dest="reload", action="store_false",
                        help="do not ask the terminal to re-read its config "
                             "after a push; the report keeps saying which key "
                             "to press")
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

    # push targets are validated before anything else runs: a bad `--to`
    # must never be discovered half a save in (§13.6)
    try:
        spec = _push_spec(args)
    except themes.ThemeError as error:
        return _fail(str(error))

    action = args.action
    if action is None:
        # Bare `huebox`: the thing you are working on, edited on a TTY and
        # previewed off one. §13.4 puts the terminal config behind it as the
        # fallback, so this is only different once a theme library exists.
        tty = sys.stdout.isatty() and sys.stdin.isatty()
        return _cmd_view(args, "edit" if tty else "show", spec,
                         follow_current=True)

    try:
        if action == "list":
            return _cmd_list()
        if action == "new":
            return _cmd_new(args, spec)
        if action == "import":
            return _cmd_import(args)
        if action == "use":
            return _cmd_use(args, spec)
        return _cmd_view(args, action, spec)
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
# where a save pushes (§13.6)
# --------------------------------------------------------------------------

def _push_spec(args) -> PushSpec:
    """`--to` / `--format` / `--config` / `--no-push` as one push target.

    Raises `ThemeError` for anything the user has to fix, and this runs
    before the first write of every command, so a typo in `--to` can never
    leave a half-pushed save behind.
    """
    to: list = []
    for part in (args.to or "").split(","):
        name = part.strip()
        if not name:
            continue
        if name not in FORMATS:
            raise themes.ThemeError(f"unknown format {name!r} "
                                    f"(choose from {', '.join(FORMAT_NAMES)})")
        if name not in to:
            to.append(name)
    if args.to is not None and not to:
        raise themes.ThemeError("--to needs at least one format")
    if to and args.config and len(to) > 1:
        raise themes.ThemeError("--config pushes one format; --to names "
                                "several - drop --config or keep a single --to")
    if args.ghostty_native and args.ghostty_in_place:
        raise themes.ThemeError("--ghostty-native and --ghostty-in-place "
                                 "ask for opposite things - pick one")
    return PushSpec(tuple(to), args.format, args.config, args.no_push,
                    args.ghostty_native, args.ghostty_in_place, args.reload)


def _push_lines(result) -> list:
    """A push report plus the one line of advice a written config needs."""
    lines = list(result.lines)
    if result.pushed and not result.reloaded:
        lines.append(RELOAD_HINT)
    return lines


def _pushed(result) -> str:
    """`ghostty, kitty` - the format names a status line names."""
    return ", ".join(fmt for fmt, _ in result.pushed)


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

def _textual_app():
    """The Textual shell, imported late.

    Not at module level, and that is the whole point of `textual` being an extra
    (§9): `huebox show`, `list`, `use`, `--dump` and `--formats` must not pay
    Textual's import, which is the larger half of a cold start. Importing it
    eagerly also made every CLI subprocess in the suite ~0.7s slower and the
    `test_themes` module four times slower than it needs to be — which is how the
    cost was found. The `REQUIRES` check above has already run, so by here the
    import cannot fail for a missing extra.
    """
    from . import app
    return app


def _missing_extras() -> list:
    """Optional-dependency groups the editor needs that are not installed.

    Read off `editor.REQUIRES` rather than named here, so the editor is the one
    place that says what it needs and cannot drift from it.

    A missing extra is a user error, so it is reported on stderr with the
    install line and exits 1 — never a traceback from deep inside an import
    (AGENTS.md). Returns the names, empty when nothing is missing.
    """
    return [name for name in editor.REQUIRES
            if importlib.util.find_spec(name) is None]


def _run_editor(target: Target, spec: PushSpec = None, driver=None) -> int:
    """Open the editor; theme mode writes truth, then pushes (§13.6).

    One writer serves the whole session, and it branches on the subject:
    a theme name means truth-then-push, `None` means the v1 direct-mode
    write into the terminal config itself (§13.4). The picker can move the
    subject mid-session (§13.7), so the writer is handed the name and the
    path instead of closing over them.

    The returned status string is what the status bar shows
    (`saved ember → ghostty`). A push that fails never undoes the save
    (decision 7): the buffer is clean, the report says why, and the process
    ends 1.

    `spec=None` is a caller with no command line behind it, so the session
    writes truth only: a programmatic `edit()` must never push a terminal
    the caller did not ask about.

    `driver` is the session, `app.run` by signature. Naming it is what keeps
    this function about the *wiring* — the writer, the picker, the backup path —
    rather than about Textual: the suites below drive the same loop through the
    stdlib seams so they can feed it keys and capture its frames, which a real
    compositor will not let them do.
    """
    spec = spec or PushSpec((), None, None, True)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # a pipe or a file: no terminal to drive, and no traceback either
        command = f"huebox edit {target.theme}" if target.theme else "huebox edit"
        _warn(f"not a terminal - run `{command}` in a terminal")
        return 0

    if driver is None:
        # Only the default driver needs the extra. An injected one is a test's
        # business — it may be the stdlib loop, which needs nothing — so the
        # check belongs with the choice, not in front of it.
        #
        # Two orderings matter here. It comes *after* the tty test, so
        # `huebox edit | cat` says what is actually wrong with a piped session
        # instead of asking for an install it will never use; and *before* the
        # import, which is the whole difference between a clean line on stderr
        # and an ImportError traceback (AGENTS.md).
        missing = _missing_extras()
        if missing:
            groups = ",".join(missing)
            return _fail(f"the editor needs the '{groups}' extra — install it "
                         f"with `pipx install 'huebox[{groups}]'` "
                         f"(or `uv tool install 'huebox[{groups}]'`), or run "
                         f"`huebox show` which needs no extra")
        run = _textual_app().run
    else:
        run = driver

    direct_fmt = target.label if target.theme is None else None
    label = spec.fmt or (spec.to[0] if spec.to else "")
    report: list = []          # the last save's push report, read at exit
    notes: list = []           # the picker's complaints, printed the same way
    failed: list = []          # non-empty when a push did not get through
    direct_warned = [False]    # --to-in-direct-mode note fires once
    native_warned = [False]    # so does --ghostty-native with nothing to name

    def write(theme, path, values):
        if theme is None:      # legacy direct mode: the config is the truth
            if spec.to and not spec.no_push and not direct_warned[0]:
                direct_warned[0] = True
                notes.append("--to has no push target in a direct session "
                             "- the config itself is written")
            if spec.ghostty_native and not native_warned[0]:
                native_warned[0] = True
                notes.append("--ghostty-native needs a theme to name a file "
                             "after - a direct session writes the config "
                             "(N saves the buffer as a theme)")
            return FORMATS[direct_fmt]["write"](path, values)
        themes.save(theme, values)          # truth first, always
        del report[:]                      # one report: this save's
        del failed[:]
        if spec.no_push:
            report.append(NO_PUSH_LINE)
            return f"saved {theme} (truth only)"
        result = themes.push(values, to=spec.to, fmt=spec.fmt, path=spec.path,
                             ghostty_native=spec.ghostty_native, name=theme,
                             ghostty_in_place=spec.ghostty_in_place,
                             reload=spec.reload)
        report.extend(_push_lines(result))
        if result.failed:
            failed.append(result)
            return f"saved {theme} - push failed"
        return f"saved {theme} → {_pushed(result)}"

    # The Textual shell runs the session and reports it on the way out
    # (`app.run` → `editor.report_session`). The four injected seams are the same
    # ones `editor.edit` took, unchanged — the writer, the picker's `Library`,
    # and the two prompt seams the shell satisfies by handing the terminal back
    # — so the writer above is built once and both readers mean the same thing.
    run(label, target.path, target.slots, write,
        backup_path=target.path if direct_fmt is not None else None,
        theme=target.theme, library=_library(notes), report=report,
        notes=notes)
    return 1 if failed else 0


def _library(notes: list) -> Library:
    """The picker onto the theme store (§13.7) — three calls, no import.

    `notes` collects what cannot go on a one-line status bar: a theme that
    would not open, a state file that could not be written, a hand-edited
    theme's dropped keys. `_run_editor` prints them after the session,
    where a report belongs and not inside the frame.
    """

    def listing():
        return [name for name, _path, _mtime in themes.list_themes()]

    def loader(name):
        warnings: list = []
        try:
            slots = themes.load(name, warnings)
        except themes.ThemeError as error:
            notes.append(str(error))
            return None
        try:
            themes.set_current(name)          # opening is choosing (§13.4)
        except OSError as error:
            notes.append(f"cannot write state: {error}")
        notes.extend(warnings)
        return slots, themes.theme_path(name)

    def creator(name, slots, force=False):
        try:
            path = themes.create(name, slots, force=force)
        except themes.ThemeError as error:
            return "", f"{error} (letters, digits, - and _, 64 max)"
        except OSError as error:
            notes.append(f"could not create {name}: {error}")
            return "", f"could not create {name}"
        try:
            themes.set_current(name)
        except OSError as error:
            notes.append(f"created {name} but the state file could not "
                         f"be written: {error}")
            return path, f"created {name} - state not written"
        return path, ""

    return Library(listing, loader, creator)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _cmd_view(args, action: str, spec: PushSpec, follow_current: bool = False) -> int:
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
        return _run_editor(target, spec)
    cols = term_size()[0] if sys.stdout.isatty() else 96
    print(render_preview(target.label, target.path, target.slots, cols=cols))
    return 0


def _cmd_new(args, spec: PushSpec) -> int:
    """`new <name>`: seed from the terminal if it has colours, else the ramp.

    Creating a theme is not a push: the file starts out equal to whatever
    it was seeded from. The editor session that follows pushes on its saves
    like any other theme session.
    """
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
    try:
        themes.set_current(args.name)
    except OSError as error:
        return _fail(f"cannot write state: {error}")
    seeded = (f"seeded from {source}" if source
              else "seeded from the built-in ramp")
    # open the file that was just written, so gaps a sparse config left are
    # editable greys rather than absent slots (§13.2)
    status = _run_editor(Target(args.name, f"theme {args.name}",
                                themes.theme_path(args.name),
                                themes.load(args.name)), spec)
    print(f"  new theme {args.name}  {themes.theme_path(args.name)}")
    print(f"  {seeded}\n")
    return status


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


def _cmd_use(args, spec: PushSpec) -> int:
    """`use <name>`: make it current, then push it live (§13.6).

    Current first, terminal second, and the truth is never rolled back: a
    push that cannot land is a report on stderr and exit 1, with the theme
    still current and still the thing the next push will send.
    """
    problem = _name_problem(args.name, "use")
    if problem:
        return _fail(problem)
    if not os.path.isfile(themes.theme_path(args.name)):
        return _fail(f"no such theme: {args.name}")

    name = args.name
    try:
        themes.set_current(name)
    except OSError as error:
        return _fail(f"cannot write state: {error}")
    print(f"  current theme: {name}")
    sys.stdout.flush()          # the report is stderr: keep the order honest
    if spec.no_push:
        _warn(NO_PUSH_LINE)
        return 0

    warnings: list = []
    slots = themes.load(name, warnings)     # a hand-edited theme still says
    for warning in warnings:                 # what it could not read (§13.2)
        _warn(warning)
    result = themes.push(slots, to=spec.to, fmt=spec.fmt, path=spec.path,
                         ghostty_native=spec.ghostty_native, name=name,
                         ghostty_in_place=spec.ghostty_in_place,
                         reload=spec.reload)
    for line in _push_lines(result):
        _warn(line)
    return 1 if result.failed else 0


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
