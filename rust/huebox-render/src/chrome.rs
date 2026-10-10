//! Frame typography: `chrome` / `title` / `wordmark` (§8.1).
//!
//! Rust port of the same section of `huebox/render.py`. The frame's own
//! vocabulary is read out of the live buffer like everything else: a key
//! is what you press, a label is what it does, muted text is the furniture
//! around them. Painted via [`chrome`], never with DIM — no terminal
//! attribute a theme cannot change appears in the frame.

use huebox_core::Slots;

use crate::{Row, painted, plain, slot_hex};

/// `arrows`, `Ctrl+S`: the half you look for.
pub const CHROME_KEY: &str = "palette-11";
/// The label beside a key: `arrows` **move**.
pub const CHROME_LABEL: &str = "palette-14";
/// A path, a hex, hue/sat/val, a counter.
pub const CHROME_MUTED: &str = "palette-8";

/// The frame's only ornament, at the top where it is read once.
pub const WORDMARK: &str = "huebox";
/// One letter, one colour, the six bright hues in order.
pub const WORDMARK_SLOTS: [&str; 6] = [
    "palette-9",
    "palette-10",
    "palette-11",
    "palette-12",
    "palette-13",
    "palette-14",
];

/// One run of frame text, painted from a slot in the live buffer.
///
/// Everything the frame says about itself comes through here, so a chrome
/// slot the buffer moves repaints the chrome on the same frame as
/// everything else (§14.1). The Python `bold` has no cell to live in —
/// [`Cell`](crate::Cell) is `(ch, fg, bg)` and the grid harness compares
/// exactly that triple — so downstream maps emphasis from position, and
/// this function takes none.
pub fn chrome(text: &str, slot: &str, slots: &Slots) -> Row {
    painted(text, slot_hex(slots, slot), "")
}

/// The `huebox` wordmark: one letter in one colour, in palette order.
///
/// Six letters, the six bright hues, cycled so a longer word still
/// colours. Live like everything else: editing a bright slot moves the
/// letter on the same frame.
pub fn wordmark(slots: &Slots, text: &str) -> Row {
    text.chars()
        .zip(WORDMARK_SLOTS.iter().cycle())
        .flat_map(|(ch, slot)| painted(&ch.to_string(), slot_hex(slots, slot), ""))
        .collect()
}

/// The `huebox` wordmark with its default text.
pub fn wordmark_default(slots: &Slots) -> Row {
    wordmark(slots, WORDMARK)
}

/// A frame header: the word in the theme's foreground, its aside muted.
///
/// `note` is the parenthetical beside it — `(live buffer: …)` — which is
/// commentary, so it recedes.
pub fn title(word: &str, slots: &Slots, note: &str) -> Row {
    let mut row = chrome(word, "foreground", slots);
    if !note.is_empty() {
        row.extend(plain(" "));
        row.extend(chrome(note, CHROME_MUTED, slots));
    }
    row
}

/// `arrows move` for a hint line: the key bright, its label beside it.
///
/// The key is the half the reader is hunting for — it is what the finger
/// has to find — so it wears the brightest thing in the line and the label
/// reads as its explanation.
pub fn key_hint(slots: &Slots, key: &str, what: &str) -> String {
    // Hints fold through `pack` as plain text in this crate (paint comes
    // from parallel runs); the pair is joined here so the fold never
    // splits a key from its label. Kept as text: see `hint_cells`.
    let _ = slots;
    format!("{key} {what}")
}

/// Hint rows for `hints` as `(key, what)` pairs, folded to `cols`.
///
/// Returns `(plain_lines, cells)`: the folded text plus the painted cells
/// per line, so `pack` folds on columns while the paint rides along.
pub fn hint_line(slots: &Slots, hints: &[(&str, &str)], cols: usize) -> Vec<Row> {
    let items: Vec<String> = hints
        .iter()
        .map(|(key, what)| key_hint(slots, key, what))
        .collect();
    crate::layout::pack(items, cols, "  ")
        .into_iter()
        .map(|line| hint_cells(slots, hints, &line))
        .collect()
}

