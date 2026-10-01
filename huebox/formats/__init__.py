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
        "env": {"KITTY_CONFIG_DIR": "kitty.conf"},
    },
    "alacritty": {
        "read": alacritty.read_toml,
        "write": alacritty.write_toml,
        "defaults": ["~/.config/alacritty/alacritty.toml",
                     "~/.config/alacritty/alacritty.yml",
                     "~/.alacritty.toml"],
        "env": {"ALACRITTY_CONFIG_DIR": "alacritty.toml",
                "ALACRITTY_CONFIG": "alacritty.toml"},
    },
}

FORMAT_NAMES = list(FORMATS)
