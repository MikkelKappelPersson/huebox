//! The footer bar: hint pairs plus the status, folded by width.
//!
//! Rust port of `app.py`'s `_fold_footer` plus the `HueFooter` paint: the
//! pairs come from the one source (`editor_hints` headless,
//! `THEME_HINTS` / `SETUP_HINTS` / `OVERRIDES_HINTS` / [`crate::app::dialog`]
//! in dialogs) and the status is `state.status`, so the bar and the frame
//! cannot disagree. The editor bar is one scrolling row; dialogs fold to
//! two lines with the status last and never dropped.
//!
//! Paint is theme-closed like every frame: keys wear `palette-11` bold
//! (what `key_hint` paints inline), labels `palette-14`, the status the
//! foreground bold — the same slots the Textual tokens bound, so no
//! compositor blue and no hover wash.

use crate::core::Slots;
use crate::render::{slot_hex, Row};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

use crate::app::cells::hex_color;

/// One folded bar row: its pairs plus its status (`None` = pairs only).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FooterFold {
    /// `(key, what)` pairs riding this row.
    pub pairs: Vec<(String, String)>,
    /// The status when it rides this row.
    pub status: Option<String>,
}

/// One row if it fits, else two rows folded by width.
///
/// The first row takes pairs while they fit; the rest ride the second,
/// with the status last — never dropped, its overflow scrolling in the
/// bar instead of clipping. Verbatim port of `_fold_footer` (widths are
/// display columns; `chars().count()` is the `len()` the Python counts).
pub fn fold_footer(pairs: &[(&str, &str)], status: &str, width: usize) -> Vec<FooterFold> {
    let wide = |key: &str, what: &str| key.chars().count() + 1 + what.chars().count();
    let mut row0: Vec<(String, String)> = vec![];
    let mut row1: Vec<(String, String)> = vec![];
    let mut taken = 0usize;
    for (key, what) in pairs {
        let pair_wide = wide(key, what);
        if taken == 0 || taken + 2 + pair_wide <= width {
            row0.push(((*key).to_owned(), (*what).to_owned()));
            taken += pair_wide + if taken == 0 { 0 } else { 2 };
        } else {
            row1.push(((*key).to_owned(), (*what).to_owned()));
        }
    }
    if !status.is_empty() {
        if row1.is_empty() && (taken == 0 || taken + 2 + status.chars().count() <= width) {
            return vec![FooterFold {
                pairs: row0,
                status: Some(status.to_owned()),
            }];
        }
        return vec![
            FooterFold {
                pairs: row0,
                status: None,
            },
            FooterFold {
                pairs: row1,
                status: Some(status.to_owned()),
            },
        ];
    }
    if row1.is_empty() {
        return vec![FooterFold {
            pairs: row0,
            status: None,
        }];
    }
    vec![
        FooterFold {
            pairs: row0,
            status: None,
        },
        FooterFold {
            pairs: row1,
            status: None,
        },
    ]
}

/// One folded row as a bar line: bright bold keys, quiet labels, bold
/// status — padded in the buffer background to the full width.
pub fn bar_line(fold: &FooterFold, cols: usize, slots: &Slots) -> Line<'static> {
    let bg = hex_color(slot_hex(slots, "background"));
    let key_fg = hex_color(slot_hex(slots, "palette-11"));
    let label_fg = hex_color(slot_hex(slots, "palette-14"));
    let status_fg = hex_color(slot_hex(slots, "foreground"));
    let mut spans: Vec<Span<'static>> = vec![];
    let mut first = true;
    for (key, what) in &fold.pairs {
        if !first {
            spans.push(Span::styled("  ", Style::new().fg(Color::Reset).bg(bg)));
        }
        first = false;
        spans.push(Span::styled(
            key.clone(),
            Style::new().fg(key_fg).bg(bg).add_modifier(Modifier::BOLD),
        ));
        // The key's own air wears the label ink, like the widget's
        // description padding it mirrors.
        spans.push(Span::styled(" ", Style::new().fg(label_fg).bg(bg)));
        spans.push(Span::styled(what.clone(), Style::new().fg(label_fg).bg(bg)));
    }
    if let Some(status) = &fold.status {
        // The status always keeps its two air cells on the left — the
        // widget's own left margin — whether pairs share the row or not.
        spans.push(Span::styled("  ", Style::new().fg(Color::Reset).bg(bg)));
        spans.push(Span::styled(
            status.clone(),
            Style::new()
                .fg(status_fg)
                .bg(bg)
                .add_modifier(Modifier::BOLD),
        ));
    }
    // The bar fills the width in the buffer background, like every frame row.
    let mut text_len: usize = spans.iter().map(|span| span.content.chars().count()).sum();
    while text_len < cols {
        spans.push(Span::styled(" ", Style::new().fg(Color::Reset).bg(bg)));
        text_len += 1;
    }
    let mut line = Line::from(spans);
    line.style = Style::new().bg(bg);
    line
}

