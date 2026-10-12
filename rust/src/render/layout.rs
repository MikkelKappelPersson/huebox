//! Layout primitives: `visible` / `clip` / `pack` and the size floor.
//!
//! Rust port of the same three functions in `huebox/render.py` plus
//! `MIN_COLS` / `MIN_ROWS` (§15.4). No widget measures width itself —
//! everything folds through these, on display columns, never on string
//! length.

use unicode_width::UnicodeWidthChar;

/// Below this the layout has nothing honest to draw: the frame shows the
/// too-small hint and nothing else (§15.4). Layout policy, so it lives
/// here next to the geometry it guards.
pub const MIN_COLS: usize = 40;
/// Minimum rows for an honest frame (see [`MIN_COLS`]).
pub const MIN_ROWS: usize = 12;

/// Final bytes of the escape sequences `visible` / `clip` skip: SGR and
/// friends, the CSI final byte (mirrors `render.ESCAPE_END`).
const ESCAPE_END: &str = "mABCDEFGHJKSTfmnsulh";

/// Display width of one char the Python way: combining marks cost
/// nothing, East-Asian wide/fullwidth cost two, everything else costs one
/// (control chars included — `east_asian_width` calls them narrow).
fn char_width(ch: char) -> usize {
    ch.width().unwrap_or(1)
}

/// The display columns `text` occupies — the width [`clip`] cuts at.
///
/// An escape costs nothing and a wide glyph costs two, exactly as in
/// `clip`: `pack` folds with it and `clip` truncates with it, so a line
/// measured by one and cut by the other cannot disagree. Escapes are
/// skipped (never emitted — no escape sequences cross this crate's
/// boundary) but still cost nothing, matching the Python semantics the adversarial
/// corpus pins.
pub fn visible(text: &str) -> usize {
    let mut seen = 0;
    let mut it = text.chars().peekable();
    while let Some(ch) = it.next() {
        if ch == '\x1b' {
            for esc in it.by_ref() {
                if ESCAPE_END.contains(esc) {
                    break;
                }
            }
            continue;
        }
        seen += char_width(ch);
    }
    seen
}

/// Display columns of a laid-out row.
pub fn row_visible(row: &[crate::render::Cell]) -> usize {
    row.iter().map(|c| char_width(c.ch)).sum()
}

/// Truncate to `cols` display columns, ignoring escapes and wide glyphs.
///
/// Mirrors `render.clip`: escapes are skipped (and dropped — there is no
/// escape-sequence out), combining marks ride on their base char for free, and a wide
/// glyph at the edge overshoots by one exactly as the Python loop does
/// (it appends, then counts two). The Python `RESET` the truncation
/// appends has no cell equivalent and is dropped.
pub fn clip(text: &str, cols: usize) -> String {
    if cols == 0 {
        return String::new();
    }
    let mut out = String::new();
    let mut seen = 0;
    let mut it = text.chars().peekable();
    while seen < cols {
        let Some(ch) = it.next() else { break };
        if ch == '\x1b' {
            for esc in it.by_ref() {
                if ESCAPE_END.contains(esc) {
                    break;
                }
            }
            continue;
        }
        out.push(ch);
        seen += char_width(ch);
    }
    out
}

/// Truncate a cell row to `cols` display columns (see [`clip`]).
pub fn clip_row(row: &[crate::render::Cell], cols: usize) -> crate::render::Row {
    let mut out = Vec::new();
    let mut seen = 0;
    for cell in row {
        if seen >= cols {
            break;
        }
        out.push(cell.clone());
        seen += char_width(cell.ch);
    }
    out
}

/// Greedily pack short items into lines that fit `cols`.
///
/// Widths are display columns with the escapes left out ([`visible`]), so
/// a caller may hand `pack` painted items and the fold still lands between
/// items. Folding on raw string length would count every colour as columns
/// and cut the line short of the edge.
pub fn pack(items: Vec<String>, cols: usize, sep: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    for item in items {
        let candidate = if current.is_empty() {
            item.clone()
        } else {
            format!("{current}{sep}{item}")
        };
        if visible(&candidate) > cols && !current.is_empty() {
            lines.push(current);
            current = item;
        } else {
            current = candidate;
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_and_ascii_widths() {
        assert_eq!(visible(""), 0);
        assert_eq!(visible("abc"), 3);
        assert_eq!(clip("abc", 2), "ab");
        assert_eq!(clip("abc", 0), "");
        assert_eq!(clip("", 10), "");
    }

    #[test]
    fn wide_glyphs_cost_two() {
        assert_eq!(visible("一二"), 4);
        // a wide glyph at the edge overshoots by one, like the Python loop
        assert_eq!(visible(&clip("ab一", 3)), 4);
        assert_eq!(clip("ab一", 4), "ab一");
    }

    #[test]
    fn combining_marks_ride_free() {
        assert_eq!(visible("e\u{301}"), 1);
        assert_eq!(visible("a\u{301}b"), 2);
        assert_eq!(clip("a\u{301}bc", 2), "a\u{301}b");
    }

    #[test]
    fn escapes_cost_nothing_and_are_dropped() {
        assert_eq!(visible("\x1b[38;2;255;0;0mabc\x1b[0m"), 3);
        assert_eq!(clip("\x1b[1mabc\x1b[0m", 2), "ab");
        assert_eq!(visible("\x1b[H\x1b[2J"), 0);
    }

    #[test]
    fn pack_folds_between_items_by_columns() {
        let rows = pack(
            vec!["aa".to_string(), "bb".to_string(), "cc".to_string()],
            5,
            " ",
        );
        assert_eq!(rows, vec!["aa bb".to_string(), "cc".to_string()]);
        // a single over-wide item still lands on its own row, uncut
        let rows = pack(vec!["abcdef".to_string()], 4, " ");
        assert_eq!(rows, vec!["abcdef".to_string()]);
        assert!(pack(vec![], 80, "  ").is_empty());
    }

    #[test]
    fn pack_ignores_paint_when_folding() {
        let painted = format!("\x1b[38;2;255;0;0m{}\x1b[0m", "aa");
        let rows = pack(vec![painted.clone(), "bb".to_string()], 5, " ");
        assert_eq!(rows.len(), 1);
        let rows = pack(vec![painted, "bb".to_string()], 4, " ");
        assert_eq!(rows.len(), 2);
    }

    #[test]
    fn clip_rows_are_char_aligned() {
        let row = crate::render::painted("a一b", "#ffffff", "");
        assert_eq!(row_visible(&row), 4);
        let cut = clip_row(&row, 3);
        assert_eq!(cut.iter().map(|c| c.ch).collect::<String>(), "a一");
    }

    #[test]
    fn random_inputs_never_exceed_the_width() {
        // no proptest on this box: a small LCG over an adversarial alphabet
        let mut state: u64 = 0x1234_5678_9abc_def0;
        let mut next = move || {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (state >> 33) as usize
        };
        let alphabet = ['a', ' ', '一', 'é', '\u{0301}', '\x1b', 'm', '[', '0'];
        for _ in 0..2000 {
            let n = next() % 12;
            let text: String = (0..n).map(|_| alphabet[next() % alphabet.len()]).collect();
            let cols = next() % 10;
            let cut = clip(&text, cols);
            // escapes never survive, and the cut never runs past cols
            // except by one wide glyph at the edge, like the Python loop
            assert!(!cut.contains('\x1b'), "{text:?}");
            assert!(visible(&cut) <= cols + 1, "{text:?} cols={cols}");
            if !cut.contains('一') {
                assert!(visible(&cut) <= cols, "{text:?} cols={cols}");
            }
        }
    }
}
