"""The theme library: `~/.config/huebox` is the truth (§13).

    home()/themes_dir()/state_path()   where everything lives (§13.1)
    valid_name()                       the one name rule (§13.3)
    create() / save()                  the canonical writer (§13.2)
    load() / list_themes()             the hand-rolled reader for our
                                       TOML subset — no tomllib (3.9)
    current() / set_current()          state.toml, two lines at most
    push()                             truth → terminal, the one write out
    export_ghostty_native()            truth → a Ghostty theme file (§13.6)
    reload_terminal()                  ask a terminal to re-read its config
    RAMP                               the built-in palette `new` seeds from

Theme files belong to huebox: the writer emits one canonical layout and
replaces the file atomically, so a hand-edited theme only ever loses its
unknown keys, never its structure or its colours. Terminal configs are the
opposite case and keep their line-level writer (§6.2): `push()` goes through
the format registry like every other write and never re-serialises one.

A save is truth first, terminal second, and never the other way round
(decision 7): the theme file outlives any one config, so a push that fails
is reported and left alone — nothing is ever rolled back. A push that
succeeds then asks the terminal to re-read what was written, so a save ends
with the terminal already showing it (§13.6).
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from collections import namedtuple
from datetime import datetime

from .color import MISSING, SLOTS, is_hex, normalize_hex
from .detect import config_holds_colours, ensure_theme_pointer, \
    ghostty_main_config, ghostty_theme_name, ghostty_themes_dir, resolve
from .formats import FORMAT_NAMES, FORMATS

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SUFFIX = ".toml"
META_KEYS = ("name", "created", "modified", "source")
HEADER = "# owned by huebox — values are yours, structure is ours"

#: The first line of every exported Ghostty theme file. The file is one
#: colour per line in Ghostty's own dialect, so the terminal reads it, our
#: reader reads it back, and the comment says plainly who owns it.
GHOSTTY_HEADER = "# a huebox theme file — rewritten on every push"

#: The built-in fallback ramp for `huebox new` when no terminal has colours
#: to seed from (§13.5, spec open question 3 — decided). A neutral dark
#: base with a readable foreground, a cursor that inverts it, a selection
#: one step lighter than the background, and the usual 0-7 / 8-15 split:
#: 0 is the background's own black, 7 and 8 are the two greys, and every
#: hue (1-6, 9-14) has a muted base and a bright sibling of the same hue.
RAMP = {
    "background": "#101014",
    "foreground": "#e6e6ea",
    "cursor-color": "#e6e6ea",
    "cursor-text": "#101014",
    "selection-background": "#2a2a34",
    "selection-foreground": "#e6e6ea",
    "palette-0": "#101014",
    "palette-1": "#a83232",
    "palette-2": "#3f7a3f",
    "palette-3": "#a88a3f",
    "palette-4": "#3f6a8a",
    "palette-5": "#8a3f6a",
    "palette-6": "#3f8a8a",
    "palette-7": "#b0b0b8",
    "palette-8": "#d0d0d8",
    "palette-9": "#e06c6c",
    "palette-10": "#6cc06c",
    "palette-11": "#e0c06c",
    "palette-12": "#6c9ce0",
    "palette-13": "#e06c9c",
    "palette-14": "#6cc0c0",
    "palette-15": "#f0f0f8",
}


class ThemeError(Exception):
    """Anything the user has to fix: bad name, missing theme, no clobber."""


# --------------------------------------------------------------------------
# where things live (§13.1)
# --------------------------------------------------------------------------

def home() -> str:
    """`$XDG_CONFIG_HOME/huebox`, falling back to `~/.config/huebox`.

    Config home, not data home: themes are hand-edited and belong in a
    dotfiles repo next to the terminal configs they drive.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "huebox")


def themes_dir() -> str:
    return os.path.join(home(), "themes")


def state_path() -> str:
    return os.path.join(home(), "state.toml")


def valid_name(name) -> bool:
    """`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` — 64 characters at most (§13.3).

    The name is a file name, so the rule stays to what every filesystem
    accepts without quoting; the length cap keeps it usable in a header.
    """
    return bool(name) and isinstance(name, str) and bool(NAME_RE.match(name))


