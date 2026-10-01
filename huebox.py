#!/usr/bin/env python3
"""huebox - a terminal theme editor.

Shows, and lets you edit, the colour theme of the terminal you are running in.
Works with Ghostty, Kitty, Alacritty and WezTerm. No third-party dependencies;
Pygments is used for the live code preview when it happens to be installed.

    huebox              TUI when stdout is a terminal, otherwise a static preview
    huebox edit         force the interactive editor
    huebox show         force the static preview
    huebox --dump       print the resolved colours and exit
"""

from __future__ import annotations

import argparse
import colorsys
import os
import re
import shutil
import sys
import unicodedata

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# the canonical colour model, shared by every supported terminal
# --------------------------------------------------------------------------

PALETTE = [f"palette-{i}" for i in range(16)]
NAMED = ["background", "foreground", "cursor-color", "cursor-text",
         "selection-background", "selection-foreground"]
SLOTS = PALETTE + NAMED

MISSING = "#808080"


# --------------------------------------------------------------------------
# colour maths
# --------------------------------------------------------------------------

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def rgb_to_hsv(rgb):
    return colorsys.rgb_to_hsv(*[c / 255 for c in rgb])


def hsv_to_rgb(h, s, v):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h, s, v))


def luminance(value: str) -> float:
    r, g, b = hex_to_rgb(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_fg(value: str) -> str:
    """Black or white, whichever stays legible on `value`."""
    return "#000000" if luminance(value) > 140 else "#ffffff"


def is_hex(value: str) -> bool:
    return bool(re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()))


def normalize_hex(value: str) -> str:
    return "#" + value.strip().lstrip("#").lower()


# --------------------------------------------------------------------------
# ANSI output helpers
# --------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def fg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\033[38;2;{r};{g};{b}m"


