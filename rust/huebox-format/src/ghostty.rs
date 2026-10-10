//! Ghostty's flat `key = value` dialect (001 spec §6).
//!
//! Two rules, palette first: `palette = <index> = <hex>`, then the six
//! named keys (`background`, `foreground`, `cursor-color`, `cursor-text`,
//! `selection-background`, `selection-foreground`). Both `=` signs are
//! required; matching is case-insensitive; only the colour token is ever
//! rewritten.

use std::collections::BTreeMap;

use huebox_core::Slots;

use crate::base::{eat, hex_val, lit, read_flat, skip_ws, write_flat};

/// The canonical slots one Ghostty line carries, plus its colour span.
pub(crate) fn match_line(line: &str) -> Option<(String, usize, usize)> {
    match_palette(line).or_else(|| match_named(line))
}

/// `palette\s*=\s*\d+\s*=\s*HEX`: the index stays verbatim, so
/// `palette = 007=…` reads as `palette-007`, as `f"palette-{index}"` does.
fn match_palette(line: &str) -> Option<(String, usize, usize)> {
    let bytes = line.as_bytes();
    let mut pos = skip_ws(bytes, 0);
    pos = lit(bytes, pos, "palette")?;
    pos = skip_ws(bytes, pos);
    pos = eat(bytes, pos, b'=')?;
    pos = skip_ws(bytes, pos);
    let start = pos;
    while pos < bytes.len() && bytes[pos].is_ascii_digit() {
        pos += 1;
    }
    if start == pos {
        return None;
    }
    let index = &line[start..pos];
    pos = skip_ws(bytes, pos);
    pos = eat(bytes, pos, b'=')?;
    pos = skip_ws(bytes, pos);
    let (vstart, vend) = hex_val(line, pos)?;
    Some((format!("palette-{index}"), vstart, vend))
}

/// `name\s*=\s*HEX` for each named key, tried in order. The slot is the
/// keyword as spelled in the file.
fn match_named(line: &str) -> Option<(String, usize, usize)> {
    const NAMED: [&str; 6] = [
        "background",
        "foreground",
        "cursor-color",
        "cursor-text",
        "selection-background",
        "selection-foreground",
    ];
    let bytes = line.as_bytes();
    for keyword in NAMED {
        let mut pos = skip_ws(bytes, 0);
        let key_start = pos;
        pos = match lit(bytes, pos, keyword) {
            Some(next) => next,
            None => continue,
        };
        let name = line[key_start..pos].to_string();
        pos = skip_ws(bytes, pos);
        pos = match eat(bytes, pos, b'=') {
            Some(next) => next,
            None => continue,
        };
        pos = skip_ws(bytes, pos);
        if let Some((vstart, vend)) = hex_val(line, pos) {
            return Some((name, vstart, vend));
        }
    }
    None
}

/// Every slot [`match_line`] finds in `text`.
pub(crate) fn read_text(text: &str) -> BTreeMap<String, String> {
    read_flat(text, match_line)
}

/// Splice `slots` into `text` line by line, or return it untouched.
/// See [`crate::base::write_flat`] for the no-op contract.
pub(crate) fn write_text(text: &str, slots: &Slots) -> String {
    write_flat(text, slots, match_line)
}

/// Drop every line [`match_line`] matches; returns the kept text and how
/// many lines went.
///
/// This is the overrides-delete worker: `themes.clear_ghostty_overrides`
/// (huebox-lib) calls it, then writes only when the count is nonzero —
/// whole-line removal is the one exception to token-splice writes (§6.2),
/// and it lives in this function, not in [`write_text`].
pub(crate) fn remove_color_lines(text: &str) -> (String, usize) {
    // `text.split("\n")`, as `clear_ghostty_overrides` splits — no `\r`
    // stripping, so a CRLF line keeps its `\r` unless it is removed.
    let lines: Vec<&str> = text.split('\n').collect();
    let kept: Vec<&str> = lines
        .iter()
        .copied()
        .filter(|line| match_line(line).is_none())
        .collect();
    (kept.join("\n"), lines.len() - kept.len())
}
