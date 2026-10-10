//! The canonical colour model, shared by every supported terminal.
//!
//! Rust port of `huebox/color.py` (spec `docs/004-rust-rewrite/spec.md` §7,
//! algorithms from `research.md` §7). Colour maths ports identically, not
//! approximately: same step sizes, hue wraps while sat/val clamp,
//! `rgb_to_hex` rounds half-to-even and clamps to 0–255, `luminance` keeps
//! its coefficients and the `readable_fg` 140 boundary.
//!
//! Domain conventions (matching the Python wrappers):
//!
//! - hex strings are `"#rrggbb"` (a missing `#` is tolerated on input);
//! - rgb channels are 0–255 floats;
//! - hsv channels are 0–1 floats.
//!
//! This crate depends on nothing but `std`.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

use std::ops::Index;

/// One nudge of the adjust keys: a degree of hue.
pub const HUE_STEP: f64 = 1.0 / 360.0;
/// One nudge of the adjust keys: two percent of sat/val.
pub const CHANNEL_STEP: f64 = 0.02;

/// Fill for slots a source omits (from `huebox/color.py`).
const MISSING: &str = "#808080";

/// Slot names in canonical order: 16 palette entries, then 6 named slots.
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

/// Position of `slot` in canonical order.
///
/// Panics on an unknown slot name; the 22 names above are the whole model.
fn index_of(slot: &str) -> usize {
    NAMES
        .iter()
        .position(|name| *name == slot)
        .unwrap_or_else(|| panic!("huebox-core: unknown slot {slot:?}"))
}

/// The 22 canonical colour slots: hex in, hex out.
///
/// Values are stored verbatim (no normalisation); a fresh `Slots` holds
/// `MISSING` grey in every slot, the same way a Python buffer starts from
/// gaps filled with `MISSING`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Slots {
    values: [String; 22],
}

impl Slots {
    /// Hex for `slot` (panics on an unknown name).
    pub fn get(&self, slot: &str) -> &str {
        self.values[index_of(slot)].as_str()
    }

    /// Replace the hex for `slot` (panics on an unknown name).
    pub fn set(&mut self, slot: &str, hex: &str) {
        let i = index_of(slot);
        self.values[i] = hex.to_owned();
    }

    /// `(name, hex)` pairs in canonical order.
    pub fn iter(&self) -> impl Iterator<Item = (&'static str, &str)> + '_ {
        NAMES
            .iter()
            .copied()
            .zip(self.values.iter().map(String::as_str))
    }
}

impl Default for Slots {
    /// Every slot starts as `MISSING` grey.
    fn default() -> Self {
        Self {
            values: std::array::from_fn(|_| MISSING.to_owned()),
        }
    }
}

impl Index<&str> for Slots {
    type Output = str;