/// Paint one folded hint line: keys bright, labels beside them.
fn hint_cells(slots: &Slots, hints: &[(&str, &str)], line: &str) -> Row {
    let mut row = Row::new();
    let mut first = true;
    // Split back into whole hints: the fold only ever joins with two
    // spaces, and no key or label contains one.
    for part in line.split("  ") {
        let hit = hints
            .iter()
            .find(|(key, what)| format!("{key} {what}") == part);
        if !first {
            row.extend(plain("  "));
        }
        first = false;
        match hit {
            Some((key, what)) => {
                row.extend(chrome(key, CHROME_KEY, slots));
                row.extend(plain(" "));
                row.extend(chrome(what, CHROME_LABEL, slots));
            }
            None => row.extend(plain(part)),
        }
    }
    row
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::visible;
    use huebox_core::Slots;

    fn slots() -> Slots {
        let mut s = Slots::default();
        for (slot, hex) in [
            ("palette-8", "#d0d0d8"),
            ("palette-9", "#e06c6c"),
            ("palette-10", "#6cc06c"),
            ("palette-11", "#e0c06c"),
            ("palette-12", "#6c9ce0"),
            ("palette-13", "#e06c9c"),
            ("palette-14", "#6cc0c0"),
            ("foreground", "#e6e6ea"),
            ("background", "#101014"),
        ] {
            s.set(slot, hex);
        }
        s
    }

    #[test]
    fn chrome_reads_the_slot_live() {
        let s = slots();
        let row = chrome("hi", CHROME_MUTED, &s);
        assert!(row.iter().all(|c| c.fg == "#d0d0d8" && c.bg.is_empty()));
        assert_eq!(row.iter().map(|c| c.ch).collect::<String>(), "hi");
        // a slot the buffer has not got paints MISSING in place
        let row = chrome("hi", "palette-99", &s);
        assert!(row.iter().all(|c| c.fg == crate::MISSING));
    }

    #[test]
    fn wordmark_is_one_letter_one_colour() {
        let s = slots();
        let row = wordmark_default(&s);
        let text: String = row.iter().map(|c| c.ch).collect();
        assert_eq!(text, WORDMARK);
        let inks: Vec<&str> = row.iter().map(|c| c.fg.as_str()).collect();
        assert_eq!(
            inks,
            vec!["#e06c6c", "#6cc06c", "#e0c06c", "#6c9ce0", "#e06c9c", "#6cc0c0"]
        );
        // live: the wordmark follows the buffer
        let mut moved = s.clone();
        moved.set(WORDMARK_SLOTS[0], "#ff00ff");
        assert_ne!(wordmark_default(&s), wordmark_default(&moved));
    }

    #[test]
    fn headers_wear_foreground_with_muted_asides() {
        let s = slots();
        let row = title("palette", &s, "");
        assert_eq!(row.iter().map(|c| c.ch).collect::<String>(), "palette");
        assert!(row.iter().all(|c| c.fg == "#e6e6ea"));
        let row = title("examples", &s, "(live buffer: ...)");
        let text: String = row.iter().map(|c| c.ch).collect();
        assert_eq!(text, "examples (live buffer: ...)");
        assert!(row[9..].iter().all(|c| c.fg == "#d0d0d8"));
    }

    #[test]
    fn folds_never_split_a_key_from_its_label() {
        let s = slots();
        let hints = [
            ("arrows", "move"),
            ("w/e", "hue"),
            ("s/d", "sat"),
            ("x/c", "light"),
            ("Esc", "quit"),
        ];
        for cols in [120, 80, 60, 40, 30] {
            for row in hint_line(&s, &hints, cols) {
                let text: String = row.iter().map(|c| c.ch).collect();
                assert!(visible(&text) <= cols, "{text:?} at {cols}");
                for part in text.split("  ") {
                    assert!(hints.iter().any(|(k, w)| format!("{k} {w}") == part));
                }
            }
        }
    }

    #[test]
    fn chrome_slots_come_from_the_sample_vocabulary() {
        // §8: no slot is spent twice over — the three chrome slots are
        // among the sample's (see `sample.rs`).
        let spent = crate::sample::slots_spent();
        for slot in [CHROME_KEY, CHROME_LABEL, CHROME_MUTED] {
            assert!(spent.contains(&slot), "{slot}");
        }
    }
}