def theme_path(name: str) -> str:
    _check(name)
    return os.path.join(themes_dir(), f"{name}{SUFFIX}")


def _check(name) -> None:
    if not valid_name(name):
        raise ThemeError("invalid theme name")


# --------------------------------------------------------------------------
# the TOML subset we both write and read (§13.2, plan 3.2)
# --------------------------------------------------------------------------

HEX_TOKEN = re.compile(r"#[0-9a-fA-F]{6}\b")


def _strip_comment(line: str) -> str:
    """Drop a trailing `# …`, ignoring a `#` that is part of a value.

    Two traps, both of which a naive split walks into: a quoted `"#1a1b26"`
    is a colour and not a comment, and so is a *bare* one — a hand-written
    `background = #0a0b0c` is exactly what a terminal config looks like,
    and the reader has to accept it (plan appendix B). Anything else after
    a `#` is a comment.
    """
    out, quote = [], None
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#" and not HEX_TOKEN.match(line, index):
            break
        else:
            out.append(char)
        index += 1
    return "".join(out).strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse(text: str) -> tuple[dict, int]:
    """`{section: {key: raw value}}` plus the number of unparsable lines.

    Our grammar, not TOML at large: `[section]` headers, `key = value`
    with the value quoted or bare, comments and blank lines anywhere, any
    key order. Anything else is counted so the caller can warn once.
    """
    sections: dict[str, dict] = {"": {}}
    section = ""
    dropped = 0
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line:
            continue
        if line.startswith("["):
            section = _unquote(line.strip().strip("[]").strip())
            sections.setdefault(section, {})
            continue
        key, sep, value = line.partition("=")
        if not sep:
            dropped += 1                     # no key/value at all
            continue
        key = _unquote(key.strip())
        if key in sections[section]:
            dropped += 1                     # a duplicate: the re-save loses one
        sections[section][key] = _unquote(value.strip())
    return sections, dropped


def _read_file(path: str) -> tuple[dict, int]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return _parse(handle.read())
    except OSError:
        return {}, 0


def _now() -> str:
    """Local ISO-8601 to the second, no timezone (§13.2)."""
    return datetime.now().replace(microsecond=0).isoformat()


def _value(slots: dict, slot: str) -> str:
    value = slots.get(slot)
    return normalize_hex(value) if value and is_hex(value) else MISSING


