"""huebox - a terminal theme editor with live preview.

Package split of the original single-file module (§17 of
docs/001-spec/spec.md). The v1 public names are re-exported here so
`import huebox` keeps working for existing tests and callers.
"""

# NOTE: version first — cli imports it during package initialisation.
__version__ = "0.4.0"

from .cli import main
from .color import MISSING, NAMED, PALETTE, SLOTS
from .formats import FORMAT_NAMES, FORMATS
from .render import (clip, diff_lines, example_lines, pack, render_preview,
                     sample_lines)
from .tui import term_size

__all__ = [
    "__version__",
    "main",
    "PALETTE",
    "NAMED",
    "SLOTS",
    "MISSING",
    "FORMATS",
    "FORMAT_NAMES",
    "clip",
    "diff_lines",
    "pack",
    "example_lines",
    "render_preview",
    "sample_lines",
    "term_size",
]
