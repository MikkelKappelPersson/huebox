//! Shared machinery for the flat (line-oriented) formats (001 spec §6).
//!
//! Every format is described by a line matcher — [`Matcher`] — naming the
//! canonical slot a line carries and the byte span of its colour token.
//! Reading only needs the slot; writing splices the new colour into the
//! span, which is what lets the writers edit text in place without
//! disturbing comments or spacing.
//!
//! Hex tolerance lives here, next to its only callers: [`format_is_hex`]
//! and [`format_normalize_hex`] mirror `color.py`'s `is_hex` /
//! `normalize_hex` byte-for-byte (the optional leading `#`, the
//! surrounding-whitespace strip, the lowercase fold). They are `pub(crate)`
//! and named for `grep`: a later pass folds them into `huebox-core`, and
//! the `format_` prefix marks every call site to move.

use std::collections::BTreeMap;

use crate::core::Slots;

/// A line matcher: the canonical slot a line carries, plus the byte span
/// of its colour token (span covers the token including any `#`).
///
/// One match per line, first rule wins — the caller tries its palette rule
/// before its named rule, as the Python rule lists are ordered.
pub(crate) type Matcher = fn(&str) -> Option<(String, usize, usize)>;

/// `color.py:is_hex` verbatim: the stripped value is an optional `#`
/// followed by exactly six ASCII hex digits — nothing more, nothing less.
///
/// Named `format_` for `grep`: a later pass folds this into `huebox-core`.
pub(crate) fn format_is_hex(value: &str) -> bool {
    let text = value.trim();
    let body = text.strip_prefix('#').unwrap_or(text);
    body.len() == 6 && body.bytes().all(|b| b.is_ascii_hexdigit())
}

/// `color.py:normalize_hex` verbatim: strip, drop every leading `#`,
/// lowercase, re-add one `#`.
///
/// Named `format_` for `grep`: a later pass folds this into `huebox-core`.
pub(crate) fn format_normalize_hex(value: &str) -> String {
    let mut out = String::from("#");
    out.push_str(&value.trim().trim_start_matches('#').to_lowercase());
    out
}

/// Offset past ASCII whitespace from `pos` (the `\s*` in every rule).
pub(crate) fn skip_ws(bytes: &[u8], mut pos: usize) -> usize {
    while pos < bytes.len() && bytes[pos].is_ascii_whitespace() {
        pos += 1;
    }
    pos
}

/// Case-insensitive ASCII keyword at `pos` (every rule is `re.IGNORECASE`);
/// returns the offset past it.
pub(crate) fn lit(bytes: &[u8], pos: usize, word: &str) -> Option<usize> {
    let end = pos + word.len();
    if end <= bytes.len() && bytes[pos..end].eq_ignore_ascii_case(word.as_bytes()) {
        Some(end)
    } else {
        None
    }
}

/// One required byte at `pos` (the `=` in `ghostty`'s `\s*=\s*`).
pub(crate) fn eat(bytes: &[u8], pos: usize, want: u8) -> Option<usize> {
    if bytes.get(pos) == Some(&want) {
        Some(pos + 1)
    } else {
        None
    }
}

/// `HEX` (`#?[0-9a-fA-F]{6}\b`) at `pos`: the span covers the token
/// including any `#`. The trailing boundary rejects a seventh word
/// character exactly as `\b` does, so `#abc1234` and `#abc123_` both fail.
/// (Alacritty's bare `#hex` value has no `\b` and does not use this.)
pub(crate) fn hex_val(line: &str, pos: usize) -> Option<(usize, usize)> {
    let bytes = line.as_bytes();
    let mut end = pos;
    if bytes.get(end) == Some(&b'#') {
        end += 1;
    }
    let digits = end;
    while end < bytes.len() && bytes[end].is_ascii_hexdigit() && end - digits < 6 {
        end += 1;
    }
    if end - digits != 6 {
        return None;
    }
    if let Some(next) = line[end..].chars().next() {
        if next == '_' || next.is_alphanumeric() {
            return None;
        }
    }
    Some((pos, end))
}

/// Splice `replacement` into `line` over `[start, end)`, tolerating stale
/// spans the way Python slicing does: indices clamp to the line instead of
/// panicking, and split multibyte characters resolve to whole ones.
///
/// The flat writers always splice fresh spans, so this is exactly
/// `line[:s] + replacement + line[e:]`. The Alacritty writer replays
/// `write_toml`'s entries-from-original-text loop, where an earlier
/// same-line replacement can shift a later span; clamping keeps that
/// inherited quirk panic-free and byte-identical on ASCII text.
pub(crate) fn splice_line(line: &str, start: usize, end: usize, replacement: &str) -> String {
    let len = line.len();
    let mut s = start.min(len);
    let mut e = end.min(len).max(s);
    while s > 0 && !line.is_char_boundary(s) {
        s -= 1;
    }
    while e < len && !line.is_char_boundary(e) {
        e += 1;
    }
    let e = e.max(s);
    format!("{}{}{}", &line[..s], replacement, &line[e..])
}

