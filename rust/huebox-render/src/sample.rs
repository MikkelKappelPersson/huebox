//! The live code sample: a hand tokenizer, not a grammar (§8).
//!
//! Rust port of `sample_lines` in `huebox/render.py`. The sample text is
//! FIXED (nine lines), so instead of a highlighting grammar this is a
//! purpose-built splitter whose output is table-tested against Pygments'
//! captured stream (see `tests/fixtures/sample_runs.json`, dumped by
//! `tests/dump_grid.py`). If the sample text ever changes, that table test
//! fails and forces the splitter update — same guard, no grammar
//! dependency (research §6).
//!
//! Token classes mirror the rows of [`TOKEN_SLOTS`]; only the classes the
//! fixed lines spend exist. Mapping is resolved once per call into
//! `(slot, text)` runs while every hex is read out of [`Slots`] per call,
//! which is what keeps the sample live (§14.1).

use huebox_core::Slots;

use crate::{Row, painted, slot_hex};

/// The fixed sample text: nine lines spending the whole zig vocabulary.
pub const SAMPLE_LINES: [(&str, &str); 9] = [
    ("// live preview - edits show here without reloading", ""),
    ("const huebox = \"terminal theme editor\";", "zig"),
    ("const std = @import(\"std\");", "zig"),
    ("", ""),
    ("pub fn main() !void {", "zig"),
    ("    var count: u32 = 42;   // your palette", "zig"),
    ("    if (count > 0xFF) {", "zig"),
    ("        std.debug.print(\"{d} colours\\n\", .{count});", "zig"),
    ("    }", "zig"),
];

/// A name in call position — the one distinction the token stream does
/// not make (see `call_position`).
pub const CALL_SLOT: &str = "palette-12";

/// First match wins: `(class, slot)` in probe order. A class the fixed
/// lines never emit has no row and lands on `foreground`.
pub const TOKEN_SLOTS: [(&str, &str); 9] = [
    ("Comment", "palette-8"),
    ("Keyword.Type", "palette-6"),
    ("Keyword", "palette-5"),
    ("Name.Builtin", "palette-14"),
    ("String.Escape", "palette-11"),
    ("String", "palette-2"),
    ("Number", "palette-3"),
    ("Operator", "palette-5"),
    ("Punctuation", "foreground"),
];

/// Token classes the fixed lines spend.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Tok {
    Comment,
    Keyword,
    KeywordType,
    NameBuiltin,
    Str,
    StrEscape,
    Number,
    Operator,
    Punctuation,
    Name,
    Text,
}

impl Tok {
    fn class(self) -> &'static str {
        match self {
            Tok::Comment => "Comment",
            Tok::Keyword => "Keyword",
            Tok::KeywordType => "Keyword.Type",
            Tok::NameBuiltin => "Name.Builtin",
            Tok::Str => "String",
            Tok::StrEscape => "String.Escape",
            Tok::Number => "Number",
            Tok::Operator => "Operator",
            Tok::Punctuation => "Punctuation",
            Tok::Name | Tok::Text => "Text",
        }
    }
}

/// Keywords the fixed lines spend (plain, reserved and type alike — the
/// table only needs the split between Keyword and Keyword.Type).
const KEYWORDS: [&str; 5] = ["const", "pub", "fn", "var", "if"];
/// Types the fixed lines spend.
const TYPES: [&str; 2] = ["u32", "void"];

/// Chars the zig lexer calls operators (the fixed lines spend `= ! : >`).
fn is_operator(ch: char) -> bool {
    matches!(ch, '=' | '!' | '<' | '>' | ':' | '&' | '|' | '+' | '-' | '*' | '/' | '%' | '^' | '~' | '?')
}

