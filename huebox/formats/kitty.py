"""kitty's space-separated `key value` dialect (§6)."""

from __future__ import annotations

import re

from .base import HEX, _named_rule, _palette_rule

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
