//! The HSV readout: readings beside bars, per axis (§8.3).
//!
//! Rust port of `hsv_numbers` / `hsv_readout` / `hsv_axis` in
//! `huebox/render.py`. A bar is a sweep of its whole axis — a legend of
//! what the axis means — with the reading printed *beside* it and the
//! hairline drawn on it, so the number and the line are both always
//! complete.

use crate::core::{hex_to_rgb, hsv_to_rgb, readable_fg, rgb_to_hex, rgb_to_hsv};

use crate::render::chrome::{chrome, CHROME_MUTED};
use crate::render::{plain, Cell, Row};

/// The reading's own hairline through the bar: the thinnest line a cell
/// can draw, one eighth of it wide, set into the cell the reading falls
/// nearest. A left one-eighth block rather than `│` because the glyph is
/// anchored to the cell's edge — the sub-cell position the bar wants.
pub const MARK: char = '\u{258f}';

/// The reading labels, in axis order.
pub const HSV_LABELS: [&str; 3] = ["hue", "sat", "val"];
/// The readings' fixed field: `120°`, ` 48%`, `100%` — a reading that
/// grows a digit must not move the bar after it.
pub const HSV_FIELD: usize = 4;
/// Fixed chrome per readout row: names, readings, gaps, in columns.
pub const HSV_CHROME: usize = 31;

/// Three rungs of the ladder: the bars give way as the row narrows.
pub const HSV_FULL: [usize; 3] = [15, 9, 9];
/// See [`HSV_FULL`].
pub const HSV_COMPACT: [usize; 3] = [11, 7, 7];
/// See [`HSV_FULL`].
pub const HSV_TIGHT: [usize; 3] = [7, 5, 5];
/// Widest first; the readout walks it for the first rung that fits.
pub const HSV_LADDER: [[usize; 3]; 3] = [HSV_FULL, HSV_COMPACT, HSV_TIGHT];

/// `hue 207.0  sat 59.4%  val 93.7%` — the exact reading, below the bars.
pub fn hsv_numbers(hue: f64, sat: f64, val: f64) -> String {
    format!(
        "hue {:5.1}  sat {:4.1}%  val {:4.1}%",
        hue * 360.0,
        sat * 100.0,
        val * 100.0
    )
}

/// `width` steps of an axis, first to last, whole — 0.0 to 1.0.
///
/// The bar is the axis, not a window onto where the reading happens to
/// sit: a hue bar that swept only the neighbourhood would show what is
/// beside the colour, and never the colour's own place in the wheel.
fn sweep(width: usize) -> Vec<f64> {
    (0..width).map(|i| i as f64 / (width - 1) as f64).collect()
}

/// Where the reading's hairline falls: the one cell it draws into.
///
/// Never more than half a cell off; at either end of the axis it lands in
/// the end cell, against that end of the bar.
pub fn marker(at: f64, width: usize) -> Option<usize> {
    if width == 0 {
        return None;
    }
    Some((at + 0.5).floor().clamp(0.0, (width - 1) as f64) as usize)
}

/// One bar: a sweep of the axis with the reading's hairline on it.
///
/// The hairline's ink is the readable side of the cell it is drawn into,
/// the same rule the palette cells' own labels follow. Bar cells carry no
/// ink unless they are the hairline.
fn chip(colour: impl Fn(f64) -> (f64, f64, f64), width: usize, at: f64) -> Row {
    let cells: Vec<String> = sweep(width)
        .into_iter()
        .map(|step| {
            let (r, g, b) = colour(step);
            rgb_to_hex(r, g, b)
        })
        .collect();
    let mark = marker(at, width).unwrap_or(0);
    let fg = readable_fg(&cells[mark]).to_owned();
    cells
        .into_iter()
        .enumerate()
        .map(|(i, cell)| {
            if i == mark {
                Cell::new(MARK, &fg, &cell)
            } else {
                Cell::new(' ', "", &cell)
            }
        })
        .collect()
}