def bg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\033[48;2;{r};{g};{b}m"


def term_size(default=(80, 24)) -> tuple[int, int]:
    """Terminal size, never returning a non-positive value.

    The layout truncates to `cols`, so a bogus 0 would blank the whole UI.
    """
    try:
        size = os.get_terminal_size()
        cols, rows = size.columns, size.lines
    except OSError:
        cols, rows = 0, 0
    if cols <= 0 or rows <= 0:
        cols, rows = default
    return cols, rows


def clip(text: str, cols: int) -> str:
    """Truncate to `cols` display columns, ignoring escapes and wide glyphs."""
    if cols <= 0:
        return ""
    out, seen, i, n = [], 0, 0, len(text)
    while i < n and seen < cols:
        ch = text[i]
        if ch == "\033":
            j = i + 1
            while j < n and text[j] not in "mABCDEFGHJKSTfmnsulh":
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        out.append(ch)
        if unicodedata.combining(ch):
            i += 1
            continue
        seen += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        i += 1
    return "".join(out) + RESET if seen >= cols else "".join(out)


def pack(items, cols: int, sep: str = "   ") -> list[str]:
    """Greedily pack short items into lines that fit `cols`."""
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = item if not current else current + sep + item
        if len(candidate) > cols and current:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# format definitions
#
# Every format is described by a list of rules. A rule is
#
#     (compiled_pattern, resolver, rewriter)
#
# where `resolver(match)` names the canonical slot the line carries and
# `rewriter(line, match, value)` returns the line with a new colour spliced
# in. Reading only needs the resolver; writing needs both, which is what
# lets us edit files in place without disturbing comments or spacing.
# --------------------------------------------------------------------------

Rule = tuple


def _palette_resolver(match) -> str:
    return f"palette-{match.group('index')}"


def _named_resolver(match, mapping=None) -> str:
    name = match.group("name")
    return (mapping or {}).get(name, name.replace("_", "-"))


def _palette_rewriter(match) -> str:
    return f"{match.group('pre')}{match.group('index')}" \
           f"{match.group('mid')}#{{value}}"


def _named_rewriter(match) -> str:
    return f"{match.group('pre')}{match.group('name')}{match.group('mid')}#{{value}}"


def _palette_rule(pattern):
    """Rule for `key<N> = #hex` (and the space-separated kitty spelling)."""
    return (pattern,
            lambda m: f"palette-{m.group('index')}",
            lambda line, m, v: line[:m.start("val")] + v + line[m.end("val"):])


def _named_rule(pattern, mapping=None):
    """Rule for `name = #hex`, mapping terminal spellings to canonical slots."""
    return (pattern,
            lambda m: _named_resolver(m, mapping),
            lambda line, m, v: line[:m.start("val")] + v + line[m.end("val"):])


HEX = r"#?[0-9a-fA-F]{6}\b"

GHOSTTY_RULES = [
    _palette_rule(re.compile(
        r"^(?P<pre>\s*palette\s*=\s*)(?P<index>\d+)(?P<mid>\s*=\s*)" + "(?P<val>" + HEX + ")",
        re.IGNORECASE)),
    _named_rule(re.compile(
        r"^(?P<pre>\s*)(?P<name>background|foreground|cursor-color|cursor-text|"
        r"selection-background|selection-foreground)(?P<mid>\s*=\s*)" + "(?P<val>" + HEX + ")",
        re.IGNORECASE)),
]

KITTY_RULES = [
    _palette_rule(re.compile(
        r"^(?P<pre>\s*color)(?P<index>\d+)(?P<mid>\s*=?)\s*" + "(?P<val>" + HEX + ")",
        re.IGNORECASE)),
    _named_rule(re.compile(
        r"^(?P<pre>\s*)(?P<name>background|foreground|cursor|"
        r"selection_background|selection_foreground)(?P<mid>\s*=?)\s*" + "(?P<val>" + HEX + ")",
        re.IGNORECASE),
        {"cursor": "cursor-color",
         "selection_background": "selection-background",
         "selection_foreground": "selection-foreground"}),
]

# --- TOML (Alacritty) -------------------------------------------------------

ALACRITTY_PATHS = {
    "colors.primary.background": "background",
    "colors.primary.foreground": "foreground",
    "colors.cursor.text": "cursor-text",
    "colors.cursor.cursor": "cursor-color",
    "colors.selection.background": "selection-background",
    "colors.selection.foreground": "selection-foreground",
    **{f"colors.normal.{i}": f"palette-{i}" for i in range(16)},
}

PATH_SLOT = {slot: path for path, slot in ALACRITTY_PATHS.items()}

_TOML_PAIR = re.compile(r"(?P<key>[A-Za-z0-9_.\-]+|\"[A-Za-z0-9_.\-]+\")"
                        r"\s*=\s*(?P<val>\"[^\"]*\"|'[^']*'|#[0-9a-fA-F]{6})")
_TOML_INLINE = re.compile(r"^\s*(?P<prefix>[A-Za-z0-9_.\-]+)\s*=\s*\{")


def _toml_key(text: str) -> str:
    return text.strip().strip("\"'")


def toml_entries(path: str):
    """Yield (index, slot, value, start, end) for every colour in a TOML file.

    Covers the three spellings Alacritty configs actually use:
        colors.primary.background = "#0f0f1a"                  dotted
        [colors.primary]\nbackground = "#0f0f1a"              table
        [colors]\nprimary = { background = "#0f0f1a" }         inline table
    """
    section = ""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return
    for index, line in enumerate(lines):
        header = re.match(r"^\s*\[([^\]]+)\]", line)
        if header:
            section = _toml_key(header.group(1))
            continue
        if not is_hex(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line)) and "#" not in line:
            continue
        inline = _TOML_INLINE.match(line)
        prefix = _toml_key(inline.group("prefix")) if inline else ""
        for match in _TOML_PAIR.finditer(line):
            raw = match.group("val")
            token = raw.strip("\"'")
            if not is_hex(token):
                continue
            key = _toml_key(match.group("key"))
            candidates = [key]
            if prefix:
                candidates.append(f"{section}.{prefix}.{key}" if section
                                  else f"{prefix}.{key}")
            elif section:
                candidates.append(f"{section}.{key}")
            for candidate in candidates:
                if candidate in ALACRITTY_PATHS:
                    yield (index, ALACRITTY_PATHS[candidate], raw,
                           match.start("val"), match.end("val"))
                    break


