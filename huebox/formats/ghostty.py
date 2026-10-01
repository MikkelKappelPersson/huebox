"""Ghostty's flat `key = value` dialect (§6)."""

from __future__ import annotations

import re

from .base import HEX, _named_rule, _palette_rule

GHOSTTY_RULES = [
    _palette_rule(re.compile(
        r"^(?P<pre>\s*palette\s*=\s*)(?P<index>\d+)(?P<mid>\s*=\s*)" + "(?P<val>" + HEX + ")",
        re.IGNORECASE)),
    _named_rule(re.compile(
        r"^(?P<pre>\s*)(?P<name>background|foreground|cursor-color|cursor-text|"
        r"selection-background|selection-foreground)(?P<mid>\s*=\s*)" + "(?P<val>" + HEX + ")",
        re.IGNORECASE)),
]
