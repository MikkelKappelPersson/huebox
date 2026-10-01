"""ANSI output, layout primitives, samples and the static preview (§8).

This module is pure: everything renders from the passed-in slots and size,
never from the terminal itself. That keeps the §15 layout tests simple.
"""

from __future__ import annotations

import unicodedata

from .color import MISSING, NAMED, hex_to_rgb, readable_fg

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def fg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\033[38;2;{r};{g};{b}m"


def bg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\033[48;2;{r};{g};{b}m"


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
    # cli always passes the live width; 96 stays the piped default.
    cols = cols or 96
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