def read_toml(path: str) -> dict:
    return {slot: normalize_hex(value.strip("\"'"))
            for _, slot, value, _, _ in toml_entries(path)}


def write_toml(path: str, slots: dict) -> None:
    """Replace each colour's value token, keeping quotes and spacing intact."""
    lines = open(path, encoding="utf-8").read().splitlines()
    for index, slot, token, start, end in list(toml_entries(path)):
        if slot not in slots:
            continue
        replacement = (f'"{slots[slot]}"'
                       if token[:1] in ('"', "'") else slots[slot])
        line = lines[index]
        lines[index] = line[:start] + replacement + line[end:]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


# --- dispatch ---------------------------------------------------------------

def read_flat(path: str, rules) -> dict:
    slots: dict[str, str] = {}
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return slots
    for line in lines:
        for pattern, resolver, _ in rules:
            match = pattern.match(line)
            if match:
                slots[resolver(match)] = normalize_hex(match.group("val"))
                break
    return slots


def write_flat(path: str, slots: dict, rules) -> None:
    lines = open(path, encoding="utf-8").read().splitlines()
    for index, line in enumerate(lines):
        for pattern, resolver, rewriter in rules:
            match = pattern.match(line)
            if match:
                slot = resolver(match)
                if slot in slots:
                    lines[index] = rewriter(line, match, slots[slot])
                break
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


FORMATS = {
    "ghostty": {
        "read": lambda p: read_flat(p, GHOSTTY_RULES),
        "write": lambda p, s: write_flat(p, s, GHOSTTY_RULES),
        "defaults": ["~/.config/ghostty/config.ghostty",
                     "~/.config/ghostty/config"],
        "env": {},
    },
    "kitty": {
        "read": lambda p: read_flat(p, KITTY_RULES),
        "write": lambda p, s: write_flat(p, s, KITTY_RULES),
        "defaults": ["~/.config/kitty/kitty.conf", "~/.kitty.conf"],
        "env": {"KITTY_CONFIG_DIR": "kitty.conf"},
    },
    "alacritty": {
        "read": read_toml,
        "write": write_toml,
        "defaults": ["~/.config/alacritty/alacritty.toml",
                     "~/.config/alacritty/alacritty.yml",
                     "~/.alacritty.toml"],
        "env": {"ALACRITTY_CONFIG_DIR": "alacritty.toml",
                "ALACRITTY_CONFIG": "alacritty.toml"},
    },
}

FORMAT_NAMES = list(FORMATS)


def _config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _prefer_xdg(paths: list[str]) -> list[str]:
    """Put the XDG location first so tests and sandboxes are respected."""
    out: list[str] = []
    base = _config_home()
    for path in paths:
        if path.startswith("~/.config/"):
            out.append(os.path.join(base, path[len("~/.config/"):]))
        out.append(path)
    return out


for _spec in FORMATS.values():
    _spec["defaults"] = _prefer_xdg(_spec["defaults"])

FORMATS["kitty"]["defaults"] = [os.path.join(_config_home(), "kitty/kitty.conf")] \
    + FORMATS["kitty"]["defaults"]


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _probes():
    """Terminals to try, most trustworthy first.

    Per-window variables beat TERM_PROGRAM, because launchers and
    multiplexers leak stale values into the environment.
    """
    return [
        ("ghostty", lambda: os.environ.get("GHOSTTY_RESOURCES_DIR")),
        ("kitty", lambda: os.environ.get("KITTY_WINDOW_ID")
         or os.environ.get("KITTY_LISTEN_ON")),
        ("alacritty", lambda: os.environ.get("ALACRITTY_WINDOW_ID")
         or os.environ.get("ALACRITTY_SOCKET")),
        ("wezterm", lambda: os.environ.get("WEZTERM_EXECUTABLE")
         or os.environ.get("WEZTERM_PANE")),
    ]


