"""ANSI output, layout primitives, samples and the static preview (§8, §14.1).

This module is pure: everything renders from the passed-in slots and size,
never from the terminal itself. That keeps the §15 layout tests simple, and
it is what makes the live-everything property (§14.1) hold by construction —
no colour survives a frame.
"""

from __future__ import annotations

import unicodedata

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

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
    """Map a Pygments token class to the palette slot that colours it."""
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
    """Tokenise the sample once, then colour it from the live slot values.

    Pygments is a declared dependency (spec §9), so the fallback-free
    import sits at module level; the cache keeps the lexing to one pass
    per session — the COLOURS are still read per frame from `slots`, so
    the sample stays live (§14.1).
    """
    global _sample_cache
    if _sample_cache is None:
        _sample_cache = list(lex("\n".join(text for text, _ in SAMPLE_LINES),
                                 get_lexer_by_name("zig")))
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
# live examples strip (§14.1)
# --------------------------------------------------------------------------

EXAMPLE_PHRASE = "The quick brown fox jumps over the lazy dog"
CURSOR_BLOCK = "██"                 # a solid block, one column per glyph
LABEL_WIDTH = 21                    # widest name (20) + one padding column


def example_lines(slots, cols=None):
    """The three live example rows: background, selection, cursor (§14.1).

    Pure like `sample_lines`: every colour is read out of `slots` on each
    call, so a single slot change moves the background, the highlight and
    the cursor on the same frame. `cols` is the width the caller can spend
    (optional): the sample text folds to fit through `pack`, the row is
    finally `clip`ped, so the strip never overflows or wraps badly.
    """
    def value(name):
        return slots.get(name, MISSING)

    # (label, hex shown, fill colour, [(text, slot for bg, slot for fg)])
    rows = [
        ("background", value("background"), value("background"),
         [(EXAMPLE_PHRASE, "background", "foreground")]),
        ("selection-background", value("selection-background"),
         value("selection-background"),
         [("selected text", "selection-background", "selection-foreground")]),
        ("cursor-color", value("cursor-color"), value("background"),
         [(CURSOR_BLOCK, "cursor-color", "cursor-text"),
          ("I", "background", "cursor-text")]),
    ]
    out = []
    for label, label_hex, fill, segments in rows:
        head = f"  {label:<{LABEL_WIDTH}} "
        room = None if cols is None else cols - len(head)
        if room is not None and room - 8 >= 12:
            # wide enough to carry the hex too; drop it before the text
            head += f"{label_hex} "
            room -= len(label_hex) + 1
        plain = "".join(chunk for chunk, _, _ in segments)
        if room is not None:
            # pack keeps whole words: fold instead of cutting mid-word
            plain = (pack(plain.split(), max(4, room - 2), sep=" ")
                     or [""])[0]
        painted = ""
        rest = plain
        for chunk, chunk_bg, chunk_fg in segments:
            # consume the folded text segment by segment: the tail of a long
            # segment falls away with the fold, shorter ones drop out whole
            take, rest = rest[:len(chunk)], rest[len(chunk):]
            if take:
                painted += (f"{bg(value(chunk_bg))}{fg(value(chunk_fg))}"
                            f"{take}{RESET}")
        if room is not None:
            painted += f"{bg(value(fill))}{' ' * max(0, room - len(plain))}{RESET}"
        row = head + painted
        out.append(clip(row, cols) if cols is not None else row)
    return out


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