/// Split the fixed sample text into `(class, text)` tokens.
///
/// Rules, in order: `//` opens a comment to end of line; `"` opens a
/// string whose `\` + next char is an escape run of its own; `@word` is a
/// builtin; digits (and `0x…`) are numbers; words are keywords, types, or
/// names; operator chars are operators; the rest is punctuation or blank
/// text. Whitespace between tokens is its own text run, exactly as
/// Pygments emits it.
fn tokenize(text: &str) -> Vec<(Tok, String)> {
    let mut out = Vec::new();
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let ch = chars[i];
        if ch == '/' && chars.get(i + 1) == Some(&'/') {
            let mut j = i;
            while j < chars.len() && chars[j] != '\n' {
                j += 1;
            }
            if j < chars.len() {
                j += 1; // the comment owns its newline, like Pygments'
            }
            out.push((Tok::Comment, chars[i..j].iter().collect()));
            i = j;
        } else if ch == '"' {
            out.push((Tok::Str, "\"".to_owned()));
            i += 1;
            let mut buf = String::new();
            while i < chars.len() && chars[i] != '"' {
                if chars[i] == '\\' && i + 1 < chars.len() {
                    if !buf.is_empty() {
                        out.push((Tok::Str, std::mem::take(&mut buf)));
                    }
                    out.push((Tok::StrEscape, chars[i..i + 2].iter().collect()));
                    i += 2;
                } else {
                    buf.push(chars[i]);
                    i += 1;
                }
            }
            if !buf.is_empty() {
                out.push((Tok::Str, buf));
            }
            if i < chars.len() {
                out.push((Tok::Str, "\"".to_owned()));
                i += 1;
            }
        } else if ch == '@' {
            let mut j = i + 1;
            while j < chars.len() && (chars[j].is_alphanumeric() || chars[j] == '_') {
                j += 1;
            }
            out.push((Tok::NameBuiltin, chars[i..j].iter().collect()));
            i = j;
        } else if ch.is_ascii_digit() {
            let mut j = i;
            if chars[j] == '0' && chars.get(j + 1) == Some(&'x') {
                j += 2;
                while j < chars.len() && chars[j].is_ascii_hexdigit() {
                    j += 1;
                }
            } else {
                while j < chars.len() && chars[j].is_ascii_digit() {
                    j += 1;
                }
            }
            out.push((Tok::Number, chars[i..j].iter().collect()));
            i = j;
        } else if ch.is_alphabetic() || ch == '_' {
            let mut j = i;
            while j < chars.len() && (chars[j].is_alphanumeric() || chars[j] == '_') {
                j += 1;
            }
            let word: String = chars[i..j].iter().collect();
            let tok = if KEYWORDS.contains(&word.as_str()) {
                Tok::Keyword
            } else if TYPES.contains(&word.as_str()) {
                Tok::KeywordType
            } else {
                Tok::Name
            };
            out.push((tok, word));
            i = j;
        } else if is_operator(ch) {
            out.push((Tok::Operator, ch.to_string()));
            i += 1;
        } else if ch.is_whitespace() {
            let mut j = i;
            while j < chars.len() && chars[j].is_whitespace() && chars[j] != '"' {
                // whitespace runs never cross a string boundary; the `\n`
                // stays with the run Pygments attaches it to
                if chars[j] == '\n' {
                    j += 1;
                    break;
                }
                j += 1;
            }
            out.push((Tok::Text, chars[i..j].iter().collect()));
            i = j;
        } else {
            out.push((Tok::Punctuation, ch.to_string()));
            i += 1;
        }
    }
    out
}

/// True when the name at `i` is called rather than declared.
///
/// The fixed lines' lexer emits a bare name for a declaration, a field, a
/// module path and a call alike: a name is in call position when `(` follows
/// it — the space between them does not count — and no `fn` precedes it, so
/// `print(...)` is a call while `fn main()` is a definition. A builtin
/// never is: it wears the builtin slot either way.
fn call_position(tokens: &[(Tok, String)], i: usize) -> bool {
    if tokens[i].0 != Tok::Name {
        return false;
    }
    let mut before = i;
    while before > 0 && tokens[before - 1].1.trim().is_empty() {
        before -= 1;
    }
    let mut after = i + 1;
    while after < tokens.len() && tokens[after].1.trim().is_empty() {
        after += 1;
    }
    after < tokens.len()
        && tokens[after].1 == "("
        && !(before > 0 && tokens[before - 1].1 == "fn")
}