def detect_order() -> list[str]:
    names = [name for name, probe in _probes() if probe()]
    program = os.environ.get("TERM_PROGRAM", "").lower()
    names += [name for name in FORMAT_NAMES if name in program and name not in names]
    names += [name for name in FORMAT_NAMES if name not in names]
    return names


def _candidate_paths(fmt: str):
    spec = FORMATS[fmt]
    for var, filename in spec["env"].items():
        value = os.environ.get(var)
        if not value:
            continue
        base = value if os.path.isdir(value) else os.path.dirname(value) or "."
        candidate = os.path.join(base, filename) if filename else value
        if os.path.isfile(candidate):
            yield candidate
    for candidate in spec["defaults"]:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            yield expanded


def _ghostty_includes(config_path: str) -> list[str]:
    """Config files pulled in via `config-file = ?path`."""
    out = []
    directory = os.path.dirname(os.path.abspath(config_path))
    try:
        lines = open(config_path, encoding="utf-8",
                     errors="replace").read().splitlines()
    except OSError:
        return out
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*config-file\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        target = match.group(1).strip().strip("\"'")
        target = (target.replace("?", directory + os.sep)
                        .replace("~", os.path.expanduser("~")))
        if os.path.isfile(target):
            out.append(target)
    return out


def _ghostty_theme_file(config_path: str):
    """Follow `theme = Name` to the file that actually holds the colours.

    Ghostty keeps colours in a separate theme file far more often than
    inline, so the main config on its own usually has nothing to edit.
    """
    name = None
    try:
        lines = open(config_path, encoding="utf-8",
                     errors="replace").read().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = re.match(r"\s*theme\s*=\s*(.+?)\s*$", line)
        if match:
            name = match.group(1).strip().strip("\"'")
    if not name:
        return None
    user = os.path.join(_config_home(), "ghostty", "themes", name)
    if os.path.isfile(user):
        return user
    resources = os.environ.get("GHOSTTY_RESOURCES_DIR", "/usr/share/ghostty")
    shipped = os.path.join(resources, "themes", name)
    if os.path.isfile(shipped):
        # a read-only shipped theme: copy somewhere we are allowed to edit
        os.makedirs(os.path.dirname(user), exist_ok=True)
        shutil.copy2(shipped, user)
        return user
    return None


def _resolve_for(fmt: str):
    """First config for `fmt` that actually defines colours."""
    read = FORMATS[fmt]["read"]
    for candidate in _candidate_paths(fmt):
        if fmt == "ghostty":
            for included in _ghostty_includes(candidate):
                if read(included):
                    return included
            themed = _ghostty_theme_file(candidate)
            if themed:
                candidate = themed
        if read(candidate):
            return candidate
    return None


def resolve(fmt: str | None = None, path: str | None = None):
    """Return (format, config_path, error)."""
    if fmt and fmt not in FORMATS:
        return None, None, f"unknown format {fmt!r}"
    if path:
        expanded = os.path.expanduser(path)
        if not os.path.isfile(expanded):
            return fmt, None, f"no such file: {expanded}"
        return fmt, expanded, None

    order = [fmt] if fmt else detect_order()
    for name in order:
        found = _resolve_for(name)
        if found:
            return name, found, None
    if fmt:
        return fmt, None, f"no {fmt} config with colours found"
    return None, None, ("found no terminal config with colours; "
                        "pass --format with one of "
                        f"{', '.join(FORMAT_NAMES)}")


# --------------------------------------------------------------------------
# live code sample
# --------------------------------------------------------------------------

SAMPLE_LINES = [
    ("// live preview - edits show here without reloading", None),
    ("const huebox = \"terminal theme editor\";", "zig"),
    ("", None),
    ("pub fn main() !void {", "zig"),
    ("    var count: u32 = 42;   // your palette", "zig"),
    ("    if (count > 0xFF) {", "zig"),
    ("        std.debug.print(\"{d} colours\\n\", .{count});", "zig"),
    ("    }", "zig"),
]


