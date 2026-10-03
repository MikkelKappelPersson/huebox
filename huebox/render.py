"""ANSI output, layout primitives, samples and the static preview (§8, §14.1).

This module is pure: everything renders from the passed-in slots and size,
never from the terminal itself. That keeps the §15 layout tests simple, and
it is what makes the live-everything property (§14.1) hold by construction —
no colour survives a frame.
"""

from __future__ import annotations

import unicodedata
from itertools import cycle

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

from .color import (MISSING, NAMED, hex_to_rgb, hsv_to_rgb, readable_fg,
                   rgb_to_hex, rgb_to_hsv)

RESET = "\033[0m"
BOLD = "\033[1m"
ESCAPE_END = "mABCDEFGHJKSTfmnsulh"   # SGR and friends: the CSI final byte
# No DIM: §8.1 says the frame's own text is drawn from the buffer, so no
# terminal attribute a theme cannot change appears in it.


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
            while j < n and text[j] not in ESCAPE_END:
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


def visible(text: str) -> int:
    """The display columns `text` occupies — the width `clip` cuts at.

    An SGR escape costs nothing and a wide glyph costs two, exactly as in
    `clip`: `pack` folds with it and `clip` truncates with it, so a line
    measured by one and cut by the other cannot disagree.
    """
    seen, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\033":
            j = i + 1
            while j < n and text[j] not in ESCAPE_END:
                j += 1
            i = j + 1
            continue
        if not unicodedata.combining(ch):
            seen += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        i += 1
    return seen


def pack(items, cols: int, sep: str = "   ") -> list[str]:
    """Greedily pack short items into lines that fit `cols`.

    Widths are display columns with the escapes left out (`visible`), so a
    caller may hand `pack` *painted* items — the frame's key hints are —
    and the fold still lands between items. Folding on raw string length
    would count every colour as columns and cut the line short of the edge.
    """
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = item if not current else current + sep + item
        if visible(candidate) > cols and current:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# frame typography (§8.1)
# --------------------------------------------------------------------------

# The frame's own vocabulary, read out of the live buffer like everything
# else: a key is what you press, a label is what it does, and muted text is
# the furniture around them — a path, a hex, a count. Not one of the three
# is a slot the code sample does not already spend (§8), so the bright half
# is read twice in a frame: once as syntax, once as chrome.
CHROME_KEY = "palette-11"      # `arrows`, `Ctrl+S`: the half you look for
CHROME_LABEL = "palette-14"    # the label beside a key: `arrows` **move**
CHROME_MUTED = "palette-8"     # a path, a hex, hue/sat/val, a counter

# Headers are the exception: a header wears the theme's own `foreground`, in
# bold, and nothing else. A header that took a palette colour would compete
# with the widget it introduces, and `foreground` is the one colour the user
# chose for text. The one place huebox decorates is the wordmark: one
# letter, one colour, the six bright hues in order.
WORDMARK = "huebox"
WORDMARK_SLOTS = ("palette-9", "palette-10", "palette-11", "palette-12",
                  "palette-13", "palette-14")


def chrome(text: str, slot: str, slots, bold: bool = False) -> str:
    """One run of frame text, painted from a slot in the live buffer.

    Everything the frame says about itself comes through here, so a chrome
    slot the buffer moves repaints the chrome on the same frame as
    everything else (§14.1) — and nothing in the frame wears a terminal
    attribute the theme cannot change.
    """
    return f"{BOLD if bold else ''}{fg(slots.get(slot, MISSING))}{text}{RESET}"


def wordmark(slots, text: str = WORDMARK) -> str:
    """The `huebox` wordmark: one letter in one colour, in palette order.

    The frame's only ornament, and deliberately at the top where it is read
    once: six letters, the six bright hues, cycled so a longer word still
    colours. Bold, because it is the loudest thing huebox draws — the theme
    it is drawing is somebody else's.
    """
    painted = (chrome(letter, slot, slots, bold=True)
               for letter, slot in zip(text, cycle(WORDMARK_SLOTS)))
    return "".join(painted)


def title(word: str, slots, note: str = "") -> str:
    """A frame header: the word in the theme's foreground, its aside muted.

    `note` is the parenthetical beside it — `(live buffer: …)`, `(git-style:
    …)` — which is commentary, so it recedes.
    """
    head = chrome(word, "foreground", slots, bold=True)
    return f"{head} {chrome(note, CHROME_MUTED, slots)}" if note else head