/// Axis painters: hue is the wheel at the slot's own sat/val, sat runs
/// grey → colour, val black → colour, each at the slot's own hue.
fn axes(hue: f64, sat: f64, val: f64) -> [(String, f64, String, char); 3] {
    [
        (
            "hue".to_owned(),
            hue,
            format!("{:>width$.0}°", hue * 360.0, width = HSV_FIELD - 1),
            'h',
        ),
        (
            "sat".to_owned(),
            sat,
            format!("{:>width$.0}%", sat * 100.0, width = HSV_FIELD - 1),
            's',
        ),
        (
            "val".to_owned(),
            val,
            format!("{:>width$.0}%", val * 100.0, width = HSV_FIELD - 1),
            'v',
        ),
    ]
}

/// The slot's hue, saturation and value: a reading and a bar each.
///
/// `hue 207° [bar] sat 59% [bar] val 94% [bar]` — the name, the reading
/// and the axis, in that order, so the hairline never shares a cell with a
/// digit. `cols` is the room the row has left: the ladder picks the first
/// rung that fits — chrome and all — and when none does the row is empty
/// and the caller prints the exact reading below instead.
pub fn hsv_readout(slots: &crate::core::Slots, value: &str, cols: usize) -> Row {
    let (r, g, b) = hex_to_rgb(value);
    let (hue, sat, val) = rgb_to_hsv(r, g, b);
    let rung = HSV_LADDER
        .iter()
        .find(|sizes| cols >= HSV_CHROME + sizes.iter().sum::<usize>());
    let Some(widths) = rung else { return vec![] };
    readout_row(slots, hue, sat, val, widths)
}

fn readout_row(
    slots: &crate::core::Slots,
    hue: f64,
    sat: f64,
    val: f64,
    widths: &[usize; 3],
) -> Row {
    let mut row = Row::new();
    for (i, (label, reading, number, _axis)) in axes(hue, sat, val).iter().enumerate() {
        if i > 0 {
            row.extend(plain("  "));
        }
        row.extend(chrome(label, CHROME_MUTED, slots));
        row.extend(plain(" "));
        row.extend(chrome(number, CHROME_MUTED, slots));
        row.extend(plain(" "));
        let cells = widths[i];
        let at = reading * (cells - 1) as f64;
        match i {
            0 => row.extend(chip(|t| hsv_to_rgb(t, sat, val), cells, at)),
            1 => row.extend(chip(|t| hsv_to_rgb(hue, t, val), cells, at)),
            _ => row.extend(chip(|t| hsv_to_rgb(hue, sat, t), cells, at)),
        };
    }
    row
}

/// One HSV axis: `hue 207° [bar]` — a stacked readout row.
///
/// Same chrome/number/ink rules as [`hsv_readout`], but a single row with
/// an explicit `bar_w` so three stacked rows share one width. Empty when
/// `bar_w` holds no bar.
pub fn hsv_axis(slots: &crate::core::Slots, value: &str, axis: usize, bar_w: usize) -> Row {
    if bar_w < 2 {
        return vec![];
    }
    let (r, g, b) = hex_to_rgb(value);
    let (hue, sat, val) = rgb_to_hsv(r, g, b);
    let (label, reading, number, _) = &axes(hue, sat, val)[axis.min(2)];
    let mut row = chrome(label, "foreground", slots);
    row.extend(plain(" "));
    row.extend(chrome(number, "foreground", slots));
    row.extend(plain("  "));
    let at = reading * (bar_w - 1) as f64;
    match axis {
        0 => row.extend(chip(|t| hsv_to_rgb(t, sat, val), bar_w, at)),
        1 => row.extend(chip(|t| hsv_to_rgb(hue, t, val), bar_w, at)),
        _ => row.extend(chip(|t| hsv_to_rgb(hue, sat, t), bar_w, at)),
    };
    row
}