    /// `slots["background"]` reads the hex (panics on an unknown name).
    fn index(&self, slot: &str) -> &str {
        self.get(slot)
    }
}

/// Decode `"#rrggbb"` to 0–255 channel floats.
///
/// A leading `#` is optional (all of them are stripped, as `lstrip("#")`
/// does); digits may be upper or lower case.
///
/// Panics on malformed input — parse errors are the caller's problem.
pub fn hex_to_rgb(hex: &str) -> (f64, f64, f64) {
    let body = hex.trim_start_matches('#');
    let channel = |i: usize| {
        u8::from_str_radix(&body[i..i + 2], 16).expect("huebox-core: hex_to_rgb needs 6 hex digits")
            as f64
    };
    (channel(0), channel(2), channel(4))
}

/// Banker's rounding (half-to-even), matching Python `round()`.
///
/// Rust `f64::round()` is half-away (`2.5 -> 3`); Python gives `2.5 -> 2`.
/// The fixture pins this: `2.5 -> 2`, `3.5 -> 4`, `254.5 -> 254`.
fn round_half_even(x: f64) -> f64 {
    let f = x.floor();
    let d = x - f;
    if d < 0.5 {
        f
    } else if d > 0.5 {
        f + 1.0
    } else if f % 2.0 == 0.0 {
        f
    } else {
        f + 1.0
    }
}

/// Encode 0–255 channel floats as lowercase `"#rrggbb"`.
///
/// Channels clamp to 0–255 and round half-to-even, matching Python
/// `"#%02x%02x%02x" % tuple(... int(round(c)) ...)`.
pub fn rgb_to_hex(r: f64, g: f64, b: f64) -> String {
    let c = |x: f64| round_half_even(x.clamp(0.0, 255.0)) as u8;
    format!("#{:02x}{:02x}{:02x}", c(r), c(g), c(b))
}

/// 0–255 rgb floats to 0–1 `(h, s, v)`.
///
/// `colorsys.rgb_to_hsv` verbatim on the 0–1 normalised channels. Rust `%`
/// keeps the sign (unlike Python), so the hue is normalised with
/// `((x % 1.0) + 1.0) % 1.0` to stay in `0–1` exactly as `(h / 6.0) % 1.0`
/// does in Python.
pub fn rgb_to_hsv(r: f64, g: f64, b: f64) -> (f64, f64, f64) {
    let (r, g, b) = (r / 255.0, g / 255.0, b / 255.0);
    let maxc = r.max(g).max(b);
    let minc = r.min(g).min(b);
    let rangec = maxc - minc;
    let v = maxc;
    if minc == maxc {
        return (0.0, 0.0, v);
    }
    let s = rangec / maxc;
    let rc = (maxc - r) / rangec;
    let gc = (maxc - g) / rangec;
    let bc = (maxc - b) / rangec;
    let h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2.0 + rc - bc
    } else {
        4.0 + gc - rc
    };
    (((h / 6.0) % 1.0 + 1.0) % 1.0, s, v)
}

/// 0–1 `(h, s, v)` to 0–255 rgb floats.
///
/// `colorsys.hsv_to_rgb` verbatim: `(h * 6.0) as i64` truncates toward zero
/// like `int()`, and `((i % 6) + 6) % 6` replays Python's always-positive
/// `%` for negative hues. Output is unclamped, as in Python.
pub fn hsv_to_rgb(h: f64, s: f64, v: f64) -> (f64, f64, f64) {
    if s == 0.0 {
        let g = v * 255.0;
        return (g, g, g);
    }
    let i = (h * 6.0) as i64;
    let f = h * 6.0 - i as f64;
    let p = v * (1.0 - s);
    let q = v * (1.0 - s * f);
    let t = v * (1.0 - s * (1.0 - f));
    let (r, g, b) = match ((i % 6) + 6) % 6 {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    };
    (r * 255.0, g * 255.0, b * 255.0)
}

/// Nudge `(h, s, v)` one step along `axis` (`'h'`, `'s'`, or anything else
/// for `'v'`, mirroring the Python `else` branch).
///
/// `dir` is ±1, `mult` the session step multiplier. Hue wraps via
/// `((x % 1.0) + 1.0) % 1.0` (Rust `%` keeps the sign — normalised);
/// sat/val clamp to 0–1.
pub fn step_hsv(h: f64, s: f64, v: f64, axis: char, dir: i32, mult: f64) -> (f64, f64, f64) {
    let d = dir as f64;
    match axis {
        'h' => (((h + d * HUE_STEP * mult) % 1.0 + 1.0) % 1.0, s, v),
        's' => (h, (s + d * CHANNEL_STEP * mult).clamp(0.0, 1.0), v),
        _ => (h, s, (v + d * CHANNEL_STEP * mult).clamp(0.0, 1.0)),
    }
}

/// Perceived brightness of 0–255 rgb floats (same coefficients as
/// `color.py`: 0.2126 / 0.7152 / 0.0722).
pub fn luminance(r: f64, g: f64, b: f64) -> f64 {
    0.2126 * r + 0.7152 * g + 0.0722 * b
}

/// Black or white, whichever stays legible on `bg_hex`: black past the 140
/// boundary (140 itself reads white), white at or below it.
pub fn readable_fg(bg_hex: &str) -> &'static str {
    let (r, g, b) = hex_to_rgb(bg_hex);
    if luminance(r, g, b) > 140.0 {
        "#000000"
    } else {
        "#ffffff"
    }
}