def key_hint(slots, key: str, what: str) -> str:
    """`arrows move` for a hint line: the key bright, its label beside it.

    The key is the half the reader is hunting for — it is what the finger
    has to find — so it wears the brightest thing in the line and the label
    reads as its explanation. `pack` folds between whole hints, never
    between a key and the label it belongs to.
    """
    return (f"{chrome(key, CHROME_KEY, slots)} "
            f"{chrome(what, CHROME_LABEL, slots)}")


def hint_line(slots, hints, cols: int, sep: str = "  ") -> list[str]:
    """Hint rows for `hints` as `(key, what)` pairs, folded to `cols`."""
    return pack([key_hint(slots, key, what) for key, what in hints],
                cols, sep=sep)


def backdrop(line: str, slots, cols: int) -> str:
    """One frame row, standing on the buffer's own background (§8.2).

    The editor is a sample of the theme, not a preview beside it: the row
    opens in the fill, reaches `cols` in it, and resets at the end, so
    none of the terminal's own background survives anywhere in the frame.
    The row is clipped first and padded after it, and the pad is measured
    with `visible()` — the width `clip` cuts at — so a row already full of
    colours reaches the edge exactly, no wider and no short.

    **A reset reopens the fill.** SGR 0 clears the background as well as
    the foreground, and a row is full of resets — one at the end of every
    chrome run, and the wordmark is one run per letter. Painting the fill
    once at the row's head would leave every run after the first reset
    sitting on the terminal's own background: the rest of the wordmark, the
    parenthetical beside a header, the gaps between two hints. So every
    reset inside the row is followed by the fill again. Nothing is painted
    over: a run that wants a background of its own paints it right after.

    A row with nothing in it is the floor and is painted once; a row with
    content in it reopens the fill after it. The shape differs because the
    frame's own air and a widget's blank line are the same columns of
    space on screen, and only the paint tells them apart.
    """
    fill = bg(slots.get("background", MISSING))
    row = clip(line, cols).replace(RESET, RESET + fill)
    seen = visible(row)
    pad = " " * max(0, cols - seen)
    if not seen:
        return f"{fill}{pad}{RESET}"
    # the row ends on the fill, unless its own last run already closed it
    return f"{fill}{row}{'' if row.endswith(fill) else fill}{pad}{RESET}"


# --------------------------------------------------------------------------
# the hsv readout (§8.3)
# --------------------------------------------------------------------------

# A bar is a window on its axis, centred on the reading: the hue bar shows
# HSV_HUE_SPAN degrees either side of the slot's own hue at the slot's own
# saturation and value, and saturation and value HSV_AXIS_SPAN either side of
# theirs. The middle cell of every bar is the slot's exact colour, so the
# number printed over it never moves and `q`/`a`/`s`/`z`/`x` slide the window
# under it. Widths are the two rungs of the ladder, all of them odd so the
# middle cell really is the centre.
HSV_HUE_SPAN = 60                    # degrees either side of the reading
HSV_AXIS_SPAN = 0.3                  # the same idea, in saturation/value
HSV_FULL = (15, 9, 9)                # hue, sat, val on a wide row
HSV_COMPACT = (11, 7, 7)             # on a nearly-wide one
HSV_LONG = "hue {:5.1f}  sat {:4.1f}%  val {:4.1f}%"
# `hue `, `  sat `, `  val ` — the labels and the gaps between the bars
HSV_LABELS = 16


def hsv_numbers(hue: float, sat: float, val: float) -> str:
    """`hue 207.0  sat 59.4%  val 93.7%` — the reading the bars replace."""
    return HSV_LONG.format(hue * 360, sat * 100, val * 100)


def _window(reading: float, span: float, width: int, wrap: bool) -> list:
    """`width` readings of one axis, evenly spaced, centred on `reading`.

    With an odd `width` the centre cell is the reading exactly, which is the
    whole arrangement: the number sits on the value it names. Hue wraps at
    the ends because the wheel does; saturation and value clamp at zero,
    because there is nothing below zero and a clamp is the truth.
    """
    step = 2 * span / (width - 1)
    out = []
    for i in range(width):
        at = reading + (i - (width - 1) / 2) * step
        out.append(at % 1.0 if wrap else min(1.0, max(0.0, at)))
    return out


def _chip(slots, colour, reading, span, width, text, wrap=False) -> str:
    """One bar: a window of the axis with `text` centred over it.

    The text is drawn cell by cell in `readable_fg` of the cell underneath —
    black or white, whichever stays legible — which is the rule the palette
    cells' own labels follow and the reason a value can sit straight on a
    gradient with no box around it. Nothing here paints a foreground of its
    own: every cell answers from the value, so the chip is legible on any
    theme, including the one whose colours are all the same.
    """
    cells = [rgb_to_hex(colour(at))
             for at in _window(reading, span, width, wrap)]
    start = (width - len(text) + 1) // 2
    return "".join(
        f"{bg(cell)}{fg(readable_fg(cell))}"
        f"{text[i - start] if start <= i < start + len(text) else ' '}{RESET}"
        for i, cell in enumerate(cells))


