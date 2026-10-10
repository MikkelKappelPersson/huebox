//! The live examples strip: background, selection, cursor (§14.1).
//!
//! Rust port of `example_lines` in `huebox/render.py`. Pure like the
//! sample: every colour is read out of `slots` on each call, so a single
//! slot change moves the background, the highlight and the cursor on the
//! same frame. Each row names the pair of slots it demonstrates and shows
//! the colour itself: no hex, the swatch is the readout.

use huebox_core::Slots;

use crate::chrome::chrome;
use crate::layout::{clip_row, pack, row_visible};
use crate::{Row, painted, plain, slot_hex};

/// The sentence every row demonstrates.
pub const EXAMPLE_PHRASE: &str = "The quick brown fox jumps over the lazy dog";
/// The character the cursor sits on.
pub const CURSOR_INDEX: usize = 8;
/// Where the selected run starts.
pub const SELECTION_INDEX: usize = 4;
/// Length of the selected run.
pub const SELECTION_LEN: usize = 15;

/// The selected run of words.
pub const SELECTED_TEXT: &str = "quick brown fox";
/// The character under the cursor.
pub const CURSOR_CHAR: &str = "k";

/// Widest short name (20) + one padding column.
pub const LABEL_WIDTH: usize = 21;
/// Widest pair label (31) + one padding column.
pub const PAIR_WIDTH: usize = 32;
/// A phrase worth showing; the pairs yield to it.
pub const MIN_SAMPLE: usize = 24;
/// Below this the pair names would eat the sentence.
pub const PAIR_MIN_COLS: usize = 2 + PAIR_WIDTH + MIN_SAMPLE;

/// `(head, span, tail)`: the phrase cut around one span.
fn split(phrase: &str, start: usize, length: usize) -> (String, String, String) {
    let chars: Vec<char> = phrase.chars().collect();
    let head: String = chars[..start].iter().collect();
    let span: String = chars[start..start + length].iter().collect();
    let tail: String = chars[start + length..].iter().collect();
    (head, span, tail)
}

/// `selection-background/foreground` — a shared prefix is printed once.
///
/// The strip names both slots of the pair it demonstrates, so the reader
/// never has to guess which foreground rides on the swatch.
pub fn pair_label(bg_slot: &str, fg_slot: &str) -> String {
    let mut common = 0;
    for (a, b) in bg_slot.chars().zip(fg_slot.chars()) {
        if a != b {
            break;
        }
        common += a.len_utf8();
    }
    if common > 0 && bg_slot.as_bytes()[common - 1] == b'-' {
        format!("{bg_slot}/{}", &fg_slot[common..])
    } else {
        format!("{bg_slot}/{fg_slot}")
    }
}