/// The main editor bar as ONE line: every pair plus the status,
/// hard-cut at `cols` — the Textual footer this mirrors never folds,
/// it truncates mid-row at the window edge. Dialog bars keep the
/// folding [`bar_lines`]; only the bottom-docked editor bar stands.
pub fn bar_line_single(
    pairs: &[(&str, &str)],
    status: &str,
    cols: usize,
    slots: &Slots,
) -> Line<'static> {
    let all: Vec<(String, String)> = pairs
        .iter()
        .map(|(key, what)| ((*key).to_owned(), (*what).to_owned()))
        .collect();
    let refs: Vec<(String, String)> = all;
    let fold = FooterFold {
        pairs: refs,
        status: if status.is_empty() {
            None
        } else {
            Some(status.to_owned())
        },
    };
    // `cols: 0` skips `bar_line`'s fill-to-width padding, leaving the
    // full unpadded row to cut down below.
    let line = bar_line(&fold, 0, slots);
    let mut spans: Vec<Span<'static>> = vec![];
    let mut taken = 0usize;
    for span in line.spans {
        if taken >= cols {
            break;
        }
        let rest = cols - taken;
        let text: String = span.content.chars().take(rest).collect();
        taken += text.chars().count();
        if text.is_empty() {
            continue;
        }
        spans.push(Span::styled(text, span.style));
    }
    let bg = hex_color(slot_hex(slots, "background"));
    while taken < cols {
        spans.push(Span::styled(" ", Style::new().fg(Color::Reset).bg(bg)));
        taken += 1;
    }
    let mut line = Line::from(spans);
    line.style = Style::new().bg(bg);
    line
}

/// The folded bar as lines, bottom-docked by the caller.
pub fn bar_lines(
    pairs: &[(&str, &str)],
    status: &str,
    cols: usize,
    slots: &Slots,
) -> Vec<Line<'static>> {
    fold_footer(pairs, status, cols)
        .iter()
        .map(|fold| bar_line(fold, cols, slots))
        .collect()
}

/// The editor bar's pairs from the headless source: keys only, no status.
pub fn editor_pairs(undo_len: usize) -> Vec<(String, String)> {
    crate::editor::editor_hints(undo_len)
}

/// Row text of a [`Row`], for width accounting in tests.
#[allow(dead_code)]
pub fn row_text(row: &Row) -> String {
    row.iter().map(|cell| cell.ch).collect()
}

/// Assert a bar line paints only theme-closed colours: background fill,
/// `palette-11` keys, `palette-14` labels, foreground status.
#[allow(dead_code)]
pub fn assert_bar_closed(line: &Line<'_>, bg: Color) {
    for span in &line.spans {
        assert_eq!(span.style.bg, Some(bg), "{span:?}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pairs() -> Vec<(&'static str, &'static str)> {
        vec![("arrows", "move"), ("Enter", "choose"), ("i", "import")]
    }

    #[test]
    fn fits_is_one_row_with_status() {
        let folds = fold_footer(&pairs(), "ok", 60);
        assert_eq!(folds.len(), 1);
        assert_eq!(folds[0].pairs.len(), 3);
        assert_eq!(folds[0].status.as_deref(), Some("ok"));
    }

    #[test]
    fn overflow_folds_with_status_last() {
        let folds = fold_footer(&pairs(), "ok", 20);
        assert_eq!(folds.len(), 2);
        assert_eq!(folds[1].status.as_deref(), Some("ok"));
        // No pair is dropped across the fold.
        let kept: usize = folds.iter().map(|fold| fold.pairs.len()).sum();
        assert_eq!(kept, 3);
    }

    #[test]
    fn status_never_dropped_when_nothing_fits() {
        let folds = fold_footer(&pairs(), "a long status line", 4);
        assert_eq!(folds.len(), 2);
        assert_eq!(folds[1].status.as_deref(), Some("a long status line"));
    }

    #[test]
    fn empty_status_rides_no_row() {
        let folds = fold_footer(&pairs(), "", 60);
        assert_eq!(
            folds,
            vec![FooterFold {
                pairs: vec![
                    ("arrows".to_owned(), "move".to_owned()),
                    ("Enter".to_owned(), "choose".to_owned()),
                    ("i".to_owned(), "import".to_owned()),
                ],
                status: None,
            }]
        );
    }

    #[test]
    fn bar_line_paint_and_bold() {
        let mut slots = Slots::default();
        slots.set("background", "#101014");
        slots.set("foreground", "#e6e6ea");
        slots.set("palette-11", "#e0c06c");
        slots.set("palette-14", "#6cc0c0");
        let folds = fold_footer(&pairs(), "saved", 60);
        assert_eq!(folds.len(), 1);
        let line = bar_line(&folds[0], 60, &slots);
        let bg = Color::Rgb(0x10, 0x10, 0x14);
        assert_bar_closed(&line, bg);
        let key = line
            .spans
            .iter()
            .find(|span| span.content == "arrows")
            .expect("key span");
        assert_eq!(key.style.fg, Some(Color::Rgb(0xe0, 0xc0, 0x6c)));
        assert!(key.style.add_modifier.contains(Modifier::BOLD));
        let status = line
            .spans
            .iter()
            .find(|span| span.content == "saved")
            .expect("status span");
        assert_eq!(status.style.fg, Some(Color::Rgb(0xe6, 0xe6, 0xea)));
        assert!(status.style.add_modifier.contains(Modifier::BOLD));
        // Full-width fill, no DIM anywhere.
        assert_eq!(
            line.spans
                .iter()
                .map(|span| span.content.chars().count())
                .sum::<usize>(),
            60
        );
        for span in &line.spans {
            assert!(!span.style.add_modifier.contains(Modifier::DIM));
        }
    }
}