def hsv_readout(slots, value: str, cols: int, numbers: bool = True) -> str:
    """The slot's hue, saturation and value: bars, or the numbers (§8.3).

    Pure like every widget here: every cell of every bar is computed from
    `value` and every colour of chrome from `slots`, on the call, so one
    keystroke slides the windows on the same frame as everything else
    (§14.1). `cols` is the room the row has left, and the ladder it answers
    with is the one §8.3 records: the full bars, the compact ones, or the
    numbers — never a rung chosen and then cut. Nothing here is ever wider
    than `cols`, and the row that carries it is a row the frame already
    spends (§15).

    `numbers=False` asks for the bars or nothing. A caller that spells the
    reading out elsewhere — the exact line under the bars — does not want it
    here too, and the frame is the one place that knows which row is which.
    """
    hue, sat, val = rgb_to_hsv(hex_to_rgb(value))
    spelled = hsv_numbers(hue, sat, val)
    width = next((sizes for sizes in (HSV_FULL, HSV_COMPACT)
                  if cols >= HSV_LABELS + sum(sizes)), None)
    if width is None:
        if not numbers or cols < len(spelled):
            return ""
        return spelled
    axes = (("hue", hue, width[0], HSV_HUE_SPAN / 360, True,
             f"{hue * 360:.0f}°", lambda t: hsv_to_rgb(t, sat, val)),
            ("sat", sat, width[1], HSV_AXIS_SPAN, False,
             f"{sat * 100:.0f}%", lambda t: hsv_to_rgb(hue, t, val)),
            ("val", val, width[2], HSV_AXIS_SPAN, False,
             f"{val * 100:.0f}%", lambda t: hsv_to_rgb(hue, sat, t)))
    return "  ".join(chrome(label, CHROME_MUTED, slots) + " " + _chip(
        slots, colour, reading, span, cells, text, wrap)
        for label, reading, cells, span, wrap, text, colour in axes)


# --------------------------------------------------------------------------
# live code sample
# --------------------------------------------------------------------------

SAMPLE_LINES = [
    ("// live preview - edits show here without reloading", None),
    ("const huebox = \"terminal theme editor\";", "zig"),
    ("const std = @import(\"std\");", "zig"),
    ("", None),
    ("pub fn main() !void {", "zig"),
    ("    var count: u32 = 42;   // your palette", "zig"),
    ("    if (count > 0xFF) {", "zig"),
    ("        std.debug.print(\"{d} colours\\n\", .{count});", "zig"),
    ("    }", "zig"),
]

# The zig lexer emits a small vocabulary — comments, keywords (plain,
# reserved and type), names, builtins, operators, punctuation, strings
# with their escapes, and numbers — and the sample is written to spend all
# of it. The base half of the palette paints syntax; the bright half paints
# what syntax alone cannot say: a comment's muted grey, an escape, a call.
# First match wins and `token in probe` is a subtree test, so a child row
# (`Keyword.Type`) must sit above its parent (`Keyword`).
CALL_SLOT = "palette-12"            # a name in call position
TOKEN_SLOTS = [("Comment", "palette-8"),
               ("Keyword.Type", "palette-6"),
               ("Keyword", "palette-5"),
               ("Name.Builtin", "palette-14"),
               ("String.Escape", "palette-11"),
               ("String", "palette-2"),
               ("Number", "palette-3"),
               ("Operator", "palette-5"),
               ("Punctuation", "foreground")]


def _slot_for_token(token, called: bool = False) -> str:
    """Map a Pygments token class to the palette slot that colours it.

    `called` marks an identifier the sample calls — the one distinction
    the token stream does not make (see `_call_position`). A class the
    zig lexer never emits has no row and lands on `foreground`.
    """
    if token is None:
        return "foreground"
    if called and token in Token.Name:
        return CALL_SLOT
    for name, slot in TOKEN_SLOTS:
        probe = Token
        for part in name.split("."):
            probe = getattr(probe, part, None)
            if probe is None:
                break
        if probe is not None and token in probe:
            return slot
    return "foreground"


