"""Dump fixture frames as (char, fg, bg) triples JSON for the Rust grid-parity test.

Part of plan Phase 3 (huebox-render): a Python helper dumps each fixture
frame and the Rust tests in `rust/huebox-render/tests/grid_parity.rs`
assert identical grids on the same fixtures. Run:

    /usr/bin/python3.14 tests/dump_grid.py

writes `rust/huebox-render/tests/fixtures/*.json` (frames, clip corpus,
pack vectors, sample runs). Fixture slots/sizes live below in FIXTURES;
regenerate only when the frame changes on purpose, with the change in the
diff.

Triple semantics (shared with the Rust side): `fg`/`bg` are lowercase
`#rrggbb` or `""` for unset (no escape in effect / terminal default).
Backdrop pad is normalised: the maximal trailing run of spaces on the
buffer background gets `fg = ""`, matching the Rust `backdrop`, which
pads with unpainted cells.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root: `import huebox`

# The layout under test reads these at frame time; pin them on so a stray
# export in the dumper's environment cannot drift the fixtures.
os.environ["HUEBOX_TOP"] = "1"
os.environ["HUEBOX_TOP_NEW"] = "1"

from huebox import editor  # noqa: E402
from huebox.color import MISSING  # noqa: E402
from huebox.render import _sample, clip, pack, visible  # noqa: E402

SGR = re.compile(r"\x1b\[([0-9;]*)m")

EVENING = {
    "palette-0": "#1a1a20", "palette-1": "#c05c5c",
    "palette-2": "#5cc05c", "palette-3": "#c0a05c",
    "palette-4": "#5c8cc0", "palette-5": "#c05c8c",
    "palette-6": "#5cc0c0", "palette-7": "#80808c",
    "palette-8": "#d0d0d8", "palette-9": "#e06c6c",
    "palette-10": "#6cc06c", "palette-11": "#e0c06c",
    "palette-12": "#6c9ce0", "palette-13": "#e06c9c",
    "palette-14": "#6cc0c0", "palette-15": "#f0f0f4",
    "background": "#101014", "foreground": "#e6e6ea",
    "cursor-color": "#e6e6ea", "cursor-text": "#101014",
    "selection-background": "#2a2a34",
    "selection-foreground": "#f0f0f8",
}

DAYBREAK = {
    "palette-0": "#fafafa", "palette-1": "#b03030",
    "palette-2": "#308030", "palette-3": "#806000",
    "palette-4": "#3050a0", "palette-5": "#802080",
    "palette-6": "#207080", "palette-7": "#606060",
    "palette-8": "#909090", "palette-9": "#d06060",
    "palette-10": "#60a060", "palette-11": "#a08030",
    "palette-12": "#6080d0", "palette-13": "#a060a0",
    "palette-14": "#4090a0", "palette-15": "#202020",
    "background": "#f4f0e8", "foreground": "#202028",
    "cursor-color": "#202028", "cursor-text": "#f4f0e8",
    "selection-background": "#d0d8e8",
    "selection-foreground": "#202028",
}

# (name, slots, cols, rows, sel, status, undo_len, use_banner, path, label)
FIXTURES = [
    ("evening-80x24", EVENING, 80, 24, 5, "", 0, None, "/tmp/x", "ghostty"),
    ("evening-100x30", EVENING, 100, 30, 0, "", 0, None, "/tmp/x", "ghostty"),
    ("evening-40x12", EVENING, 40, 12, 21, "", 0, None, "/tmp/theme.toml", "kitty"),
    ("evening-120x24-forced", EVENING, 120, 24, 5, "", 0, True, "/tmp/x", "ghostty"),
    ("evening-60x16", EVENING, 60, 16, 5, "", 0, None, "/tmp/x", "alacritty"),
    ("evening-toosmall", EVENING, 39, 11, 5, "", 0, None, "/tmp/x", "ghostty"),
    ("evening-status", EVENING, 80, 24, 5, "saved", 2, None, "/tmp/x", "ghostty"),
    ("daybreak-150x50", DAYBREAK, 150, 50, 16, "", 0, None,
     "/home/u/.config/huebox/daybreak.toml", "ghostty"),
    ("daybreak-80x24", DAYBREAK, 80, 24, 10, "applied", 1, None, "", "direct:/tmp/cfg"),
    ("empty-80x24", {}, 80, 24, 5, "", 0, None, "/tmp/x", "ghostty"),
]

# Adversarial width corpus: wide glyphs, combining marks, escapes.
CORPUS = [
    "", "abc", "a" * 40, "一二三四五", "ab一cd一ef",
    "é", "áb", "e\u0301", "a\u0301bc",
    "\x1b[38;2;255;0;0mabc\x1b[0m", "\x1b[1mab\x1b[0mcd",
    "\x1b[H\x1b[2J", "a\x1b[38;2;1;2;3m一\x1b[0mb",
    "  huebox  ", "terminal too small — need 40x12",
    "hue 207° sat 59% val 94%", "█▀▄ ▄█▀",
]
CORPUS_COLS = [0, 1, 2, 3, 4, 5, 10, 40]

PACK_CASES = [
    (["aa", "bb", "cc"], 5, " "),
    (["aa", "bb", "cc"], 4, " "),
    (["arrows move", "w/e hue", "s/d sat", "x/c light"], 40, "  "),
    (["arrows move", "w/e hue", "s/d sat", "x/c light"], 24, "  "),
    (["\x1b[38;2;255;0;0maa\x1b[0m", "bb"], 5, " "),
    (["abcdef"], 4, " "),
    ([], 80, "  "),
]


def parse_row(text):
    """One backdrop'd row as [(char, fg, bg)] with `""` for unset."""
    cells, fg, bg, pos = [], None, None, 0
    for match in SGR.finditer(text):
        for char in text[pos:match.start()]:
            cells.append([char, fg or "", bg or ""])
        params = match.group(1)
        if params == "0":
            fg, bg = None, None
        elif params == "1":
            pass  # bold: no cell in the model, the harness ignores it
        elif params.startswith("38;2;"):
            _, _, r, g, b = params.split(";")
            fg = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
        elif params.startswith("48;2;"):
            _, _, r, g, b = params.split(";")
            bg = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
        pos = match.end()
    for char in text[pos:]:
        cells.append([char, fg or "", bg or ""])
    return cells


