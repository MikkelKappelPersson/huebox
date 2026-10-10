//! The live diff hunk: removed red, added green (§14.4).
//!
//! Rust port of `diff_lines` in `huebox/render.py`. A git hunk over the
//! sample above it, so the block reads as one story: the program you are
//! looking at, and the change someone would commit. A diff has no lexer
//! behind it — `@@` and the two signs are the whole vocabulary — so the
//! mapping is four slots and no grammar.

use huebox_core::Slots;

use crate::layout::clip_row;
use crate::{Row, painted, plain, slot_hex};

/// The `@@` that opens a hunk.
pub const DIFF_MARKS: &str = "palette-6";
/// The hunk header's tail and any unchanged line: muted, like a comment.
pub const DIFF_CONTEXT: &str = "palette-8";
/// `+` in green.
pub const DIFF_ADDED: &str = "palette-2";
/// `-` in red.
pub const DIFF_REMOVED: &str = "palette-1";

/// `("@@", "-6,2 +6,2", "@@ pub fn main() !void {")`.
pub const DIFF_HUNK: (&str, &str, &str) = ("@@", "-6,2 +6,2", "@@ pub fn main() !void {");
/// `(sign, text)` body rows.
pub const DIFF_BODY: [(char, &str); 4] = [
    ('-', "var count: u32 = 42;   // your palette"),
    ('+', "var count: u32 = 0x2A;  // your palette"),
    ('-', "std.debug.print(\"{d} colours\\n\", .{count});"),
    ('+', "std.debug.print(\"{d} slots\\n\", .{count});"),
];

/// The slot a diff sign wears.
pub fn diff_sign_slot(sign: char) -> &'static str {
    match sign {
        '+' => DIFF_ADDED,
        '-' => DIFF_REMOVED,
        _ => DIFF_CONTEXT,
    }
}

/// The block's own indent, as the sample has.
pub const DIFF_INDENT: &str = "    ";

/// The live diff hunk: removed red, added green.
///
/// Pure like the sample and the examples: every colour is read out of
/// `slots` on the call, so one keystroke repaints both sides of the hunk
/// on the same frame. Only the two signs are loud — the `@@` and
/// everything after it wear the comment slot, which is the reading git
/// gives the same lines. `indent` is the block's own air: the bare
/// frame's four columns, empty inside bordered panels.
pub fn diff_lines(slots: &Slots, cols: Option<usize>, indent: &str) -> Vec<Row> {
    let paint = |text: &str, slot: &str| painted(text, slot_hex(slots, slot), "");
    let (marks, counts, context) = DIFF_HUNK;
    let mut rows = vec![{
        let mut row = plain(indent);
        row.extend(paint(marks, DIFF_MARKS));
        row.extend(paint(&format!(" {counts} {context}"), DIFF_CONTEXT));
        row
    }];
    for (sign, text) in DIFF_BODY {
        let mut row = plain(indent);
        row.extend(paint(&format!("{sign}{text}"), diff_sign_slot(sign)));
        rows.push(row);
    }
    match cols {
        Some(c) => rows.iter().map(|r| clip_row(r, c)).collect(),
        None => rows,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::row_visible;
    use huebox_core::Slots;

    fn text(row: &Row) -> String {
        row.iter().map(|c| c.ch).collect()
    }

    #[test]
    fn hunk_is_a_git_hunk() {
        let s = Slots::default();
        let rows = diff_lines(&s, Some(100), DIFF_INDENT);
        assert_eq!(rows.len(), DIFF_BODY.len() + 1);
        let plain: Vec<String> = rows.iter().map(text).collect();
        assert_eq!(plain[0], format!("    {} {} {}", DIFF_HUNK.0, DIFF_HUNK.1, DIFF_HUNK.2));
        assert!(plain[1].trim_start().starts_with('-'));
        assert!(plain[2].trim_start().starts_with('+'));
    }

    #[test]
    fn signs_wear_base_red_and_green() {
        assert_eq!((DIFF_REMOVED, DIFF_ADDED), ("palette-1", "palette-2"));
        assert_eq!(DIFF_MARKS, "palette-6");
        assert_eq!(DIFF_CONTEXT, "palette-8");
        let mut s = Slots::default();
        s.set(DIFF_REMOVED, "#ff0000");
        s.set(DIFF_ADDED, "#00ff00");
        s.set(DIFF_MARKS, "#00ffff");
        s.set(DIFF_CONTEXT, "#808080");
        let rows = diff_lines(&s, Some(100), DIFF_INDENT);
        assert!(rows[1][4..].iter().all(|c| c.fg == "#ff0000"));
        assert!(rows[2][4..].iter().all(|c| c.fg == "#00ff00"));
        assert_eq!(rows[0][4].fg, "#00ffff");
        assert_eq!(rows[0][6].fg, "#808080");
    }

    #[test]
    fn one_slot_change_moves_only_its_lines() {
        let base = Slots::default();
        let mut after = base.clone();
        after.set(DIFF_REMOVED, "#ff00ff");
        let (before, moved) = (
            diff_lines(&base, Some(100), DIFF_INDENT),
            diff_lines(&after, Some(100), DIFF_INDENT),
        );
        for index in [1, 3] {
            assert_ne!(before[index], moved[index]);
        }
        for index in [0, 2, 4] {
            assert_eq!(before[index], moved[index]);
        }
    }

    #[test]
    fn rows_fit_and_are_ascii() {
        let s = Slots::default();
        for row in diff_lines(&s, Some(100), DIFF_INDENT) {
            let t = text(&row);
            assert!(t.len() <= 78, "{t:?}");
            assert!(t.is_ascii(), "{t:?}");
        }
        for cols in [120, 80, 60, 40, 38, 30, 24, 20, 10] {
            for row in diff_lines(&s, Some(cols), DIFF_INDENT) {
                assert!(row_visible(&row) <= cols);
            }
        }
    }

    #[test]
    fn missing_slots_render_as_grey() {
        let rows = diff_lines(&Slots::default(), Some(80), DIFF_INDENT);
        assert!(rows
            .iter()
            .flat_map(|r| r.iter())
            .any(|c| c.fg == crate::MISSING));
    }
}
