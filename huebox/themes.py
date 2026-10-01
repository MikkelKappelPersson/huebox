"""The theme library: `~/.config/huebox` is the truth (§13).

    home()/themes_dir()/state_path()   where everything lives (§13.1)
    valid_name()                       the one name rule (§13.3)
    create() / save()                  the canonical writer (§13.2)
    load() / list_themes()             the hand-rolled reader for our
                                       TOML subset — no tomllib (3.9)
    current() / set_current()          state.toml, two lines at most
    push()                             truth → terminal, the one write out
    RAMP                               the built-in palette `new` seeds from

Theme files belong to huebox: the writer emits one canonical layout and
replaces the file atomically, so a hand-edited theme only ever loses its
unknown keys, never its structure or its colours. Terminal configs are the
opposite case and keep their line-level writer (§6.2): `push()` goes through
the format registry like every other write and never re-serialises one.

A save is truth first, terminal second, and never the other way round
(decision 7): the theme file outlives any one config, so a push that fails
is reported and left alone — nothing is ever rolled back.
"""

from __future__ import annotations

import os
import re
from collections import namedtuple
from datetime import datetime

from .color import MISSING, SLOTS, is_hex, normalize_hex
from .detect import resolve
from .formats import FORMAT_NAMES, FORMATS

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SUFFIX = ".toml"
META_KEYS = ("name", "created", "modified", "source")
HEADER = "# owned by huebox — values are yours, structure is ours"

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
    tmp = f"{path}.tmp"
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


#: What one `push` did. The caller routes `lines` (they are its report) and
#: the verdict: `failed` is the exit code, `pushed` the `fmt: path` pairs a
#: status line names. A plain list of strings could not say which target
#: failed without the caller re-reading the text.
PushResult = namedtuple("PushResult", "lines pushed failed")


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


def push(slots: dict, to=None, fmt: str = None, path: str = None,
         no_push: bool = False) -> PushResult:
    """Write a saved buffer into the terminal config(s) that hold colours.

    Phase 1 of §13.6: every target goes through today's `resolve()` — the
    file the colours actually live in, whether that is the config itself, a
    Ghostty `config-file` include or the file a `theme =` points at — and
    then through that format's own line-level writer, so the §6.2 contract
    holds for every byte that lands (decision 9). One writer, one promise.

    Three rules the terminal side does not get to negotiate:

    - A config with no colours is never a target, exactly as in detection
      (§7.3): a stale env var must not turn into a rewritten config.
    - Keys the target does not carry are reported, never inserted
      (`not carried by this config: …`). Adding a line would break the
      line-level contract, and open question 1 is still open.
    - Every target is tried even when an earlier one failed; `failed` is
      the caller's exit code, and the truth file written before this call
      is never rolled back (decision 7).

    `path` is an explicit config (the `--config` seam) and only makes sense
    for a single target — several targets with one file is a CLI error.
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

    for name in names:
        found, target, error = resolve(name, explicit)
        label = name or "terminal"
        if error or not target or not found:
            # `--config` with a path that says neither format gets here
            # too: §7.1 makes that an error, not a coin toss
            reason = error or (f"cannot tell which format {target} is - pass "
                               f"--format with one of {', '.join(FORMAT_NAMES)}")
            lines.append(f"{label}: {reason}")
            failed = True
            continue
        existing = FORMATS[found]["read"](target)
        if not existing:
            lines.append(f"{found}: no colours in {target} - not a push target")
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

    return PushResult(tuple(lines), tuple(pushed), failed)


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