def normalise_pad(line, cells, background):
    """The backdrop pad loses its fg.

    Matches the Rust `backdrop`, which pads with unpainted cells: the
    Python frame's pad inherits whatever fg the row left in effect. Only
    the pad is reset — the spaces after the row's *last* buffer-fill
    escape, which `backdrop` emits right before the pad. Content spaces
    that merely share the buffer background (a `cursor-text` swatch on a
    matching background) keep their paint.
    """
    r, g, b = (int(background[1:3], 16), int(background[3:5], 16),
                int(background[5:7], 16))
    fill = f"\x1b[48;2;{r};{g};{b}m"
    at = line.rfind(fill)
    if at < 0:
        return cells
    tail = SGR.sub("", line[at + len(fill):])
    if tail and not tail.strip(" "):
        for cell in cells[len(cells) - len(tail):]:
            assert cell[0] == " ", (line, cell)
            cell[1] = ""
    return cells


def dump_frame(name, slots, cols, rows, sel, status, undo_len, use_banner,
               path, label):
    """Draw one fixture frame and dump its grid, hits and regions."""
    hits, regions = [], []
    undo = [("palette-0", "#000000")] * undo_len
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        editor.draw_editor("ghostty", path, dict(slots), sel, undo, status,
                           1, head=label, size=(cols, rows), hits=hits,
                           regions=regions, use_banner=use_banner)
    text = buf.getvalue()
    assert text.startswith("\033[H\033[2J"), name
    text = text[len("\033[H\033[2J"):]
    lines = text.split("\r\n")
    if text.endswith("\r\n"):
        lines = lines[:-1]
    background = slots.get("background", MISSING)
    grid = [normalise_pad(line, parse_row(line), background)
            for line in lines]
    return {
        "name": name,
        "argv": {"label": label, "path": path, "sel": sel,
                 "undo_len": undo_len, "status": status, "cols": cols,
                 "rows": rows,
                 "use_banner": use_banner if use_banner is not None else "auto"},
        "slots": slots,
        "grid": grid,
        "hits": [[h.y, h.x0, h.x1, h.slot] for h in hits],
        "regions": [[n, first, count] for n, first, count in regions],
    }


def plain(text):
    """`clip` output with escapes and the truncation RESET stripped."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def main():
    root = os.path.join(_HERE, "..", "rust", "huebox-render", "tests",
                        "fixtures")
    os.makedirs(root, exist_ok=True)

    frames = [dump_frame(*case) for case in FIXTURES]
    with open(os.path.join(root, "frames.json"), "w") as handle:
        json.dump(frames, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    corpus = [{"input": text, "visible": visible(text),
               "clips": {str(cols): plain(clip(text, cols))
                         for cols in CORPUS_COLS}}
              for text in CORPUS]
    with open(os.path.join(root, "clip_corpus.json"), "w") as handle:
        json.dump(corpus, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    packs = [{"items": items, "cols": cols, "sep": sep,
              "lines": pack(items, cols, sep=sep)}
             for items, cols, sep in PACK_CASES]
    with open(os.path.join(root, "pack_cases.json"), "w") as handle:
        json.dump(packs, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    runs = [{"slot": slot, "text": text} for slot, text in _sample()]
    with open(os.path.join(root, "sample_runs.json"), "w") as handle:
        json.dump(runs, handle, indent=1, ensure_ascii=False)
        handle.write("\n")

    print(f"dumped {len(frames)} frames + clip/pack/sample fixtures to {root}")


if __name__ == "__main__":
    main()