def _atomic_write(path: str, text: str) -> None:
    """Write a sibling tmp and rename it over the target.

    Theme files are ours, so a save is all-or-nothing: a crash or a full
    disk leaves the old file or the new one, never half a theme. Terminal
    configs keep the in-place line-level writer instead — renaming over
    them would break the §6.2 contract.
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)      # never leave a half-written theme behind
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# writing (§13.2)
# --------------------------------------------------------------------------

def meta(name: str) -> dict:
    """The `[theme]` block of a theme file: name/created/modified/source."""
    sections, _ = _read_file(theme_path(name))
    block = sections.get("theme", {})
    return {key: block[key] for key in META_KEYS if key in block}


def source_of(name: str) -> str:
    """The recorded import origin, `fmt:path`, or `""` when hand-made."""
    return meta(name).get("source", "")


def save(name: str, slots: dict, source: str = None) -> str:
    """Write the canonical theme file; returns its path.

    `created` is preserved from the file already on disk (a re-save is not
    a birth), `modified` is now, and `source` is kept unless a new origin
    is passed — `import` is the only thing that knows one. All 22 slots are
    written, palette-then-named, so a gap-filled load is healed on save.
    """
    _check(name)
    os.makedirs(themes_dir(), exist_ok=True)
    path = theme_path(name)
    header = meta(name)
    now = _now()
    lines = [HEADER, "[theme]",
             f'name = "{name}"',
             f'created = "{header.get("created") or now}"',
             f'modified = "{now}"']
    origin = source or header.get("source")
    if origin:
        lines.append(f'source = "{origin}"  # import origin, informational')
    lines += ["", "[colors]"]
    lines += [f'{slot} = "{_value(slots, slot)}"' for slot in SLOTS]
    _atomic_write(path, "\n".join(lines) + "\n")
    return path


def _clash(name: str):
    """An existing theme file that would collide with `name`.

    Compared case-insensitively: macOS and Windows filesystems fold case,
    where `Ember` and `ember` are one file, and the second `create` would
    silently edit the first (plan appendix B).
    """
    wanted = name.lower()
    try:
        entries = os.listdir(themes_dir())
    except OSError:
        return None
    for entry in entries:
        if entry.endswith(SUFFIX) and entry[:-len(SUFFIX)].lower() == wanted:
            return entry
    return None


def create(name: str, slots: dict, source: str = None, force: bool = False) -> str:
    """Create a theme file; refuses to replace one without `force`.

    The library directory is created here, lazily — running `huebox list`
    on a machine that never made a theme writes nothing (§13.1).
    """
    _check(name)
    os.makedirs(themes_dir(), exist_ok=True)
    if not force and _clash(name):
        raise ThemeError(f"theme {name} already exists "
                         "(--force replaces it)")
    return save(name, slots, source=source)


# --------------------------------------------------------------------------
# reading (§13.2, §13.3)
# --------------------------------------------------------------------------

def load(name: str, warnings: list = None) -> dict:
    """All 22 slots of a theme; gaps arrive as `MISSING` grey.

    Gap-tolerant by design: a hand-written theme with three colours still
    opens, and the editor fills the rest in on the next save. Unknown keys
    and unknown sections are dropped (the writer would lose them anyway)
    and counted, so the caller can say so once.
    """
    path = theme_path(name)
    if not os.path.isfile(path):
        raise ThemeError(f"no such theme: {name}")
    sections, dropped = _read_file(path)
    if warnings is None:
        warnings = []

    slots: dict[str, str] = {}
    for key, value in sections.get("colors", {}).items():
        if key not in SLOTS:
            dropped += 1
        elif is_hex(value):
            slots[key] = normalize_hex(value)
        else:
            warnings.append(f"{name}: {key} = {value!r} is not a colour - ignored")
    for section, block in sections.items():
        if section == "colors":
            continue
        for key in block:
            if section != "theme" or key not in META_KEYS:
                dropped += 1
    if dropped:
        warnings.append(f"{name}: dropped {dropped} unknown key(s) on save")

    gaps = [slot for slot in SLOTS if slot not in slots]
    if gaps:
        warnings.append(f"{name}: {len(gaps)} slot(s) have no value, "
                        f"shown grey until you save: {', '.join(gaps)}")
    for slot in gaps:
        slots[slot] = MISSING
    return slots


def list_themes() -> list:
    """`(name, path, mtime)` for every theme file, sorted by name.

    A file whose stem is not a legal theme name (§13.3) is not listed: it
    could never be opened, so a row for it would be a lie. Rename it.
    """
    try:
        entries = sorted(os.listdir(themes_dir()))
    except OSError:
        return []
    out = []
    for entry in entries:
        if not entry.endswith(SUFFIX):
            continue
        name = entry[:-len(SUFFIX)]
        if not valid_name(name):
            continue
        path = os.path.join(themes_dir(), entry)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        out.append((name, path, mtime))
    return out


# --------------------------------------------------------------------------
# the terminal side (§6, §13.6)
# --------------------------------------------------------------------------

def read_terminal(fmt: str, path: str) -> dict:
    """The slots a terminal config holds right now — an import source."""
    return FORMATS[fmt]["read"](path)


def ghostty_native_path(name: str) -> str:
    """Where `export_ghostty_native` puts `name`: Ghostty's own theme dir."""
    _check(name)
    return os.path.join(ghostty_themes_dir(), name)


def _ghostty_template() -> str:
    """A theme file with every key Ghostty knows, in Ghostty's dialect.

    The placeholders are real colours, not blanks: the file is written to
    a sibling tmp, the values are spliced in by the *same* line-level
    writer a push uses, and only then is it renamed over the target. So a
    reader sees a finished file or the old one, and what it reads back is
    exactly what went in.
    """
    lines = [GHOSTTY_HEADER]
    lines += [f"palette = {index}=#000000" for index in range(16)]
    lines += [f"{slot} = #000000" for slot in SLOTS[16:]]
    return "\n".join(lines) + "\n"