def _slot_for_token(token, slots) -> str:
    if token is None:
        return "foreground"
    mapping = [("Comment", "palette-8"), ("Keyword", "palette-5"),
               ("Name.Decorator", "palette-11"), ("Name.Builtin", "palette-6"),
               ("Name.Class", "palette-4"), ("Name.Function", "palette-4"),
               ("Name.Namespace", "palette-6"), ("String", "palette-2"),
               ("Char", "palette-2"), ("Number", "palette-3"),
               ("Operator", "palette-5"), ("Name.Exception", "palette-1"),
               ("Generic", "palette-11"), ("Punctuation", "foreground"),
               ("Error", "palette-1")]
    try:
        from pygments.token import Token
    except Exception:
        return "foreground"
    for name, slot in mapping:
        probe = Token
        for part in name.split("."):
            probe = getattr(probe, part, None)
            if probe is None:
                break
        if probe is not None and token in probe:
            return slot
    return "foreground"


_sample_cache = None


def sample_lines(slots):
    """Tokenise the sample once, then colour it from the live slot values."""
    global _sample_cache
    if _sample_cache is None:
        try:
            from pygments import lex
            from pygments.lexers import get_lexer_by_name
            source = "\n".join(text for text, _ in SAMPLE_LINES)
            _sample_cache = list(lex(source, get_lexer_by_name("zig")))
        except Exception:
            _sample_cache = None
    if not _sample_cache:
        return [(fg(slots.get("foreground", "#ededfe")) + text + RESET, 0)
                for text, _ in SAMPLE_LINES]
    out, current = [], None
    for token, text in _sample_cache:
        if not text:
            continue
        slot = _slot_for_token(token, slots)
        if slot != current:
            current = slot
            out.append(fg(slots.get(slot, slots.get("foreground", "#ededfe"))))
        out.append(text)
    out.append(RESET)
    body = "".join(out)
    return [(line, 0) for line in body.split("\n")]


# --------------------------------------------------------------------------
# static preview
# --------------------------------------------------------------------------

def render_preview(fmt, path, slots, cols=None, rows=None) -> str:
    cols = cols or (term_size()[0] if sys.stdout.isatty() else 96)
    lines = []
    lines.append(f"{BOLD}huebox{RESET}  {BOLD}{fmt}{RESET}"
                 f"{'  ' + DIM + path + RESET if path and len(path) < cols else ''}")
    lines.append("")

    cell_full, cell_min = 13, 6
    indent = "  "
    for per_row, cellw in ((8, cell_full), (8, cell_min), (4, cell_full),
                           (4, cell_min), (2, cell_full), (1, cell_full)):
        if len(indent) + per_row * cellw <= cols:
            break
    show_hex = cellw >= cell_full
    for start in range(0, 16, per_row):
        cells = []
        for i in range(start, min(start + per_row, 16)):
            value = slots.get(f"palette-{i}", MISSING)
            label = (f" {i:>2} {value} " if show_hex else f" {i:>2}")
            cells.append(f"{bg(value)}{fg(readable_fg(value))}"
                         f"{label.ljust(cellw)}{RESET}")
        lines.append((indent + "".join(cells)).rstrip())
    lines.append(f"{DIM}0-7 base   8-15 bright{RESET}")
    lines.append("")

    pad = max(8, min(21, cols - 13))
    iface = pad + 11
    per = 2 if len(indent) + 2 * iface + 2 <= cols else 1
    for start in range(0, len(NAMED), per):
        cells = []
        for key in NAMED[start:start + per]:
            value = slots.get(key, MISSING)
            cells.append(f"{bg(value)}{fg(readable_fg(value))}"
                         f" {key[:pad]:<{pad}} {value} {RESET}")
        lines.append((indent + "  ".join(cells)).rstrip())
    lines.append("")
    lines.append(f"  {DIM}edit interactively:  huebox edit{RESET}")
    lines.append("")
    return "\n".join(clip(line, cols) for line in lines)


# --------------------------------------------------------------------------
# interactive editor
# --------------------------------------------------------------------------

