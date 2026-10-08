"""Provider themes: a `FORMATS`-shaped facade over on-disk theme files.

A provider is a terminal whose themes already sit on the machine as
files — Ghostty's shipped + user theme dirs, kitty's themes dirs,
Alacritty's themes dir. This module lists those files and reads them
through the format registry, so dialect coverage is inherited, never
re-implemented. No subprocess, no network (spec decision 7).

Pinned local dirs (passed in by the `cli` composition root, never probed
here — this module imports `formats` + stdlib only, never `themes` or
`detect`, so the headless import state can consume it without touching
either):

========== ================ ==========================================
provider   format read      dirs `cli` passes, in order
========== ================ ==========================================
ghostty    `ghostty`        `$GHOSTTY_RESOURCES_DIR/themes` (shipped,
                            default `/usr/share/ghostty`), then
                            `detect.ghostty_themes_dir()` (user)
kitty      `kitty`          `~/.config/kitty/themes`, then
                            `~/.config/kitty/kitty-themes/themes`
                            (the `kitten themes` cache)
alacritty  `alacritty`      `~/.config/alacritty/themes`
========== ================ ==========================================

Discovery is non-recursive with one suffix rule per provider: Ghostty
theme files are extensionless (`themes/<name>`, spaces included — the
shipped dir holds `Catppuccin Mocha` and friends); kitty themes are
`*.conf`; Alacritty themes are `*.toml` / `*.yml` / `*.yaml` (anything
the reader finds no colours in is skipped with a note, §7). Dotfiles are
never themes. A missing or unreadable dir is the empty group, never an
exception; a file with zero colours never lists — skipped per-file with
a note the caller reports.

Shape mirrors `FORMATS`: `PROVIDERS[name]["list"](dirs)` lists
`(display_name, path)` plus per-file notes, `PROVIDERS[name]["read"]`
reads slots, `PROVIDERS[name]["format"]` names the format. `list_themes`
is the same listing as a plain function; `slugify` / `unique_slug` /
`batch_unique` are the §8.4 name mapping (pure, no library access — a
candidate that clashes with the library is reported upstream, never
renamed here).
"""

from __future__ import annotations

import os
import re

from .formats import FORMATS

#: Longest legal huebox name (`themes.valid_name`): 64 characters.
MAX_NAME_LEN = 64

#: Chars a slugified stem leaves for a `-N` batch suffix (`-2` .. `-9`).
_SUFFIX_ROOM = 2

#: Accepted filename suffixes per provider; Ghostty takes none because
#: its theme files are extensionless (`themes/<name>`).
SUFFIXES = {
    "ghostty": (),
    "kitty": (".conf",),
    "alacritty": (".toml", ".yml", ".yaml"),
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Map a provider theme name to a huebox-name candidate (§8.4).

    Lowercase, runs of non-`[a-z0-9]` collapse to one `-`, leading `-`
    stripped (so the result starts `^[A-Za-z0-9]` like `valid_name`
    demands), stem cut to leave room for a `-N` suffix within 64 chars.
    Pure: no filesystem, no library — a clash with the library is the
    caller's to report (spec §7), never renamed here. Returns `""` when
    nothing survives (a name of pure punctuation); the caller reports
    that as a bad name.
    """
    slug = _NON_ALNUM.sub("-", str(name).lower()).lstrip("-")
    return slug[:MAX_NAME_LEN - _SUFFIX_ROOM]


def unique_slug(base: str, taken_lower: set) -> str:
    """First free slug for `base`: `base`, else `base-2`, `base-3`, ….

    `taken_lower` holds the lowercased names this batch already claimed;
    the returned candidate (lowercased) is added to it, so a loop over
    the batch never reuses a name. The stem is re-cut per suffix so
    every candidate stays within 64 chars. An empty `base` falls back to
    `"theme"`. Case-insensitive like `themes.create` — but the set here
    is the batch only; the library comparison happens upstream.
    """
    stem = (base or "theme")[:MAX_NAME_LEN]
    candidate = stem
    number = 1
    while candidate.lower() in taken_lower:
        number += 1
        suffix = f"-{number}"
        candidate = f"{stem[:MAX_NAME_LEN - len(suffix)]}{suffix}"
    taken_lower.add(candidate.lower())
    return candidate


def batch_unique(bases: list) -> list:
    """`unique_slug` over `bases` in order; returns the candidates."""
    taken: set = set()
    return [unique_slug(base, taken) for base in bases]


def _display_name(provider: str, filename: str):
    """The list-row name for `filename`, or `None` when no theme file."""
    if filename.startswith("."):
        return None
    if provider == "ghostty":
        if os.path.splitext(filename)[1]:
            return None
        return filename
    lowered = filename.lower()
    for suffix in SUFFIXES[provider]:
        if lowered.endswith(suffix) and len(filename) > len(suffix):
            return filename[:-len(suffix)]
    return None


def _known(provider: str):
    """The `PROVIDERS` entry for `provider`, or a usage error."""
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"unknown provider {provider!r} "
                         f"(choose from {', '.join(PROVIDERS)})") from None


def list_themes(provider: str, dirs=None) -> tuple:
    """`(entries, notes)` for `provider` across `dirs`, in order.

    `entries` is `[(display_name, path)]` sorted by name (case-folded),
    one row per theme file that holds colours; `notes` names each
    skipped file (`<path>: no colours found, skipped`). A dir that is
    missing or unreadable contributes nothing — never an exception. A
    name present in two dirs resolves to the later dir (the user dir
    shadows shipped). `dirs=None` lists nothing.
    """
    spec = _known(provider)
    found: dict = {}
    notes: list = []
    for directory in dirs or []:
        try:
            with os.scandir(directory) as handle:
                filenames = sorted(entry.name for entry in handle)
        except OSError:
            continue
        for filename in filenames:
            display = _display_name(provider, filename)
            if display is None:
                continue
            path = os.path.join(directory, filename)
            if not os.path.isfile(path):
                continue
            if not spec["read"](path):
                notes.append(f"{path}: no colours found, skipped")
                continue
            found[display] = path
    entries = sorted(found.items(), key=lambda item: item[0].lower())
    return entries, notes


def read_theme(provider: str, path: str) -> dict:
    """Slots for one provider file via `FORMATS`; `{}` when none found."""
    return _known(provider)["read"](path) or {}


PROVIDERS = {
    "ghostty": {
        "format": "ghostty",
        "suffixes": SUFFIXES["ghostty"],
        "list": lambda dirs=None: list_themes("ghostty", dirs),
        "read": FORMATS["ghostty"]["read"],
    },
    "kitty": {
        "format": "kitty",
        "suffixes": SUFFIXES["kitty"],
        "list": lambda dirs=None: list_themes("kitty", dirs),
        "read": FORMATS["kitty"]["read"],
    },
    "alacritty": {
        "format": "alacritty",
        "suffixes": SUFFIXES["alacritty"],
        "list": lambda dirs=None: list_themes("alacritty", dirs),
        "read": FORMATS["alacritty"]["read"],
    },
}

PROVIDER_NAMES = list(PROVIDERS)