/// Split like the writers read: one entry per `\n`, a single trailing
/// `\r` off each line — so a CRLF file parses the way `splitlines` sees
/// it, and a changed write normalises endings exactly as `write_flat` /
/// `write_toml` do (join with `\n` plus one trailing newline).
pub(crate) fn split_lines(text: &str) -> Vec<&str> {
    let mut lines: Vec<&str> = text
        .split('\n')
        .map(|line| line.strip_suffix('\r').unwrap_or(line))
        .collect();
    // `splitlines` yields no segment past a final newline: drop it, so a
    // changed write (`join + "\n"`) round-trips a trailing newline
    // instead of doubling it.
    if text.ends_with('\n') {
        lines.pop();
    }
    lines
}

/// Every slot `matcher` finds in `text`. A repeated slot keeps its last
/// value, as the dict comprehension in `read_flat` does.
pub(crate) fn read_flat(text: &str, matcher: Matcher) -> BTreeMap<String, String> {
    let mut slots = BTreeMap::new();
    for line in split_lines(text) {
        if let Some((slot, start, end)) = matcher(line) {
            slots.insert(slot, format_normalize_hex(&line[start..end]));
        }
    }
    slots
}

/// Splice `slots` into `text` line by line — or return it untouched
/// (001 spec §6.2 rule 4).
///
/// Only colour tokens are replaced; comments, spacing and unrelated keys
/// survive byte-for-byte. When nothing changed the input comes back
/// verbatim (byte-identical, so [`crate::format::apply_write`] reports a no-op and
/// the caller never opens the file); when something did, the lines are
/// joined with `\n` plus one trailing newline, as `write_flat` writes.
///
/// `if slot in slots` becomes a lookup miss here: index spellings no theme
/// holds (`palette-007`, `palette-99`) skip instead of panicking —
/// `Slots::get` and `Slots["name"]` panic on unknown names by design.
pub(crate) fn write_flat(text: &str, slots: &Slots, matcher: Matcher) -> String {
    let table: BTreeMap<&str, &str> = slots.iter().collect();
    let before = split_lines(text);
    let mut lines: Vec<String> = before.iter().map(|line| line.to_string()).collect();
    for line in lines.iter_mut() {
        if let Some((slot, start, end)) = matcher(line) {
            if let Some(hex) = table.get(slot.as_str()) {
                *line = splice_line(line, start, end, hex);
            }
        }
    }
    if lines.iter().map(String::as_str).eq(before.iter().copied()) {
        return text.to_owned();
    }
    let mut out = lines.join("\n");
    out.push('\n');
    out
}

#[cfg(test)]
mod tests {
    //! Byte-parity pins for the two hex helpers: the `is_hex` (12) and
    //! `normalize_hex` (7) vectors from `tests/fixtures/parity_color.json`,
    //! hardcoded so this crate stays free of file I/O even in tests.

    use super::*;

    #[test]
    fn is_hex_matches_python_byte_for_byte() {
        let cases: [(&str, bool); 12] = [
            ("#000000", true),
            ("000000", true),
            ("#ABCDEF", true),
            ("abcdef", true),
            ("  #ABCDEF  ", true),
            ("#ffffff ", true),
            ("#abcdeg", false),
            ("#12345", false),
            ("#1234567", false),
            ("", false),
            ("#12345g", false),
            ("not a colour", false),
        ];
        for (input, expected) in cases {
            assert_eq!(format_is_hex(input), expected, "is_hex({input:?})");
        }
    }

    #[test]
    fn normalize_hex_matches_python_byte_for_byte() {
        let cases: [(&str, &str); 7] = [
            ("  #ABCDEF  ", "#abcdef"),
            ("abcdef", "#abcdef"),
            ("ABCDEF", "#abcdef"),
            ("#AbC123", "#abc123"),
            ("  ff0000  ", "#ff0000"),
            ("#000000", "#000000"),
            ("#AbCdEf", "#abcdef"),
        ];
        for (input, expected) in cases {
            assert_eq!(
                format_normalize_hex(input),
                expected,
                "normalize({input:?})"
            );
        }
    }

    #[test]
    fn hex_val_rejects_seventh_word_char() {
        assert_eq!(hex_val("#abc123", 0), Some((0, 7)));
        assert_eq!(hex_val("abc123", 0), Some((0, 6)));
        assert_eq!(hex_val("#ABC123", 0), Some((0, 7)));
        assert_eq!(hex_val("#abc1234", 0), None);
        assert_eq!(hex_val("#abc123_", 0), None);
        assert_eq!(hex_val("#abc12", 0), None);
        assert_eq!(hex_val("##abc123", 0), None);
        assert_eq!(hex_val("x#abc123", 1), Some((1, 8)));
        assert_eq!(hex_val("#abc123é", 0), None);
        assert_eq!(hex_val("#abc123-é", 0), Some((0, 7)));
    }

    #[test]
    fn splice_line_clamps_like_python_slicing() {
        assert_eq!(splice_line("a = #000000", 4, 11, "#010203"), "a = #010203");
        // Stale end past the line end clamps instead of panicking.
        assert_eq!(splice_line("ab", 0, 99, "cd"), "cd");
    }
}