/// One examples-strip row: label, slot pair, painted segments.
type ExampleRow = (&'static str, (&'static str, &'static str), Vec<(String, String, String)>);

/// The three live example rows: background, selection, cursor.
///
/// `cols` is the width the caller can spend (`None`: labelled but
/// unfilled — no fill padding, no clip). The sample text folds to fit
/// through `pack`, and what is left of each row is padded in the buffer's
/// `background` — a resolved value, never the demonstrated colour past
/// its own span. `indent` is the row head's own air: the bare frame's two
/// columns, empty inside bordered panels.
pub fn example_lines(slots: &Slots, cols: Option<usize>, indent: &str) -> Vec<Row> {
    let fill = slot_hex(slots, "background").to_owned();
    let rows: [ExampleRow; 3] = {
        let (sel_head, sel_text, sel_tail) =
            split(EXAMPLE_PHRASE, SELECTION_INDEX, SELECTION_LEN);
        let (cur_head, cur_char, cur_tail) = split(EXAMPLE_PHRASE, CURSOR_INDEX, 1);
        [
            (
                "background",
                ("background", "foreground"),
                vec![(EXAMPLE_PHRASE.to_owned(), "background".to_owned(), "foreground".to_owned())],
            ),
            (
                "selection-background",
                ("selection-background", "selection-foreground"),
                vec![
                    (sel_head, "background".to_owned(), "foreground".to_owned()),
                    (sel_text, "selection-background".to_owned(), "selection-foreground".to_owned()),
                    (sel_tail, "background".to_owned(), "foreground".to_owned()),
                ],
            ),
            (
                "cursor-color",
                ("cursor-color", "cursor-text"),
                vec![
                    (cur_head, "background".to_owned(), "foreground".to_owned()),
                    (cur_char, "cursor-color".to_owned(), "cursor-text".to_owned()),
                    (cur_tail, "background".to_owned(), "foreground".to_owned()),
                ],
            ),
        ]
    };
    let pairs = cols.is_none_or(|c| c >= PAIR_MIN_COLS);
    let label_width = if pairs { PAIR_WIDTH } else { LABEL_WIDTH };
    let mut out = Vec::new();
    for (name, pair, segments) in rows {
        let label = if pairs { pair_label(pair.0, pair.1) } else { name.to_owned() };
        let mut head = plain(indent);
        head.extend(chrome(&format!("{label:<label_width$} "), "foreground", slots));
        let room = cols.map(|c| c.saturating_sub(row_visible(&head)));
        let plain_text: String = segments.iter().map(|(chunk, _, _)| chunk.as_str()).collect();
        // pack keeps whole words: fold instead of cutting mid-word
        let folded = match room {
            Some(r) => pack(
                plain_text.split(' ').map(str::to_owned).collect(),
                r.saturating_sub(2).max(4),
                " ",
            )
            .into_iter()
            .next()
            .unwrap_or_default(),
            None => plain_text.clone(),
        };
        let mut painted_row = Row::new();
        let mut rest = folded.as_str();
        for (chunk, chunk_bg, chunk_fg) in &segments {
            // consume the folded text segment by segment: the tail of a
            // long segment falls away with the fold, shorter ones drop out
            let take = rest.len().min(chunk.len());
            let (take_text, remaining) = rest.split_at(take);
            rest = remaining;
            if !take_text.is_empty() {
                painted_row.extend(painted(
                    take_text,
                    slot_hex(slots, chunk_fg),
                    slot_hex(slots, chunk_bg),
                ));
            }
        }
        if let Some(r) = room {
            let pad = r.saturating_sub(folded.len());
            painted_row.extend(painted(&" ".repeat(pad), "", &fill));
        }
        let mut row = head;
        row.extend(painted_row);
        out.push(match cols {
            Some(c) => clip_row(&row, c),
            None => row,
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::visible;

    fn slots() -> Slots {
        let mut s = Slots::default();
        for (slot, hex) in [
            ("background", "#101014"),
            ("foreground", "#e6e6ea"),
            ("selection-background", "#2a2a34"),
            ("selection-foreground", "#f0f0f8"),
            ("cursor-color", "#e6e6ea"),
            ("cursor-text", "#101014"),
        ] {
            s.set(slot, hex);
        }
        s
    }

    fn text(row: &Row) -> String {
        row.iter().map(|c| c.ch).collect()
    }

    #[test]
    fn splits_cut_the_phrase() {
        let (h, m, t) = split(EXAMPLE_PHRASE, SELECTION_INDEX, SELECTION_LEN);
        assert_eq!(m, SELECTED_TEXT);
        assert_eq!(format!("{h}{m}{t}"), EXAMPLE_PHRASE);
        let (h, m, t) = split(EXAMPLE_PHRASE, CURSOR_INDEX, 1);
        assert_eq!(m, CURSOR_CHAR);
        assert_eq!(format!("{h}{m}{t}"), EXAMPLE_PHRASE);
    }

    #[test]
    fn three_rows_label_their_pair_and_no_hex() {
        let s = slots();
        let rows = example_lines(&s, Some(100), "  ");
        assert_eq!(rows.len(), 3);
        let pairs = [
            "background/foreground",
            "selection-background/foreground",
            "cursor-color/text",
        ];
        assert_eq!(
            [
                pair_label("background", "foreground"),
                pair_label("selection-background", "selection-foreground"),
                pair_label("cursor-color", "cursor-text"),
            ],
            pairs
        );
        for (row, label) in rows.iter().zip(pairs) {
            assert!(text(row).contains(label), "{}", text(row));
            assert!(!text(row).contains('#'), "{}", text(row));
        }
    }

    #[test]
    fn label_column_fits_the_longest_pair() {
        assert_eq!(PAIR_WIDTH - 1, "selection-background/foreground".len());
        assert_eq!(LABEL_WIDTH - 1, "selection-background".len());
    }

    #[test]
    fn narrow_rows_fall_back_to_short_names() {
        let s = slots();
        let below = example_lines(&s, Some(PAIR_MIN_COLS - 1), "  ");
        let at = example_lines(&s, Some(PAIR_MIN_COLS), "  ");
        assert!(text(&below[1]).contains("selection-background  "));
        assert!(text(&at[1]).contains("selection-background/foreground "));
        for row in &below {
            assert!(!text(row).contains('/'));
        }
    }

    #[test]
    fn selection_and_cursor_ride_on_their_colours() {
        let s = slots();
        let rows = example_lines(&s, Some(100), "  ");
        let sel: Vec<(&String, &String)> = rows[1]
            .iter()
            .map(|c| (&c.bg, &c.fg))
            .collect();
        let span: String = rows[1].iter().map(|c| c.ch).collect();
        let at = span.find(SELECTED_TEXT).unwrap();
        let cells: Vec<char> = span.chars().collect();
        let idx: usize = cells[..].iter().take(at).count();
        // the selected words sit on selection-background…
        assert!(rows[1][idx..idx + SELECTED_TEXT.len()].iter().all(|c| c.bg == "#2a2a34" && c.fg == "#f0f0f8"));
        // …exactly once, and the tail is background again
        assert_eq!(rows[1].iter().filter(|c| c.bg == "#2a2a34").count(), SELECTED_TEXT.len());
        let _ = sel;
        let cur: String = rows[2].iter().map(|c| c.ch).collect();
        let at = cur.find(CURSOR_CHAR).unwrap();
        let cell = &rows[2][cur[..at].chars().count()];
        assert_eq!((cell.bg.as_str(), cell.fg.as_str()), ("#e6e6ea", "#101014"));
    }

    #[test]
    fn one_slot_change_moves_its_row() {
        let base = Slots::default();
        let owners: [(&str, &[usize]); 6] = [
            ("background", &[0, 1, 2]),
            ("foreground", &[0, 1, 2]),
            ("selection-background", &[1]),
            ("selection-foreground", &[1]),
            ("cursor-color", &[2]),
            ("cursor-text", &[2]),
        ];
        for (name, owned) in owners {
            let mut changed = base.clone();
            changed.set(name, "#ff00ff");
            let before = example_lines(&base, Some(100), "  ");
            let after = example_lines(&changed, Some(100), "  ");
            for row in 0..3 {
                if owned.contains(&row) {
                    assert_ne!(before[row], after[row], "{name} row {row}");
                } else {
                    assert_eq!(before[row], after[row], "{name} row {row}");
                }
            }
        }
    }

    #[test]
    fn rows_never_exceed_the_width() {
        let s = Slots::default();
        for cols in [120, 100, 80, 60, 40, 38, 30, 24, 20, 10] {
            for row in example_lines(&s, Some(cols), "  ") {
                assert!(visible(&text(&row)) <= cols, "{} at {cols}", text(&row));
            }
        }
    }

    #[test]
    fn without_width_rows_are_labelled_but_unfilled() {
        let s = Slots::default();
        let rows = example_lines(&s, None, "  ");
        let labels: Vec<String> = rows.iter().map(|r| text(r).split_whitespace().next().unwrap().to_owned()).collect();
        assert_eq!(
            labels,
            vec![
                "background/foreground",
                "selection-background/foreground",
                "cursor-color/text",
            ]
        );
        assert_eq!(
            text(&rows[1]).trim_start(),
            format!("selection-background/foreground  {EXAMPLE_PHRASE}")
        );
    }

    #[test]
    fn cursor_survives_the_narrowest_width() {
        let s = slots();
        let rows = example_lines(&s, Some(crate::layout::MIN_COLS - 2), "  ");
        let row = &rows[2];
        assert!(row.iter().any(|c| c.ch.to_string() == CURSOR_CHAR
            && c.bg == "#e6e6ea"
            && c.fg == "#101014"));
    }
}
