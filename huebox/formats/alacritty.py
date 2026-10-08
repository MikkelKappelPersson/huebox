"""Alacritty's TOML dialect: dotted keys, tables, inline tables (§6)."""

from __future__ import annotations

import re

from ..color import is_hex, normalize_hex

ALACRITTY_PATHS = {
    "colors.primary.background": "background",
    "colors.primary.foreground": "foreground",
    "colors.cursor.text": "cursor-text",
    "colors.cursor.cursor": "cursor-color",
    "colors.selection.background": "selection-background",
    "colors.selection.foreground": "selection-foreground",
    **{f"colors.normal.{i}": f"palette-{i}" for i in range(16)},
    # the same eight under their everyday names: hand-written configs
    # say `black`, not `0` — both spellings read and write (§6).
    **{f"colors.normal.{name}": f"palette-{i}" for i, name in enumerate(
        ["black", "red", "green", "yellow",
         "blue", "magenta", "cyan", "white"])},
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
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
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
    """Replace each colour's value token, keeping quotes and spacing intact.

    Same rule 4 as `write_flat` (§6.2): nothing changed means the file is
    not opened for writing, so a push of an unchanged theme leaves the
    config's mtime alone.
    """
    with open(path, encoding="utf-8") as handle:
        lines_before = handle.read().splitlines()
    lines = list(lines_before)
    for index, slot, token, start, end in list(toml_entries(path)):
        if slot not in slots:
            continue
        replacement = (f'"{slots[slot]}"'
                       if token[:1] in ('"', "'") else slots[slot])
        line = lines[index]
        lines[index] = line[:start] + replacement + line[end:]
    if lines == lines_before:
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