def export_ghostty_native(name: str, slots: dict) -> str:
    """Write the theme as a Ghostty theme file; returns its path (§13.6).

    A theme file of ours, in the terminal's own directory, in the terminal's
    own flat dialect — all 22 slots, palette then named, the same
    `palette = 0=#…` spelling the config uses. Ghostty reads it like any
    other config (plan appendix A), and huebox reads it back through
    `read_flat`, so round-tripping is true by construction rather than by a
    test's goodwill. The file is named after the theme it holds, which is
    what keeps one theme's colours out of another theme's file.

    The name is a theme name (§13.3) because it becomes a file name in a
    directory huebox does not own; an illegal one raises before anything
    is written. A slot the buffer has no value for is written as the same
    `MISSING` grey a push sends, so the file and the terminal agree.
    """
    _check(name)
    path = ghostty_native_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(_ghostty_template())
        FORMATS["ghostty"]["write"](tmp, {slot: _value(slots, slot)
                                          for slot in SLOTS})
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)          # never leave a half-written theme behind
        except OSError:
            pass
        raise
    return path


#: What one `push` did. The caller routes `lines` (they are its report) and
#: the verdict: `failed` is the exit code, `pushed` the `fmt: path` pairs a
#: status line names, `reloaded` the formats whose terminal was asked to
#: re-read its config. A plain list of strings could not say which target
#: failed without the caller re-reading the text — nor which terminals are
#: already showing the push, which is what decides whether the caller still
#: owes the user a "reload your terminal".
PushResult = namedtuple("PushResult", "lines pushed failed reloaded",
                        defaults=((),))


#: How a terminal is told to re-read its config (§13.6). Only terminals
#: with a real interface are here: kitty answers its own remote control,
#: ghostty is asked with a signal, and a format that cannot be told keeps
#: the advice line ("press ctrl+shift+,"). Nothing here may fail a save —
#: the colours are already written by the time a reload is attempted.
RELOAD_COMMANDS = {
    "kitty": ("kitty", "@", "load-config"),
}


#: Where the reload looks for a running terminal. A constant so a test
#: can hand it a directory of fake processes instead of the real machine.
_PROC = "/proc"