def _call_position(tokens, i: int) -> bool:
    """True when the name at `i` is called rather than declared.

    The zig lexer emits a bare `Name` for a declaration, a field, a
    module path and a call alike, so the token stream alone cannot tell
    them apart: a name is in call position when `(` follows it — the space
    between them does not count — and no `fn` precedes it, so `print(...)`
    is a call while `fn main()` is a definition. A builtin never is: it
    wears the builtin slot either way.
    """
    token = tokens[i][0]
    if token not in Token.Name or token in Token.Name.Builtin:
        return False
    before = i
    while before and not tokens[before - 1][1].strip():
        before -= 1
    after = i + 1
    while after < len(tokens) and not tokens[after][1].strip():
        after += 1
    return (after < len(tokens) and tokens[after][1] == "("
            and not (before and tokens[before - 1][1] == "fn"))


_sample_cache = None                 # [(slot, text)]: lexed and mapped once


def _sample():
    """The sample as `(slot, text)` runs — lexed and mapped once a session.

    Neither the tokens nor the mapping change while the editor runs; only
    the hex behind a slot does. Resolving both here leaves `sample_lines`
    reading `slots` on every call, which is what keeps the sample live
    (§14.1) without re-walking the mapping table for every token, every
    frame.
    """
    global _sample_cache
    if _sample_cache is None:
        # Pygments is a declared dependency (§9), so the import above is
        # unguarded; the cache is what makes the lex worth having.
        tokens = list(lex("\n".join(text for text, _ in SAMPLE_LINES),
                          get_lexer_by_name("zig")))
        _sample_cache = [
            (_slot_for_token(token, _call_position(tokens, i)), text)
            for i, (token, text) in enumerate(tokens) if text]
    return _sample_cache


def sample_lines(slots):
    """Paint the sample from the live slot values: one lookup per run."""
    out, current = [], None
    for slot, text in _sample():
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
CURSOR_INDEX = 8                    # the character the cursor sits on
SELECTION_INDEX = 4                 # where the selected run starts


def _split(phrase, start, length):
    """(head, span, tail): the phrase cut around one span of `length`."""
    return (phrase[:start], phrase[start:start + length],
            phrase[start + length:])


SEL_HEAD, SELECTED_TEXT, SEL_TAIL = _split(EXAMPLE_PHRASE, SELECTION_INDEX, 15)
CUR_HEAD, CURSOR_CHAR, CUR_TAIL = _split(EXAMPLE_PHRASE, CURSOR_INDEX, 1)
LABEL_WIDTH = 21                    # widest short name (20) + one padding column
PAIR_WIDTH = 32                     # widest pair label (31) + one padding column
MIN_SAMPLE = 24                     # a phrase worth showing; the pairs yield to it
PAIR_MIN_COLS = 2 + PAIR_WIDTH + MIN_SAMPLE


def pair_label(bg_slot, fg_slot):
    """`selection-background/foreground` — a shared prefix is printed once.

    The strip names both slots of the pair it demonstrates, so the reader
    never has to guess which foreground rides on the swatch.
    """
    common = 0
    for a, b in zip(bg_slot, fg_slot):
        if a != b:
            break
        common += 1
    if common and bg_slot[common - 1] == "-":
        return f"{bg_slot}/{fg_slot[common:]}"
    return f"{bg_slot}/{fg_slot}"