/// Paint helper for tests: the plain text of a row.
#[cfg(test)]
pub fn row_text(row: &[Cell]) -> String {
    row.iter().map(|c| c.ch).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Slots;
    use crate::render::layout::row_visible;

    fn slots() -> Slots {
        let mut s = Slots::default();
        s.set("palette-8", "#d0d0d8");
        s.set("palette-11", "#e0c06c");
        s.set("foreground", "#e6e6ea");
        s.set("background", "#101014");
        s
    }

    /// Bar cells only: every bar cell carries its own background while
    /// chrome carries none — the same split the Python tests' bg-escape
    /// regex sees.
    fn bars_of(row: &[Cell]) -> Vec<&Cell> {
        row.iter().filter(|c| !c.bg.is_empty()).collect()
    }

    fn wide() -> usize {
        HSV_FULL.iter().sum::<usize>() + HSV_CHROME
    }

    #[test]
    fn readings_are_in_fixed_fields_so_bars_never_move() {
        let s = slots();
        for value in [
            "#090000", "#ff0000", "#00ff00", "#00ffff", "#ffffff", "#61afef",
        ] {
            let row = hsv_readout(&s, value, wide());
            assert_eq!(
                row_visible(&row) - HSV_FULL.iter().sum::<usize>(),
                HSV_CHROME
            );
            assert_eq!(row_visible(&row), wide());
        }
        assert_eq!(HSV_CHROME, 31);
        assert!(row_text(&hsv_readout(&s, "#00ff00", wide())).starts_with("hue 120° "));
        assert!(row_text(&hsv_readout(&s, "#090000", wide())).starts_with("hue   0° "));
    }

    #[test]
    fn wide_room_gives_three_bars_with_readings() {
        let s = slots();
        let row = hsv_readout(&s, "#61afef", wide());
        let text = row_text(&row);
        for (label, reading) in [("hue", "207°"), ("sat", " 59%"), ("val", " 94%")] {
            assert!(text.contains(&format!("{label} {reading} ")), "{text:?}");
        }
        assert_eq!(row.len(), wide());
    }

    #[test]
    fn every_bar_is_a_sweep_of_its_whole_axis() {
        let s = slots();
        let row = hsv_readout(&s, "#61afef", wide());
        let bars = bars_of(&row);
        assert_eq!(bars.len(), HSV_FULL.iter().sum());
        let (r, g, b) = hex_to_rgb("#61afef");
        let (hue, sat, val) = rgb_to_hsv(r, g, b);
        // locate bars: each axis is label+num chrome then bar cells; find
        type Paint = Box<dyn Fn(f64) -> (f64, f64, f64)>;
        let paints: [Paint; 3] = [
            Box::new(move |t| hsv_to_rgb(t, sat, val)),
            Box::new(move |t| hsv_to_rgb(hue, t, val)),
            Box::new(move |t| hsv_to_rgb(hue, sat, t)),
        ];
        // bar cells are contiguous: chrome carries no background
        let mut pos = 0;
        for (w, paint) in HSV_FULL.iter().zip(paints.iter()) {
            let bar = &bars[pos..pos + w];
            let sweep: Vec<String> = (0..*w)
                .map(|i| {
                    let (rr, gg, bb) = paint(i as f64 / (*w - 1) as f64);
                    rgb_to_hex(rr, gg, bb)
                })
                .collect();
            assert_eq!(bar.iter().map(|c| c.bg.clone()).collect::<Vec<_>>(), sweep);
            pos += w;
        }
    }

    #[test]
    fn every_bar_carries_one_hairline_and_nothing_else() {
        let s = slots();
        for value in ["#61afef", "#ff0000", "#ffffff", "#000000", "#010203"] {
            let row = hsv_readout(&s, value, wide());
            let bars = bars_of(&row);
            let (r, g, b) = hex_to_rgb(value);
            let (h, sa, v) = rgb_to_hsv(r, g, b);
            let readings = [h, sa, v];
            let mut pos = 0;
            for (axis, w) in HSV_FULL.iter().enumerate() {
                let bar = &bars[pos..pos + w];
                let want = ((readings[axis] * (*w - 1) as f64 + 0.5).floor() as usize).min(w - 1);
                let marks: Vec<usize> = bar
                    .iter()
                    .enumerate()
                    .filter(|(_, c)| c.ch == MARK)
                    .map(|(i, _)| i)
                    .collect();
                assert_eq!(marks, vec![want], "{value} axis {axis}");
                assert_eq!(bar[want].fg, readable_fg(&bar[want].bg));
                for (i, cell) in bar.iter().enumerate() {
                    if i != want {
                        assert!(cell.fg.is_empty(), "{value} axis {axis} cell {i}");
                    }
                }
                pos += w;
            }
        }
    }

    #[test]
    fn marker_hugs_the_ends() {
        assert_eq!(marker(0.0, 9), Some(0));
        assert_eq!(marker(-1.0, 9), Some(0));
        assert_eq!(marker(8.0, 9), Some(8));
        assert_eq!(marker(99.0, 9), Some(8));
        assert_eq!(marker(4.0, 9), Some(4));
        assert_eq!(marker(4.4, 9), Some(4));
        assert_eq!(marker(4.6, 9), Some(5));
    }

    #[test]
    fn bars_follow_the_reading_they_are_about() {
        let s = slots();
        let before = hsv_readout(&s, "#61afef", wide());
        let after = hsv_readout(&s, "#ef8b61", wide());
        assert_ne!(row_text(&before), row_text(&after));
        // hue wheel is the whole wheel at the same sat/val: same sweep
        let bg = |row: &Row| {
            bars_of(row)[..HSV_FULL[0]]
                .iter()
                .map(|c| c.bg.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(bg(&after), bg(&before));
        assert_eq!(before, hsv_readout(&s, "#61afef", wide()));
    }

    #[test]
    fn nothing_in_a_bar_is_a_terminal_attribute() {
        let s = slots();
        let row = hsv_readout(&s, "#61afef", wide());
        // only bar cells carry bg; chrome cells carry none
        let inked = row.iter().filter(|c| !c.fg.is_empty()).count();
        let text_len = row_text(&row).chars().count();
        assert!(inked < text_len);
        // exactly three hairline inks inside bars
        let bar_ink = bars_of(&row).iter().filter(|c| !c.fg.is_empty()).count();
        assert_eq!(bar_ink, 3);
    }

    #[test]
    fn ladder_picks_widest_fitting_rung() {
        let s = slots();
        for sizes in [HSV_FULL, HSV_COMPACT, HSV_TIGHT] {
            let need = HSV_CHROME + sizes.iter().sum::<usize>();
            let row = hsv_readout(&s, "#61afef", need);
            assert_eq!(row_visible(&row), need);
        }
        assert!(hsv_readout(&s, "#61afef", 40).is_empty());
        for value in ["#61afef", "#000000", "#ffffff"] {
            for cols in [0, 10, 40, 50, 60, 70, wide(), wide() + 4] {
                assert!(row_visible(&hsv_readout(&s, value, cols)) <= cols);
            }
        }
    }

    #[test]
    fn axis_rows_share_one_width() {
        let s = slots();
        let rows: Vec<Row> = (0..3).map(|a| hsv_axis(&s, "#61afef", a, 20)).collect();
        assert!(rows.iter().all(|r| row_visible(r) == row_visible(&rows[0])));
        assert!(hsv_axis(&s, "#61afef", 0, 1).is_empty());
    }

    #[test]
    fn numbers_spell_the_exact_reading() {
        assert!(hsv_numbers(207.0 / 360.0, 0.594, 0.937).contains("207.0"));
    }
}