ADJUST = {
    "q": ("h", -1), "w": ("h", +1),
    "a": ("s", -1), "s": ("s", +1),
    "z": ("v", -1), "x": ("v", +1),
    "h": ("h", -1), "l": ("h", +1),
    "j": ("v", -1), "k": ("v", +1),
    "H": ("s", -1), "L": ("s", +1),
}
MULT_STEPS = [1, 5, 20]


def enter_raw():
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    return fd, saved


def exit_raw(fd, saved):
    import termios
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def read_key(fd):
    """One keypress.

    Two traps this avoids:

    * os.read on the fd, never sys.stdin.read -- sys.stdin is buffered, so it
      would pull a whole escape sequence into userspace and leave select()
      looking at an empty fd, which reads as a lone Escape.
    * read the sequence one byte at a time and stop at its final byte. A
      greedy drain also swallows the *next* keystroke, so pressing an arrow
      twice quickly would arrive as one unmatched sequence.
    """
    import select
    try:
        first = os.read(fd, 1)
    except OSError:
        return "esc"
    if not first:
        return "esc"
    if first != b"\033":
        return first.decode("latin1")

    if not select.select([fd], [], [], 0.05)[0]:
        return "esc"                      # a bare Escape
    lead = os.read(fd, 1)
    if lead == b"O":                     # SS3: OA/OB/OC/OD
        if not select.select([fd], [], [], 0.05)[0]:
            return "esc"
        return {"A": "up", "B": "down", "C": "right",
                "D": "left"}.get(os.read(fd, 1).decode("latin1"), "esc")
    if lead != b"[":
        return "esc"

    seq = b"["
    while select.select([fd], [], [], 0.05)[0]:
        char = os.read(fd, 1)
        if not char:
            break
        seq += char
        if 0x40 <= char[0] <= 0x7E:       # CSI final byte
            break
        if len(seq) > 24:
            break
    return {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
            "[H": "home", "[F": "end"}.get(seq.decode("latin1"), "esc")


def draw_editor(fmt, path, slots, sel, undo, status, mult):
    cols, rows = term_size()
    sys.stdout.write("\033[H\033[2J")
    body = []

    head = f"  {BOLD}huebox{RESET}  {BOLD}{fmt}{RESET}"
    if path and len(path) + len(head) + 2 <= cols:
        head += f"  {DIM}{path}{RESET}"
    body.append(head)
    body.append("")

    cell_full, cell_min = 13, 6
    for per_row, cellw in ((8, cell_full), (8, cell_min), (4, cell_full),
                           (4, cell_min), (2, cell_full), (1, cell_full)):
        if len("  ") + per_row * cellw <= cols:
            break
    show_hex = cellw >= cell_full

    def swatch(index, selected):
        value = slots.get(f"palette-{index}", MISSING)
        mark = ">" if selected else " "
        label = (f" {mark}{index:>2} {value} " if show_hex else f" {mark}{index:>2}")
        return (f"{bg(value)}{fg(readable_fg(value))}"
                f"{BOLD if selected else ''}{label.ljust(cellw)}{RESET}")

    body.append(f"  {BOLD}palette{RESET}")
    for start in range(0, 16, per_row):
        body.append(("  " + "".join(
            swatch(i, sel == i) for i in range(start, start + per_row)
            if i < 16)).rstrip())
    body.append(f"  {DIM}0-7 base   8-15 bright{RESET}")
    body.append("")

    per = 2 if len("  ") + 2 * 32 + 2 <= cols else 1
    body.append(f"  {BOLD}interface{RESET}")
    for start in range(0, len(NAMED), per):
        cells = []
        for key in NAMED[start:start + per]:
            index = SLOTS.index(key)
            value = slots.get(key, MISSING)
            mark = ">" if sel == index else " "
            style = BOLD if sel == index else ""
            cells.append(f"{bg(value)}{fg(readable_fg(value))}{style}"
                         f" {mark}{key:<21} {value} {RESET}")
        body.append(("  " + "  ".join(cells)).rstrip())
    body.append("")

    key = SLOTS[sel]
    value = slots.get(key, MISSING)
    h, s, v = rgb_to_hsv(hex_to_rgb(value))
    body.append(f"  {BOLD}selected{RESET}  {key}  {value}   {DIM}"
                f"hue {h * 360:5.1f}  sat {s * 100:4.1f}%  val {v * 100:4.1f}%{RESET}")
    body.append(f"    {fg(value)}AaBbCc 0123 {RESET}")
    body.append("")

    tail = [f"  {DIM}{line}{RESET}" for line in pack(
        ["arrows move", "q/w hue", "a/s sat", "z/x light", f"f x{mult}",
         "i hex", "^S save", f"u undo({len(undo)})", "r revert", "Esc quit"],
        cols - 2)]
    if status:
        tail.append(f"  {BOLD}{status}{RESET}")

    extra = []
    budget = rows - len(body) - len(tail)
    if budget >= 4:
        rendered = sample_lines(slots)
        room = min(len(rendered), budget - 2)
        if room >= 1:
            extra.append(f"  {BOLD}live code{RESET} "
                         f"{DIM}(truecolor, no reload needed){RESET}")
            extra.extend("    " + line.replace(RESET, RESET + "    ")
                         for line, _ in rendered[:room])
            extra.append("")

    out = body + extra + tail
    if len(out) > rows:
        out = out[:rows - len(tail)] + tail
    # CRLF: raw mode disables ONLCR, so a bare \n would not reset the column
    sys.stdout.write("\r\n".join(clip(line, cols) for line in out) + "\r\n")
    sys.stdout.flush()