/// Map one token class to the palette slot that colours it.
///
/// `called` marks an identifier the sample calls. A class the fixed lines
/// never emit has no row and lands on `foreground`.
fn slot_for(class: &str, called: bool) -> &'static str {
    if called {
        return CALL_SLOT;
    }
    // subtree test, child rows above parents: `Keyword.Type` before
    // `Keyword`, exactly as the Python probe walk does
    for (name, slot) in TOKEN_SLOTS {
        if class == name
            || (name == "Keyword" && class.starts_with("Keyword."))
            || (name == "String" && class.starts_with("String."))
            || (name == "Name" && class.starts_with("Name."))
        {
            return slot;
        }
    }
    "foreground"
}

/// The sample as `(slot, text)` runs — lexed and mapped per call.
///
/// Neither the tokens nor the mapping change between frames; only the hex
/// behind a slot does. Resolving both here leaves [`sample_lines`]
/// reading `slots` on every call, which is what keeps the sample live
/// (§14.1) without re-walking the mapping table per token per frame —
/// and with no cache anywhere, since the text is nine fixed lines.
pub fn sample_runs() -> Vec<(&'static str, String)> {
    let joined: String = SAMPLE_LINES
        .iter()
        .map(|(text, _)| *text)
        .collect::<Vec<_>>()
        .join("\n");
    // Pygments lexers ensure a trailing newline: the captured stream ends
    // with a `Text` newline run of its own, and `sample_lines` below (like
    // the Python one) turns it into the trailing blank row the frame pops.
    let tokens = tokenize(&(joined + "\n"));
    tokens
        .iter()
        .enumerate()
        .filter(|(_, (_, text))| !text.is_empty())
        .map(|(i, (tok, text))| {
            let called = *tok == Tok::Name && call_position(&tokens, i);
            let class = if called { "Name.Call" } else { tok.class() };
            (slot_for(class, called), text.clone())
        })
        .collect()
}

/// Fallback ink for a slot the buffer has not got, when the
/// foreground itself is missing too (mirrors the hardcoded default in
/// `render.sample_lines`).
pub const SAMPLE_FALLBACK: &str = "#ededfe";

/// The ink for one sample run: the stored hex, or the buffer's
/// foreground when the slot is missing — pale lavender when the
/// foreground is missing too.
///
/// Faithful to `slots.get(slot, slots.get("foreground", "#ededfe"))`
/// except in one degenerate case: a `Slots` cannot tell an explicitly
/// stored `#808080` from a missing slot (both read `MISSING`), so a
/// stored exact-grey renders as the fallback here while the Python
/// keeps it. Complete buffers — the only thing the editor ever holds —
/// never consult the fallback at all.
fn run_ink<'a>(slots: &'a Slots, slot: &str, default: &'a str) -> &'a str {
    let stored = slot_hex(slots, slot);
    if stored == crate::MISSING {
        default
    } else {
        stored
    }
}

/// The set of slots the sample spends (for the §8 chrome test).
pub fn slots_spent() -> Vec<&'static str> {
    let mut seen = Vec::new();
    for (slot, _) in sample_runs() {
        if !seen.contains(&slot) {
            seen.push(slot);
        }
    }
    seen
}

