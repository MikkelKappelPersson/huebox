//! Pure frame rows for the huebox editor: no compositor, no I/O, no
//! escape sequences in or out.
//!
//! Rust port of `huebox/render.py` (`clip` / `pack` / `visible`, frame
//! typography, samples, examples strip, diff, HSV bars), `huebox/preview.py`
//! (the shared swatch-cell family) and the pure layout half of
//! `huebox/editor.py` (`Grid`, `grid_geometry`, `move_slot`, the
//! side/top/panel shares, `draw_editor` as [`frame::frame`]).
//!
//! The cell model replaces the serialise-and-reparse round-trip the
//! Python frame parses back per row: every builder here emits styled
//! cells directly and the downstream app maps them to Ratatui spans.
//! Cells are built styled, never serialised and reparsed — the parse
//! stage ceases to exist by design (research §4 binds).
//!
//! Every function takes the current [`Slots`] and reads its colours on the
//! call — live-everything (§14.1) holds by construction: no colour survives
//! a frame. Nothing here caches, touches the terminal, or reads the disk.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod banner;
pub mod chrome;
pub mod diff;
pub mod examples;
pub mod floor;
pub mod frame;
pub mod grid;
pub mod hsv;
pub mod layout;
pub mod palette;
pub mod sample;

use huebox_core::Slots;

/// One frame cell: a character plus its paint.
///
/// `fg` / `bg` are lowercase `"#rrggbb"` hex, or `""` when unset — a span
/// that paints no background (frame chrome, sample text) leaves `bg`
/// empty, meaning the buffer background, and [`floor::backdrop`] resolves
/// it to the concrete fill when the row is laid out. `fg` empty is the
/// terminal default, exactly what the Python frame's unpainted runs leave
/// in effect. Downstream the app maps these to Ratatui spans.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cell {
    /// The character in this column (a combining mark is its own cell).
    pub ch: char,
    /// Foreground hex `"#rrggbb"`, or `""` for unset.
    pub fg: String,
    /// Background hex `"#rrggbb"`, or `""` for the buffer background.
    pub bg: String,
    /// Bold emphasis, exactly where Python's frame wore `BOLD`: titles,
    /// the wordmark-adjacent headers, selected rows/cells, banner faces.
    /// The grid harness still compares `(ch, fg, bg)` triples — emphasis
    /// rides along, never disturbing parity.
    pub bold: bool,
}

impl Cell {
    /// One cell with concrete paint, unbolded.
    pub fn new(ch: char, fg: &str, bg: &str) -> Self {
        Self {
            ch,
            fg: fg.to_owned(),
            bg: bg.to_owned(),
            bold: false,
        }
    }

    /// One unpainted air cell.
    pub fn air() -> Self {
        Self {
            ch: ' ',
            fg: String::new(),
            bg: String::new(),
            bold: false,
        }
    }

    /// This cell with bold emphasis set.
    pub fn bolded(mut self) -> Self {
        self.bold = true;
        self
    }
}

/// One frame row: a run of cells, char-aligned (a wide glyph is one cell).
pub type Row = Vec<Cell>;

/// Fill for slots a buffer omits — painted in place, never a colour of
/// the frame's own (mirrors `color.MISSING`).
pub const MISSING: &str = "#808080";

/// The hex `slots` holds for `name`, or [`MISSING`] when the buffer has no
/// such slot — the safe form of `Slots::get`, which panics on unknown
/// names. Mirrors `slots.get(name, MISSING)`.
pub fn slot_hex<'a>(slots: &'a Slots, name: &str) -> &'a str {
    const NAMES: [&str; 22] = [
        "palette-0",
        "palette-1",
        "palette-2",
        "palette-3",
        "palette-4",
        "palette-5",
        "palette-6",
        "palette-7",
        "palette-8",
        "palette-9",
        "palette-10",
        "palette-11",
        "palette-12",
        "palette-13",
        "palette-14",
        "palette-15",
        "background",
        "foreground",
        "cursor-color",
        "cursor-text",
        "selection-background",
        "selection-foreground",
    ];
    if NAMES.contains(&name) {
        slots.get(name)
    } else {
        MISSING
    }
}

/// One painted run of text: a cell per char sharing `fg` / `bg`.
pub fn painted(text: &str, fg: &str, bg: &str) -> Row {
    text.chars().map(|ch| Cell::new(ch, fg, bg)).collect()
}

/// One unpainted run of text: a cell per char with empty paint.
pub fn plain(text: &str) -> Row {
    text.chars().map(|ch| Cell::new(ch, "", "")).collect()
}

pub use layout::{clip, pack, visible};