#[cfg(test)]
mod tests {
    //! Parity replay of `tests/fixtures/parity_color.json` (the oracle from
    //! plan Phase 1) plus unit tests for wrap/clamp/rounding edges.
    //!
    //! The fixture is read at test time with a std-only JSON reader — this
    //! crate has no dependencies. Hex/int outputs compare byte-for-byte;
    //! floats within 1e-9 absolute.

    use super::*;
    use std::path::PathBuf;

    const FLOAT_TOL: f64 = 1e-9;

    fn fixture_path() -> PathBuf {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        // Task brief says `../../…`; the crate sits two levels below the
        // repo root (`rust/huebox-core`), so `../../../…` is the true path.
        // Accept either; fail loudly listing both.
        for rel in [
            "../../../tests/fixtures/parity_color.json",
            "../../tests/fixtures/parity_color.json",
        ] {
            let p = manifest.join(rel);
            if p.is_file() {
                return p;
            }
        }
        panic!(
            "huebox-core: parity fixture missing (looked under {manifest:?}; \
             run Python `tests/test_parity.py` first)"
        )
    }

    // -- std-only JSON reader (test code only) ---------------------------

    #[derive(Debug)]
    enum J {
        Null,
        Bool(bool),
        Num(f64),
        Str(String),
        Arr(Vec<J>),
        Obj(Vec<(String, J)>),
    }

    impl J {
        fn get(&self, key: &str) -> &J {
            match self {
                J::Obj(pairs) => pairs
                    .iter()
                    .find(|(k, _)| k == key)
                    .map(|(_, v)| v)
                    .unwrap_or_else(|| panic!("fixture: missing key {key:?}")),
                _ => panic!("fixture: {key:?} not an object"),
            }
        }

        fn arr(&self) -> &Vec<J> {
            match self {
                J::Arr(items) => items,
                _ => panic!("fixture: not an array"),
            }
        }

        fn num(&self) -> f64 {
            match self {
                J::Num(n) => *n,
                _ => panic!("fixture: not a number"),
            }
        }

        fn text(&self) -> &str {
            match self {
                J::Str(s) => s,
                _ => panic!("fixture: not a string"),
            }
        }
    }