def example_lines(slots, cols=None):
    """The three live example rows: background, selection, cursor (§14.1).

    Pure like `sample_lines`: every colour is read out of `slots` on each
    call, so a single slot change moves the background, the highlight and
    the cursor on the same frame. Each row names the pair of slots it
    demonstrates — `selection-background/foreground` — and shows the colour
    itself: no hex, the swatch is the readout. Wide terminals get the pair
    names; a narrow one keeps the plain slot name so the sentence still has
    room to show anything (`PAIR_MIN_COLS`).
    `cols` is the width the caller can spend (optional): the sample text
    folds to fit through `pack`, the row is finally `clip`ped, and the
    strip never overflows or wraps badly. What is left of each row is
    padded in the buffer's `background` — the fill is a resolved value,
    not a slot name, so a missing slot paints MISSING *in place* and never
    by accident — and the demonstrated colour is exactly the span that
    demonstrates it: `selection-background` covers the selected words and
    nothing past them.
    """
    def value(name):
        return slots.get(name, MISSING)

    # the rest of every row is padded in the buffer's background, never in
    # the colour the row demonstrates: a demonstrated colour ends where its
    # own span does, so a selection run stops at the last selected word
    # instead of running on to the edge of the frame as if it were selected
    fill = value("background")

    # (short label, (bg slot, fg slot), [(text, bg, fg)])
    rows = [
        ("background", ("background", "foreground"),
         [(EXAMPLE_PHRASE, "background", "foreground")]),
        # a selected run of words, in selection-foreground on the
        # selection background, sitting in the sentence like a real one
        ("selection-background",
         ("selection-background", "selection-foreground"),
         [(SEL_HEAD, "background", "foreground"),
          (SELECTED_TEXT, "selection-background", "selection-foreground"),
          (SEL_TAIL, "background", "foreground")]),
        # the cursor covers one character and carries it in cursor-text on
        # cursor-color — a block cursor drawn the way the terminal draws it
        ("cursor-color", ("cursor-color", "cursor-text"),
         [(CUR_HEAD, "background", "foreground"),
          (CURSOR_CHAR, "cursor-color", "cursor-text"),
          (CUR_TAIL, "background", "foreground")]),
    ]
    pairs = cols is None or cols >= PAIR_MIN_COLS
    label_width = PAIR_WIDTH if pairs else LABEL_WIDTH
    out = []
    for name, pair, segments in rows:
        label = pair_label(*pair) if pairs else name
        # the pair names are the row's own subject, in the theme's text
        # colour: no label colour of their own, so the sentence they
        # introduce stays the loudest thing on the row (§8.1). The padding
        # rides inside the painted run and the row's width is measured with
        # `visible`, escapes and all.
        head = "  " + chrome(f"{label:<{label_width}} ", "foreground", slots)
        room = None if cols is None else cols - visible(head)
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
            painted += f"{bg(fill)}{' ' * max(0, room - len(plain))}{RESET}"
        row = head + painted
        out.append(clip(row, cols) if cols is not None else row)
    return out


# --------------------------------------------------------------------------
# live diff (§14.4)
# --------------------------------------------------------------------------

# A git hunk over the sample above it, so the block reads as one story: the
# program you are looking at, and the change someone would commit. A diff has
# no lexer behind it — `@@` and the two signs are the whole vocabulary — so
# the mapping is four slots and no Pygments.
DIFF_MARKS = "palette-6"            # the @@ that opens a hunk
# the hunk header's tail and any unchanged line: muted, like a comment (§8)
DIFF_CONTEXT = "palette-8"
DIFF_ADDED = "palette-2"            # + in green
DIFF_REMOVED = "palette-1"          # - in red
DIFF_HUNK = ("@@", "-6,2 +6,2", "@@ pub fn main() !void {")
DIFF_BODY = (("-", 'var count: u32 = 42;   // your palette'),
             ("+", 'var count: u32 = 0x2A;  // your palette'),
             ("-", 'std.debug.print("{d} colours\\n", .{count});'),
             ("+", 'std.debug.print("{d} slots\\n", .{count});'))
DIFF_SIGNS = {"+": DIFF_ADDED, "-": DIFF_REMOVED, " ": DIFF_CONTEXT}
DIFF_INDENT = "    "                # the block's own indent, as the sample has


def diff_lines(slots, cols=None):
    """The live diff hunk: removed red, added green (§14.4).

    Pure like `sample_lines` and `example_lines`: every colour is read out
    of `slots` on the call, so one keystroke repaints both sides of the
    hunk on the same frame. Only the two signs are loud — the `@@` and
    everything after it wear the comment slot, which is the reading git
    gives the same lines. The indent is painted here rather than by the
    editor's reset-splice so that the hunk header's two halves line up.
    """
    def paint(text, slot):
        return f"{fg(slots.get(slot, MISSING))}{text}{RESET}"

    marks, counts, context = DIFF_HUNK
    rows = [DIFF_INDENT + paint(marks, DIFF_MARKS)
            + paint(f" {counts} {context}", DIFF_CONTEXT)]
    for sign, text in DIFF_BODY:
        rows.append(DIFF_INDENT + paint(sign + text, DIFF_SIGNS[sign]))
    return [clip(row, cols) if cols is not None else row for row in rows]


# --------------------------------------------------------------------------
# static preview
# --------------------------------------------------------------------------

def render_preview(fmt, path, slots, cols=None, rows=None) -> str:
    # cli always passes the live width; 96 stays the piped default.
    cols = cols or 96
    lines = []
    subject = wordmark(slots) + "  " + title(fmt, slots)
    if path and len(path) < cols:
        subject += "  " + chrome(path, CHROME_MUTED, slots)
    lines.append(subject)
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
    lines.append(chrome("0-7 base   8-15 bright", CHROME_MUTED, slots))
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
    lines.append("  " + chrome("edit interactively:  huebox edit",
                               CHROME_MUTED, slots))
    lines.append("")
    return "\n".join(clip(line, cols) for line in lines)