def ghostty_app_pid() -> int:
    """The running Ghostty application process, or `None`.

    Ghostty has no CLI action for a config reload in 1.3 (`+reload_config`
    is a *keybind* action, and the desktop file only offers `new-window`),
    but its application does handle `SIGUSR2` as "reload the
    configuration" — the same thing `ctrl+shift+,` does. So the process is
    found the only way it can be: by walking `/proc` for a process named
    `ghostty` and reading its command line. `--gtk-single-instance` is the
    application, which is the process that owns the signal handler; the
    per-window surfaces, if a build has them, are not signalled.
    """
    proc = _PROC
    try:
        entries = os.listdir(proc)
    except OSError:
        return None
    for entry in sorted(entries, key=lambda name: name.zfill(8)):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc, entry, "comm"),
                      encoding="utf-8", errors="replace") as handle:
                if handle.read().strip() != "ghostty":
                    continue
            with open(os.path.join(proc, entry, "cmdline"), "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "ghostty" in cmdline and "single-instance" in cmdline:
            return int(entry)
    return None


def reload_terminal(fmt: str) -> str:
    """Ask `fmt`'s terminal to re-read its config; the report line, or "".

    Best effort by contract: a terminal that is not there, a signal that
    lands nowhere and a command that is not installed are all the same
    event to the user — the colours are on disk either way — so this
    returns "" and the caller keeps the "reload your terminal" advice.
    """
    if fmt == "ghostty":
        pid = ghostty_app_pid()
        if pid is None:
            return ""
        try:
            os.kill(pid, signal.SIGUSR2)
        except OSError:
            return ""
        return f"ghostty: reloaded (config re-read, pid {pid})"
    command = RELOAD_COMMANDS.get(fmt)
    if not command:
        return ""
    try:
        # no tty in any of the three: a reload that stole a keystroke or
        # printed into the editor's frame would be worse than no reload
        done = subprocess.run(list(command), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5,
                              check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    if done.returncode:
        return ""
    return f"{fmt}: reloaded ({' '.join(command)})"


def _target_names(to, fmt) -> list:
    """The formats a push aims at, in order.

    `--to` (a comma list or a list) wins; otherwise the caller's forced
    format, otherwise `None` — the sentinel that means "whatever terminal
    this is", which only today's `resolve()` can answer.
    """
    if to:
        wanted = to.split(",") if isinstance(to, str) else list(to)
        names = []
        for part in wanted:
            name = str(part).strip()
            if not name:
                continue
            if name not in FORMATS:
                raise ThemeError(f"unknown format {name!r} "
                                 f"(choose from {', '.join(FORMAT_NAMES)})")
            if name not in names:
                names.append(name)
        if not names:
            raise ThemeError("--to needs at least one format")
        return names
    return [fmt or None]


def _theme_file_for(name: str) -> str:
    """The file `theme = name` means: Ghostty's own themes dir, same value."""
    return os.path.join(ghostty_themes_dir(), name)


def _export_or_edit(found: str, target: str, name: str, main: str,
                    force: bool, in_place: bool) -> bool:
    """Should this Ghostty target be exported as a theme file? (§13.6)

    The rule is one sentence: **a theme's colours never land in a file that
    belongs to another theme.** So when the config is organised by theme —
    its `theme =` line names the file the colours live in — the save is
    exported under the theme's own name and the pointer is repointed, and
    the file named by the old pointer is left exactly as it was. When the
    config holds its colours inline (or in an include), there is no theme
    file in the picture to confuse, so the colours are edited where they
    already are and the config's layout is none of huebox's business.

    `force` is `--ghostty-native`: export even where the colours are
    inline, which is the one way a push adds a `theme =` line. `in_place`
    is `--ghostty-in-place`: never export.

    The colours being written are the test, not the config's shape: the
    config is organised by theme when the file in front of us is either
    the theme the pointer names or the config that carries the pointer
    (the `--config` seam hands back the latter verbatim, §7.1, and the
    colours are still behind the pointer).
    """
    if found != "ghostty" or not name:
        return False
    if in_place:
        return False
    if force or not main:
        return True
    named = ghostty_theme_name(main)
    if not named:
        return False
    here = os.path.realpath(target)
    return here in (os.path.realpath(_theme_file_for(named)),
                    os.path.realpath(main))


def _foreign_theme(target: str, name: str, existing: dict, slots: dict):
    """Complain when `target` is *another* theme's file, else `None`.

    The last guard under the in-place path, and the one that makes the
    rule above an invariant rather than a default: `--ghostty-in-place`
    on a config that points at a theme file is refused whenever the file
    is named after a different theme and the push would actually change
    it. Colours that match already are not a change, so a save that
    changes nothing is still allowed through.
    """
    if not name or os.path.basename(target) == name:
        return None
    directory = os.path.dirname(os.path.realpath(target))
    if directory != os.path.realpath(ghostty_themes_dir()):
        return None                       # inline config or an include
    carried = [slot for slot in SLOTS if slot in existing]
    if not any(_value(slots, slot) != existing[slot] for slot in carried):
        return None                       # would write nothing anyway
    return (f"refusing to write theme {name!r} into {target}: that is the "
            f"theme file for {os.path.basename(target)!r}, and a save must "
            f"never cross two themes' names - drop --ghostty-in-place to "
            f"export {name!r} as its own theme file")


def _dangling_pointer(main: str):
    """The theme a config points at that is not on disk, else `None`.

    §7.3 refuses a config with no colours in its chain, and that stands.
    But a `theme =` line naming a file that is *missing* is a different
    thing: the chain is broken, not colourless, and Ghostty itself calls it
    a configuration error on reload. The export is the one thing that can
    put the colours back, so a dangling pointer is a target rather than a
    refusal — and the report says the file was missing, because that is
    what the user was living with.
    """
    if not main:
        return None
    named = ghostty_theme_name(main)
    if not named or os.path.isfile(_theme_file_for(named)):
        return None
    return named


def _push_native(name: str, slots: dict, main: str, dangling=None):
    """Export a Ghostty theme file, then point the main config at it.

    Returns `(path, lines, failed)`: the exported file the status line
    names, the report, and whether the target failed. Neither write is
    rolled back if the other one misses (decision 7) — a theme file
    without a pointer is a harmless file, and the report says so.

    The order is truth → file → pointer, and a main config that carried
    colours of its own is *not* erased: the pointer is appended at the
    end, so the theme file is the last word on colours for as long as the
    pointer is there. The report says those colours are now shadowed,
    because a colour the user cannot see is the one thing a push must
    not do quietly.
    """
    try:
        path = export_ghostty_native(name, slots)
        shadowed = bool(FORMATS["ghostty"]["read"](main))
        action = ensure_theme_pointer(main, name)
    except (ThemeError, OSError, ValueError) as problem:
        return None, [f"ghostty: {main}: {problem}"], True
    lines = [f"ghostty: exported {path}",
             f"ghostty: theme = {name} {action} in {main}"]
    if dangling:
        lines.append(f"ghostty: {main} pointed at {dangling!r}, which was "
                     f"not on disk - the colours are back in a file of that "
                     f"name now")
    if shadowed:
        lines.append(f"ghostty: the colours in {main} are now shadowed by "
                     f"{path} - remove the inline ones to change them again")
    return path, lines, False


def push(slots: dict, to=None, fmt: str = None, path: str = None,
         no_push: bool = False, ghostty_native: bool = False,
         name: str = None, ghostty_in_place: bool = False,
         reload: bool = False) -> PushResult:
    """Write a saved buffer into the terminal config(s) that hold colours.

    Two paths, and for Ghostty the config decides which (§13.6):

    - **The export** (default whenever the target is a Ghostty theme):
      write `~/.config/ghostty/themes/<name>` and point the main config at
      it. The theme's colours land in the theme's own file, the file the
      pointer used to name is left alone, and the pointer is the one line
      that moves.
    - **The in-place write**: splice the 22 slots into the file that
      already holds the colours, through that format's own line-level
      writer, so the §6.2 contract holds for every byte that lands
      (decision 9). One writer, one promise.

    A Ghostty target is exported when its config is organised by theme —
    the `theme =` line names the file the colours live in — and edited
    in place when the colours are inline or in an include, because then
    no theme's name is in play. `--ghostty-native` forces the export
    (adding the pointer where there is none), `--ghostty-in-place` forces
    the edit. Either way the rule holds in both: a theme's colours are
    never written into a file that belongs to another theme
    (`_foreign_theme` refuses the one case that could).

    Every target goes through today's `resolve()` — the file the colours
    actually live in, whether that is the config itself, a Ghostty
    `config-file` include or the file a `theme =` points at — so the
    reader and the writer always agree about which file this is.

    Three rules the terminal side does not get to negotiate:

    - A config with no colours is never a target, exactly as in detection
      (§7.3): a stale env var must not turn into a rewritten config.
    - Keys the target does not carry are reported, never inserted
      (`not carried by this config: …`). Adding a line would break the
      line-level contract, and open question 1 is still open. An export
      has no such report: its file carries all 22 by construction.
    - Every target is tried even when an earlier one failed; `failed` is
      the caller's exit code, and the truth file written before this call
      is never rolled back (decision 7).

    `path` is an explicit config (the `--config` seam) and only makes sense
    for a single target — several targets with one file is a CLI error.
    With an export, `path` names the main config to point at.

    `reload` asks each terminal that took a push to re-read its config, so
    a save ends with the terminal already showing the colours; the formats
    that managed it come back in `PushResult.reloaded`, and the ones that
    did not are the caller's cue to keep saying which key to press. It is
    `False` by default so a programmatic push never reaches for a signal or
    a subprocess nobody asked about; the command line turns it on.
    """
    if no_push:
        return PushResult((), (), False)

    names = _target_names(to, fmt)
    if path is not None and len(names) != 1:
        raise ValueError(f"an explicit config path pushes exactly one "
                         f"target, got {len(names)} target names")
    explicit = path if len(names) == 1 else None
    lines: list = []
    pushed: list = []
    failed = False

    for wanted in names:
        found, target, error = resolve(wanted, explicit)
        label = wanted or "terminal"
        # The config that holds the `theme =` line, asked for before the
        # colours: a dangling pointer is a target even though nothing
        # resolves, because the export is what repairs it (§7.3).
        main = None
        if (wanted or "ghostty") == "ghostty":
            main = ghostty_main_config(explicit)
        dangling = _dangling_pointer(main)
        if error or not target or not found:
            if dangling and name and not ghostty_in_place:
                found, target = "ghostty", main
            elif dangling:
                # the one state where "no colours found" is the wrong
                # answer to give: say what is missing and what to do
                advice = ("save the buffer as a theme (N) and huebox will "
                          "export it" if not name else
                          "drop --ghostty-in-place to have huebox export it")
                lines.append(f"{label}: {main} points at theme "
                             f"{dangling!r}, which is not on disk - {advice}")
                failed = True
                continue
            else:
                # `--config` with a path that says neither format gets here
                # too: §7.1 makes that an error, not a coin toss
                reason = error or (f"cannot tell which format {target} is - "
                                   f"pass --format with one of "
                                   f"{', '.join(FORMAT_NAMES)}")
                lines.append(f"{label}: {reason}")
                failed = True
                continue
        elif found == "ghostty":
            main = main or ghostty_main_config(explicit)
        if _export_or_edit(found, target, name, main, ghostty_native,
                           ghostty_in_place):
            if main is None:
                lines.append("ghostty: no ghostty config to point at "
                             "(no config.ghostty or config found)")
                failed = True
                continue
            if not (dangling or config_holds_colours("ghostty", main)):
                lines.append(f"ghostty: no colours in {main} "
                             "- not a push target")
                failed = True
                continue
            path_, extra, bad = _push_native(name, slots, main, dangling)
            lines.extend(extra)
            if bad:
                failed = True
                continue
            pushed.append((found, path_))
            continue
        existing = FORMATS[found]["read"](target)
        if not existing:
            lines.append(f"{found}: no colours in {target} - not a push target")
            failed = True
            continue
        foreign = _foreign_theme(target, name, existing, slots)
        if foreign:
            lines.append(f"ghostty: {foreign}")
            failed = True
            continue
        try:
            FORMATS[found]["write"](target, slots)
        except OSError as problem:
            lines.append(f"{found}: {target}: {problem}")
            failed = True
            continue
        pushed.append((found, target))
        lines.append(f"{found}: pushed to {target}")
        missing = [slot for slot in SLOTS if slot not in existing]
        if missing:
            lines.append(f"{found}: not carried by this config: "
                         f"{', '.join(missing)}")

    reloaded: list = []
    if reload:
        for hit, _target in pushed:
            note = reload_terminal(hit)
            if note:
                lines.append(note)
                reloaded.append(hit)

    return PushResult(tuple(lines), tuple(pushed), failed, tuple(reloaded))


# --------------------------------------------------------------------------
# state.toml (§13.1, §13.4)
# --------------------------------------------------------------------------

def current():
    """The theme `state.toml` points at, or `None`.

    A missing, unreadable, corrupt or nonsense state file all read as
    `None`: the caller falls back to the v1 direct mode, and the next
    `set_current` rewrites the file whole. `rm`/`mv` on theme files keep
    working because the state is only a pointer, never a cache.
    """
    sections, _ = _read_file(state_path())
    name = sections.get("", {}).get("current")
    return name if valid_name(name) else None


def set_current(name: str) -> None:
    """Point the state at `name`; rewrites the file even if it was corrupt."""
    _check(name)
    os.makedirs(home(), exist_ok=True)
    _atomic_write(state_path(), f'current = "{name}"\n')
