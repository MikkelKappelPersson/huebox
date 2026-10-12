//! kitty's space-separated `key value` dialect (001 spec §6).
//!
//! Two rules, palette first: `color<index>` plus an *optional* `=`, then
//! the five named keys (`background`, `foreground`, `cursor`,
//! `selection_background`, `selection_foreground`) with kitty's three
//! renames folded to canonical slots. Matching is case-insensitive; only
//! the colour token is ever rewritten.

use std::collections::BTreeMap;

use crate::core::Slots;

use crate::format::base::{hex_val, lit, read_flat, skip_ws, write_flat};

/// The canonical slot one kitty line carries, plus its colour span.
pub(crate) fn match_line(line: &str) -> Option<(String, usize, usize)> {
    match_palette(line).or_else(|| match_named(line))
}

/// `color\d+\s*=?\s*HEX`: the index stays verbatim (`color007` reads as
/// `palette-007`), and the `=` may be absent entirely.
fn match_palette(line: &str) -> Option<(String, usize, usize)> {
    let bytes = line.as_bytes();
    let mut pos = skip_ws(bytes, 0);
    pos = lit(bytes, pos, "color")?;
    let start = pos;
    while pos < bytes.len() && bytes[pos].is_ascii_digit() {
        pos += 1;
    }
    if start == pos {
        return None;
    }
    let index = &line[start..pos];
    pos = skip_ws(bytes, pos);
    pos = eat_opt_eq(bytes, pos);
    pos = skip_ws(bytes, pos);
    let (vstart, vend) = hex_val(line, pos)?;
    Some((format!("palette-{index}"), vstart, vend))
}

/// Map a matched keyword to its slot: the three exact-lowercase renames,
/// else the file's spelling with underscores folded to dashes.
fn map_slot(name: &str) -> String {
    match name {
        "cursor" => "cursor-color".to_string(),
        "selection_background" => "selection-background".to_string(),
        "selection_foreground" => "selection-foreground".to_string(),
        _ => name.replace('_', "-"),
    }
}

/// `name\s*=?\s*HEX` for each named key, tried in order. The slot comes
/// from [`map_slot`] applied to the keyword as spelled in the file.
fn match_named(line: &str) -> Option<(String, usize, usize)> {
    const NAMED: [&str; 5] = [
        "background",
        "foreground",
        "cursor",
        "selection_background",
        "selection_foreground",
    ];
    let bytes = line.as_bytes();
    for keyword in NAMED {
        let mut pos = skip_ws(bytes, 0);
        let key_start = pos;
        pos = match lit(bytes, pos, keyword) {
            Some(next) => next,
            None => continue,
        };
        let slot = map_slot(&line[key_start..pos]);
        pos = skip_ws(bytes, pos);
        pos = eat_opt_eq(bytes, pos);
        pos = skip_ws(bytes, pos);
        if let Some((vstart, vend)) = hex_val(line, pos) {
            return Some((slot, vstart, vend));
        }
    }
    None
}

/// kitty's `(?P<mid>\s*=?)`: whitespace, then an `=` only if one is there.
fn eat_opt_eq(bytes: &[u8], pos: usize) -> usize {
    if bytes.get(pos) == Some(&b'=') {
        pos + 1
    } else {
        pos
    }
}

/// Every slot [`match_line`] finds in `text`.
pub(crate) fn read_text(text: &str) -> BTreeMap<String, String> {
    read_flat(text, match_line)
}

/// Splice `slots` into `text` line by line, or return it untouched.
/// See [`crate::format::base::write_flat`] for the no-op contract.
pub(crate) fn write_text(text: &str, slots: &Slots) -> String {
    write_flat(text, slots, match_line)
}