    struct Parser<'a> {
        b: &'a [u8],
        i: usize,
    }

    impl<'a> Parser<'a> {
        fn ws(&mut self) {
            while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
                self.i += 1;
            }
        }

        fn value(&mut self) -> J {
            self.ws();
            if self.i >= self.b.len() {
                panic!("fixture: unexpected end of input");
            }
            match self.b[self.i] {
                b'{' => self.object(),
                b'[' => self.array(),
                b'"' => J::Str(self.string()),
                b't' => self.literal("true", J::Bool(true)),
                b'f' => self.literal("false", J::Bool(false)),
                b'n' => self.literal("null", J::Null),
                _ => J::Num(self.number()),
            }
        }

        fn eat(&mut self, want: u8) {
            self.ws();
            assert_eq!(
                self.b.get(self.i),
                Some(&want),
                "fixture: expected byte {want:?}"
            );
            self.i += 1;
        }

        fn literal(&mut self, word: &str, v: J) -> J {
            assert!(
                self.b[self.i..].starts_with(word.as_bytes()),
                "fixture: bad literal"
            );
            self.i += word.len();
            v
        }

        fn object(&mut self) -> J {
            self.eat(b'{');
            let mut pairs = Vec::new();
            self.ws();
            if self.b.get(self.i) == Some(&b'}') {
                self.i += 1;
                return J::Obj(pairs);
            }
            loop {
                self.ws();
                let key = self.string();
                self.eat(b':');
                let v = self.value();
                pairs.push((key, v));
                self.ws();
                match self.b.get(self.i) {
                    Some(b',') => self.i += 1,
                    Some(b'}') => {
                        self.i += 1;
                        break;
                    }
                    other => panic!("fixture: bad object tail {other:?}"),
                }
            }
            J::Obj(pairs)
        }

        fn array(&mut self) -> J {
            self.eat(b'[');
            let mut items = Vec::new();
            self.ws();
            if self.b.get(self.i) == Some(&b']') {
                self.i += 1;
                return J::Arr(items);
            }
            loop {
                items.push(self.value());
                self.ws();
                match self.b.get(self.i) {
                    Some(b',') => self.i += 1,
                    Some(b']') => {
                        self.i += 1;
                        break;
                    }
                    other => panic!("fixture: bad array tail {other:?}"),
                }
            }
            J::Arr(items)
        }

        fn string(&mut self) -> String {
            assert_eq!(self.b.get(self.i), Some(&b'"'), "fixture: bad string");
            self.i += 1;
            let mut out = String::new();
            loop {
                match *self.b.get(self.i).expect("fixture: unterminated string") {
                    b'"' => {
                        self.i += 1;
                        return out;
                    }
                    b'\\' => {
                        self.i += 1;
                        match self.b[self.i] {
                            b'"' => out.push('"'),
                            b'\\' => out.push('\\'),
                            b'/' => out.push('/'),
                            b'b' => out.push('\u{0008}'),
                            b'f' => out.push('\u{000C}'),
                            b'n' => out.push('\n'),
                            b'r' => out.push('\r'),
                            b't' => out.push('\t'),
                            b'u' => {
                                let h = std::str::from_utf8(&self.b[self.i + 1..self.i + 5])
                                    .expect("fixture: bad \\u");
                                let cp = u32::from_str_radix(h, 16).expect("fixture: bad \\u");
                                out.push(char::from_u32(cp).expect("fixture: bad \\u scalar"));
                                self.i += 4;
                            }
                            e => panic!("fixture: bad escape {e:?}"),
                        }
                        self.i += 1;
                    }
                    _ => {
                        // Fixture strings are plain ASCII; multi-byte UTF-8
                        // would need char-boundary slicing — not needed here.
                        out.push(self.b[self.i] as char);
                        self.i += 1;
                    }
                }
            }
        }

        fn number(&mut self) -> f64 {
            let start = self.i;
            while self.i < self.b.len()
                && matches!(
                    self.b[self.i],
                    b'-' | b'+' | b'0'..=b'9' | b'.' | b'e' | b'E'
                )
            {
                self.i += 1;
            }
            std::str::from_utf8(&self.b[start..self.i])
                .expect("fixture: bad number text")
                .parse::<f64>()
                .expect("fixture: bad number")
        }
    }

    fn load_fixture() -> J {
        let text = std::fs::read_to_string(fixture_path()).expect("fixture unreadable");
        let mut p = Parser {
            b: text.as_bytes(),
            i: 0,
        };
        let v = p.value();
        p.ws();
        assert_eq!(p.i, p.b.len(), "fixture: trailing bytes");
        v
    }

    fn assert_close(got: f64, want: f64, what: &str) {
        assert!(
            (got - want).abs() <= FLOAT_TOL,
            "{what}: got {got:?} want {want:?} within {FLOAT_TOL}"
        );
    }

    fn triple(items: &[J]) -> (f64, f64, f64) {
        assert_eq!(items.len(), 3, "fixture: triple len");
        (items[0].num(), items[1].num(), items[2].num())
    }

    // -- parity replay ----------------------------------------------------

    #[test]
    fn parity_meta_and_slot_order() {
        let data = load_fixture();
        let meta = data.get("meta");
        assert_close(meta.get("hue_step").num(), HUE_STEP, "hue_step");
        assert_close(meta.get("channel_step").num(), CHANNEL_STEP, "channel_step");
        assert_eq!(meta.get("missing").text(), MISSING);
        let slots: Vec<&str> = meta.get("slots").arr().iter().map(J::text).collect();
        let names: Vec<&str> = NAMES.to_vec();
        assert_eq!(slots, names);
        let iterated: Vec<&str> = Slots::default().iter().map(|(name, _)| name).collect();
        assert_eq!(iterated, names);
    }

    #[test]
    fn parity_hex_to_rgb() {
        let data = load_fixture();
        for case in data.get("hex_to_rgb").arr() {
            let (r, g, b) = hex_to_rgb(case.get("input").text());
            let (er, eg, eb) = triple(case.get("expected").arr());
            assert_eq!((r, g, b), (er, eg, eb), "input {:?}", case.get("input"));
        }
    }

    #[test]
    fn parity_rgb_to_hex() {
        let data = load_fixture();
        for case in data.get("rgb_to_hex").arr() {
            let (r, g, b) = triple(case.get("input").arr());
            assert_eq!(rgb_to_hex(r, g, b), case.get("expected").text());
        }
    }

    #[test]
    fn parity_rgb_to_hsv() {
        let data = load_fixture();
        for case in data.get("rgb_to_hsv").arr() {
            let (r, g, b) = triple(case.get("input").arr());
            let (h, s, v) = rgb_to_hsv(r, g, b);
            let (eh, es, ev) = triple(case.get("expected").arr());
            assert_close(h, eh, "h");
            assert_close(s, es, "s");
            assert_close(v, ev, "v");
        }
    }

    #[test]
    fn parity_hsv_to_rgb() {
        let data = load_fixture();
        for case in data.get("hsv_to_rgb").arr() {
            let (h, s, v) = triple(case.get("input").arr());
            let (r, g, b) = hsv_to_rgb(h, s, v);
            let (er, eg, eb) = triple(case.get("expected").arr());
            assert_close(r, er, "r");
            assert_close(g, eg, "g");
            assert_close(b, eb, "b");
        }
    }

    #[test]
    fn parity_luminance() {
        let data = load_fixture();
        for case in data.get("luminance").arr() {
            let (r, g, b) = hex_to_rgb(case.get("input").text());
            assert_close(luminance(r, g, b), case.get("expected").num(), "luminance");
        }
    }

    #[test]
    fn parity_readable_fg() {
        let data = load_fixture();
        for case in data.get("readable_fg").arr() {
            assert_eq!(
                readable_fg(case.get("input").text()),
                case.get("expected").text()
            );
        }
    }

    #[test]
    fn parity_step_hsv() {
        let data = load_fixture();
        for case in data.get("step_hsv").arr() {
            let (r, g, b) = hex_to_rgb(case.get("value").text());
            let (h, s, v) = rgb_to_hsv(r, g, b);
            let axis = case
                .get("channel")
                .text()
                .chars()
                .next()
                .expect("fixture: empty channel");
            let dir = case.get("direction").num() as i32;
            let mult = case.get("mult").num();
            let (h2, s2, v2) = step_hsv(h, s, v, axis, dir, mult);
            let (r2, g2, b2) = hsv_to_rgb(h2, s2, v2);
            assert_eq!(rgb_to_hex(r2, g2, b2), case.get("expected").text());
        }
    }

    #[test]
    fn parity_fixture_shape() {
        // Sections outside this crate's API (`is_hex`, `normalize_hex`)
        // still parse intact — guards against fixture drift.
        let data = load_fixture();
        let is_hex = data.get("is_hex").arr();
        assert_eq!(is_hex.len(), 12);
        assert!(matches!(is_hex[0].get("expected"), J::Bool(true)));
        assert!(matches!(is_hex[6].get("expected"), J::Bool(false)));
        let norm = data.get("normalize_hex").arr();
        assert_eq!(norm.len(), 7);
        assert_eq!(norm[0].get("expected").text(), "#abcdef");
    }

    // -- model ------------------------------------------------------------

    #[test]
    fn slots_default_get_set_index_iter() {
        let mut slots = Slots::default();
        for (name, hex) in slots.iter() {
            assert_eq!(hex, MISSING, "default {name}");
        }
        assert_eq!(slots.iter().count(), 22);
        slots.set("background", "#101014");
        assert_eq!(slots.get("background"), "#101014");
        assert_eq!(&slots["background"], "#101014");
        // Defaults untouched elsewhere.
        assert_eq!(slots.get("foreground"), MISSING);
        // Full round-trip in canonical order.
        for (i, name) in NAMES.iter().enumerate() {
            slots.set(name, &format!("#{i:02x}{i:02x}{i:02x}"));
        }
        let names: Vec<&str> = slots.iter().map(|(n, _)| n).collect();
        assert_eq!(names, NAMES.to_vec());
        assert_eq!(&slots["palette-0"], "#000000");
        assert_eq!(&slots["selection-foreground"], "#151515");
        // Clone/eq behave by value.
        assert_eq!(slots.clone(), slots);
    }

    // -- edges ------------------------------------------------------------

    #[test]
    fn bankers_rounding_edges() {
        // Half-to-even, not half-away: 2.5 -> 2, 3.5 -> 4 (Rust .round()
        // would give 3 and 4 — the first is the trap).
        assert_eq!(rgb_to_hex(2.5, 2.5, 2.5), "#020202");
        assert_eq!(rgb_to_hex(3.5, 3.5, 3.5), "#040404");
        assert_eq!(rgb_to_hex(0.5, 0.5, 0.5), "#000000");
        assert_eq!(rgb_to_hex(1.5, 1.5, 1.5), "#020202");
        assert_eq!(rgb_to_hex(127.5, 127.5, 127.5), "#808080");
        assert_eq!(rgb_to_hex(254.5, 0.0, 0.0), "#fe0000");
        // Clamp before rounding.
        assert_eq!(rgb_to_hex(-1.0, 300.0, 127.5), "#00ff80");
        assert_eq!(rgb_to_hex(-100.0, 1000.0, 0.0), "#00ff00");
    }

    #[test]
    fn hue_wraps_sat_val_clamp() {
        // Hue below 0 wraps instead of clamping.
        let (h, s, v) = step_hsv(0.0, 1.0, 1.0, 'h', -1, 1.0);
        assert_close(h, 1.0 - HUE_STEP, "wrap down");
        assert_eq!((s, v), (1.0, 1.0));
        // Hue at the top wraps to the bottom.
        let (h, _, _) = step_hsv(1.0 - HUE_STEP / 2.0, 1.0, 1.0, 'h', 1, 1.0);
        assert_close(h, HUE_STEP / 2.0, "wrap up");
        // Sat/val clamp at both ends, even under x20.
        assert_eq!(step_hsv(0.5, 0.0, 0.5, 's', -1, 20.0).1, 0.0);
        assert_eq!(step_hsv(0.5, 1.0, 0.5, 's', 1, 20.0).1, 1.0);
        assert_eq!(step_hsv(0.5, 0.5, 0.0, 'v', -1, 20.0).2, 0.0);
        assert_eq!(step_hsv(0.5, 0.5, 1.0, 'v', 1, 20.0).2, 1.0);
        // One sat step is exactly CHANNEL_STEP.
        let (_, s, _) = step_hsv(0.5, 0.5, 0.5, 's', 1, 1.0);
        assert_close(s, 0.52, "channel step");
    }

    #[test]
    fn negative_hue_matches_python_modulo() {
        // #ff0004 has a negative pre-modulo hue; Python `% 1.0` lands at
        // 0.9973…, while a naive Rust `%` keeps the negative sign.
        let (h, s, v) = rgb_to_hsv(255.0, 0.0, 4.0);
        assert_close(h, 0.9973856209150327, "hue");
        assert_eq!((s, v), (1.0, 1.0));
        // Negative-hue branch of hsv_to_rgb: int(-1.2) is -1, and Python
        // -1 % 6 is 5 -> (v, p, q), unclamped past 255.
        let (r, g, b) = hsv_to_rgb(-0.2, 1.0, 1.0);
        assert_eq!((r, g), (255.0, 0.0));
        assert_close(b, 306.00000000000006, "unclamped blue");
    }

    #[test]
    fn constants_and_readable_boundary() {
        assert_eq!(HUE_STEP, 1.0 / 360.0);
        assert_eq!(CHANNEL_STEP, 0.02);
        // 140.0 itself reads white; only past 140 reads black.
        assert_eq!(readable_fg("#8c8c8c"), "#ffffff"); // lum exactly 140
        assert_eq!(readable_fg("#8d8d8d"), "#000000"); // lum 141
        assert_eq!(readable_fg("#000000"), "#ffffff");
        assert_eq!(readable_fg("#ffffff"), "#000000");
    }
}