/// Paint the sample from the live slot values: one lookup per run.
///
/// Each row re-opens its own ink: a run spanning a newline must not leave
/// the next row relying on the previous row's state, since rows stand
/// alone — which is how `std.debug.` once survived the modal dim undimmed.
pub fn sample_lines(slots: &Slots) -> Vec<Row> {
    let fg = slot_hex(slots, "foreground");
    let default = if fg == crate::MISSING {
        SAMPLE_FALLBACK
    } else {
        fg
    };
    let mut lines: Vec<Row> = vec![Vec::new()];
    for (slot, text) in sample_runs() {
        for (index, chunk) in text.split('\n').enumerate() {
            if index > 0 {
                lines.push(Vec::new());
            }
            if !chunk.is_empty() {
                let ink = run_ink(slots, slot, default);
                lines.last_mut().unwrap().extend(painted(chunk, ink, ""));
            }
        }
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    fn full_slots() -> Slots {
        let mut s = Slots::default();
        for (i, hex) in [
            "#1a1a20", "#c05c5c", "#5cc05c", "#c0a05c", "#5c8cc0", "#c05c8c",
            "#5cc0c0", "#80808c", "#d0d0d8", "#e06c6c", "#6cc06c", "#e0c06c",
            "#6c9ce0", "#e06c9c", "#6cc0c0", "#f0f0f4",
        ]
        .iter()
        .enumerate()
        {
            s.set(&format!("palette-{i}"), hex);
        }
        for (slot, hex) in [
            ("background", "#101014"),
            ("foreground", "#e6e6ea"),
            ("cursor-color", "#e6e6ea"),
            ("cursor-text", "#101014"),
            ("selection-background", "#2a2a34"),
            ("selection-foreground", "#f0f0f8"),
        ] {
            s.set(slot, hex);
        }
        s
    }

    #[test]
    fn splitter_spends_the_whole_lexer_vocabulary() {
        let runs = sample_runs();
        let spent: std::collections::HashSet<&str> =
            runs.iter().map(|(slot, _)| *slot).collect();
        let base: std::collections::HashSet<&str> =
            ["palette-2", "palette-3", "palette-5", "palette-6"]
                .into_iter()
                .collect();
        let bright: std::collections::HashSet<&str> =
            ["palette-8", "palette-11", "palette-12", "palette-14"]
                .into_iter()
                .collect();
        assert!(base.iter().all(|s| spent.contains(s)));
        assert!(bright.iter().all(|s| spent.contains(s)));
        assert!(spent.contains("foreground"));
        // no table row is dead: every TOKEN_SLOTS slot is spent
        for (_, slot) in TOKEN_SLOTS {
            assert!(spent.contains(slot), "{slot}");
        }
    }

    #[test]
    fn splitter_maps_the_fixed_vocabulary() {
        let runs = sample_runs();
        let slot_of = |want: &str| {
            runs.iter().find(|(_, t)| t == want).map(|(s, _)| *s)
        };
        assert_eq!(slot_of("u32"), Some("palette-6"));
        assert_eq!(slot_of("void"), Some("palette-6"));
        assert_eq!(slot_of("@import"), Some("palette-14"));
        assert_eq!(slot_of("\\n"), Some("palette-11"));
        assert_eq!(slot_of("{d} colours"), Some("palette-2"));
        assert_eq!(slot_of("print"), Some(CALL_SLOT));
        for name in ["main", "huebox", "std", "count"] {
            assert_eq!(slot_of(name), Some("foreground"), "{name}");
        }
    }

    #[test]
    fn sample_fits_eighty_columns_and_is_ascii() {
        let s = full_slots();
        for row in sample_lines(&s) {
            let text: String = row.iter().map(|c| c.ch).collect();
            assert!(text.len() + 4 <= 80, "{text:?}");
            assert!(text.is_ascii(), "{text:?}");
        }
    }

    #[test]
    fn sample_is_live_per_run() {
        // changing the bright blue moves `print` and nothing else
        let base = full_slots();
        let mut moved = base.clone();
        moved.set(CALL_SLOT, "#ff00ff");
        let before = sample_lines(&base);
        let after = sample_lines(&moved);
        assert_eq!(before.len(), after.len());
        let mut prints = 0;
        for (old, new) in before.iter().zip(after.iter()) {
            let ot: String = old.iter().map(|c| c.ch).collect();
            let nt: String = new.iter().map(|c| c.ch).collect();
            assert_eq!(ot, nt);
            for (o, n) in old.iter().zip(new.iter()) {
                assert_eq!(o.ch, n.ch);
                if ot.contains("print") && o.ch == 'p' {
                    prints += (n.fg == "#ff00ff") as usize;
                }
                if o.fg != n.fg {
                    assert_eq!(n.fg, "#ff00ff");
                }
            }
        }
        assert_eq!(prints, 1);
    }

    #[test]
    fn every_row_reopens_its_own_ink() {
        // no row relies on a previous row's state: every non-blank char
        // of every row carries its slot's ink
        let s = full_slots();
        for row in sample_lines(&s) {
            for cell in &row {
                if !cell.ch.is_whitespace() {
                    assert!(!cell.fg.is_empty(), "{cell:?}");
                }
            }
        }
    }
}