def edit(fmt, path, slots):
    original = dict(slots)
    sel, undo, status = 0, [], ""
    mult = MULT_STEPS[0]

    backup = f"{path}.huebox.bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)

    fd, saved = enter_raw()
    try:
        while True:
            draw_editor(fmt, path, slots, sel, undo, status, mult)
            key = read_key(fd)
            name = SLOTS[sel]
            value = slots.get(name)
            if value is None:
                continue
            hue, sat, val = rgb_to_hsv(hex_to_rgb(value))
            status = ""

            if key in ("esc", "Q", "\x03"):
                break
            elif key in ("up", "down", "left", "right"):
                sel = max(0, min(len(SLOTS) - 1, sel + {
                    "up": -8, "down": 8, "left": -1, "right": 1}[key]))
            elif key in ADJUST:
                channel, direction = ADJUST[key]
                if channel == "h":
                    hue = (hue + direction / 360 * mult) % 1.0
                elif channel == "s":
                    sat = max(0.0, min(1.0, sat + direction * 0.02 * mult))
                else:
                    val = max(0.0, min(1.0, val + direction * 0.02 * mult))
                slots[name] = rgb_to_hex(hsv_to_rgb(hue, sat, val))
                undo.append((name, value))
            elif key == "f":
                mult = MULT_STEPS[(MULT_STEPS.index(mult) + 1) % len(MULT_STEPS)]
                status = f"step size x{mult}"
            elif key == "u":
                if undo:
                    slot, previous = undo.pop()
                    slots[slot] = previous
                    status = f"undid {slot}"
            elif key == "r":
                undo.clear()
                slots = dict(original)
                status = "reverted to original"
            elif key in ("i", "X"):
                exit_raw(fd, saved)
                sys.stdout.write("\r\033[2J\033[H")
                try:
                    typed = input(f"  new hex for {name}: ").strip()
                except EOFError:
                    typed = None
                fd, saved = enter_raw()
                if typed is None:
                    continue
                if is_hex(typed):
                    slots[name] = normalize_hex(typed)
                    undo.append((name, value))
                    status = f"{name} = {slots[name]}"
                else:
                    status = "not a valid 6-digit hex - ignored"
            elif key == "\x13":
                status = "saved"

            FORMATS[fmt]["write"](path, slots)
    finally:
        exit_raw(fd, saved)

    print(f"  updated {path}")
    print(f"  backup of the starting state: {backup}")
    print("  reload your terminal to see the change\n")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

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
        edit(fmt, path, slots)
    else:
        print(render_preview(fmt, path, slots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
