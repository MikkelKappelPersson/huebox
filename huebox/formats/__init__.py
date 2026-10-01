"""Supported terminal formats: name → read/write/defaults/env (§6)."""

from __future__ import annotations

from . import alacritty, ghostty, kitty
from .base import read_flat, write_flat

FORMATS = {
    "ghostty": {
        "read": lambda p: read_flat(p, ghostty.GHOSTTY_RULES),
        "write": lambda p, s: write_flat(p, s, ghostty.GHOSTTY_RULES),
        "defaults": ["~/.config/ghostty/config.ghostty",
                     "~/.config/ghostty/config"],
        "env": {},
    },
    "kitty": {
        "read": lambda p: read_flat(p, kitty.KITTY_RULES),
        "write": lambda p, s: write_flat(p, s, kitty.KITTY_RULES),
        "defaults": ["~/.config/kitty/kitty.conf", "~/.kitty.conf"],
        # `KITTY_CONFIG_DIRECTORY` is the spelling upstream documents
        # (plan appendix A); `KITTY_CONFIG_DIR` was ours, kept as a
        # secondary probe for one release after 2.0 and removed with the
        # next minor bump. Order matters - the dict is probed in order.
        "env": {"KITTY_CONFIG_DIRECTORY": "kitty.conf",
                "KITTY_CONFIG_DIR": "kitty.conf"},
    },
    "alacritty": {
        "read": alacritty.read_toml,
        "write": alacritty.write_toml,
        # upstream's own order (plan appendix A) with huebox's legacy
        # `alacritty.yml` kept where v1 found it
        "defaults": ["~/.config/alacritty/alacritty.toml",
                     "~/.config/alacritty.toml",
                     "~/.config/alacritty/alacritty.yml",
                     "~/.alacritty.toml"],
        # alacritty documents no env var for the config path, so there is
        # none to honour here; --config is the only override (§7.1)
        "env": {},
    },
}

FORMAT_NAMES = list(FORMATS)
