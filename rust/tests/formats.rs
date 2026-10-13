//! Round-trip tests through the registry facade only: every format must
//! read all its slots and write them back without touching a single
//! unrelated byte. Rust port of `tests/test_formats.py` — same fixtures,
//! same cases, plus the no-op gate (`apply_write`_None_) and the Ghostty
//! overrides-delete worker, which have no Python-side round-trip.
//!
//! These tests never reach into format internals: `read` / `write` /
//! `apply_write` / `ghostty_remove_color_lines` are the whole surface.

use std::collections::BTreeMap;

use huebox::core::Slots;
use huebox::format::{apply_write, ghostty_remove_color_lines, read, write, FORMATS, FORMAT_NAMES};

const GHOSTTY: &str = "# a comment that must survive\n\
                       font-family = \"Some Font\"\n\
                       \n\
                       palette = 0=#0a0a13\n\
                       palette = 1=#ff0067\n\
                       palette = 15=#f8f8ff\n\
                       \n\
                       background = #0f0f1a\n\
                       foreground = #ededfe\n\
                       cursor-color = #ededfe\n\
                       cursor-text = #0f0f1a\n\
                       selection-background = #202036\n\
                       selection-foreground = #ededff\n";

const KITTY: &str = "# kitty theme\n\
                     font_family      SauceCodePro Nerd Font\n\
                     \n\
                     background            #0f0f1a\n\
                     foreground            #ededfe\n\
                     cursor                #ededfe\n\
                     selection_background  #202036\n\
                     selection_foreground  #ededff\n\
                     color0  #0a0a13\n\
                     color1  #ff0067\n\
                     color15 #f8f8ff\n";

const ALACRITTY_TOML: &str = "# alacritty config\n\
                               [general]\n\
                               live_config_reload = true\n\
                               \n\
                               [colors.primary]\n\
                               background = \"#0f0f1a\"\n\
                               foreground = \"#ededfe\"\n\
                               \n\
                               [colors.cursor]\n\
                               text    = \"#0f0f1a\"\n\
                               cursor  = \"#ededfe\"\n\
                               \n\
                               [colors.selection]\n\
                               background = \"#202036\"\n\
                               foreground = \"#ededff\"\n\
                               \n\
                               [colors.normal]\n\
                               \"0\"  = \"#0a0a13\"\n\
                               \"1\"  = \"#ff0067\"\n\
                               \"15\" = \"#f8f8ff\"\n";

const ALACRITTY_DOTTED: &str =
    "[colors]\nprimary = { background = \"#0f0f1a\", foreground = \"#ededfe\" }\nnormal = { \"0\" = \"#0a0a13\" }\n";

fn expected_slots(format: &str) -> Vec<&'static str> {
    match format {
        "ghostty" => vec![
            "palette-0",
            "palette-1",
            "palette-15",
            "background",
            "foreground",
            "cursor-color",
            "cursor-text",
            "selection-background",
            "selection-foreground",
        ],
        "kitty" => vec![
            "palette-0",
            "palette-1",
            "palette-15",
            "background",
            "foreground",
            "cursor-color",
            "selection-background",
            "selection-foreground",
        ],
        "alacritty" => vec![
            "palette-0",
            "palette-1",
            "palette-15",
            "background",
            "foreground",
            "cursor-color",
            "cursor-text",
            "selection-background",
            "selection-foreground",
        ],
        _ => panic!("unknown fixture format {format:?}"),
    }
}

fn fixture(format: &str) -> &'static str {
    match format {
        "ghostty" => GHOSTTY,
        "kitty" => KITTY,
        "alacritty" => ALACRITTY_TOML,
        _ => panic!("unknown fixture format {format:?}"),
    }
}

/// One distinct hex per slot index: a wrong mapping cannot hide behind a
/// shared value.
fn distinct_hex(i: usize) -> String {
    format!(
        "#{:02x}{:02x}{:02x}",
        i * 11 % 256,
        i * 37 % 256,
        i * 53 % 256
    )
}

const ALL_NAMED: [(&str, &str); 6] = [
    ("background", "background"),
    ("foreground", "foreground"),
    ("cursor-color", "cursor-color"),
    ("cursor-text", "cursor-text"),
    ("selection-background", "selection-background"),
    ("selection-foreground", "selection-foreground"),
];

/// A config holding every slot the format carries, in canonical order.
fn full_text(format: &str) -> (String, Vec<(String, String)>) {
    let mut want: Vec<(String, String)> = (0..16)
        .map(|i| (format!("palette-{i}"), distinct_hex(i)))
        .collect();
    for (slot, _) in ALL_NAMED {
        if format == "kitty" && slot == "cursor-text" {
            continue; // kitty's `cursor` is cursor-color only
        }
        want.push((slot.to_owned(), distinct_hex(want.len())));
    }
    let mut text = String::from("# full-slot fixture\n");
    match format {
        "ghostty" => {
            for (slot, hex) in &want {
                if let Some(index) = slot.strip_prefix("palette-") {
                    text.push_str(&format!("palette = {index}={hex}\n"));
                } else {
                    text.push_str(&format!("{slot} = {hex}\n"));
                }
            }
        }
        "kitty" => {
            for (slot, hex) in &want {
                if let Some(index) = slot.strip_prefix("palette-") {
                    text.push_str(&format!("color{index} {hex}\n"));
                } else {
                    let key = match slot.as_str() {
                        "cursor-color" => "cursor",
                        "selection-background" => "selection_background",
                        "selection-foreground" => "selection_foreground",
                        key => key,
                    };
                    text.push_str(&format!("{key} {hex}\n"));
                }
            }
        }
        "alacritty" => {
            text.push_str("[colors.primary]\n");
            for slot in ["background", "foreground"] {
                let hex = &want.iter().find(|(s, _)| s == slot).unwrap().1;
                text.push_str(&format!("{slot} = \"{hex}\"\n"));
            }
            text.push_str("[colors.cursor]\n");
            for (key, slot) in [("text", "cursor-text"), ("cursor", "cursor-color")] {
                let hex = &want.iter().find(|(s, _)| s == slot).unwrap().1;
                text.push_str(&format!("{key} = \"{hex}\"\n"));
            }
            text.push_str("[colors.selection]\n");
            for slot in ["selection-background", "selection-foreground"] {
                let key = slot.strip_prefix("selection-").unwrap();
                let hex = &want.iter().find(|(s, _)| s == slot).unwrap().1;
                text.push_str(&format!("{key} = \"{hex}\"\n"));
            }
            text.push_str("[colors.normal]\n");
            for i in 0..16 {
                let hex = &want[i].1;
                text.push_str(&format!("\"{i}\" = \"{hex}\"\n"));
            }
        }
        _ => panic!("unknown fixture format {format:?}"),
    }
    (text, want)
}

/// `Slots` holding exactly the pairs `read` found (blank themes start
/// grey; tests set what the config carried).
fn slots_of(pairs: &BTreeMap<String, String>) -> Slots {
    let mut slots = Slots::default();
    for (name, hex) in pairs {
        slots.set(name, hex);
    }
    slots
}

#[test]
fn registry_shape() {
    assert_eq!(FORMAT_NAMES, ["ghostty", "kitty", "alacritty"]);
    assert_eq!(FORMATS.len(), 3);
    for (def, name) in FORMATS.iter().zip(FORMAT_NAMES) {
        assert_eq!(def.name, name);
    }
    // Static detection data ports with the registry (no I/O here — the
    // detection pass in huebox-lib reads these).
    let kitty = FORMATS.iter().find(|def| def.name == "kitty").unwrap();
    assert_eq!(
        kitty.env,
        [
            ("KITTY_CONFIG_DIRECTORY", "kitty.conf"),
            ("KITTY_CONFIG_DIR", "kitty.conf"),
        ]
    );
    assert!(FORMATS
        .iter()
        .find(|def| def.name == "ghostty")
        .unwrap()
        .env
        .is_empty());
    assert!(FORMATS
        .iter()
        .find(|def| def.name == "alacritty")
        .unwrap()
        .env
        .is_empty());
    assert_eq!(
        FORMATS
            .iter()
            .find(|def| def.name == "ghostty")
            .unwrap()
            .defaults,
        [
            "~/.config/ghostty/config.ghostty",
            "~/.config/ghostty/config",
            "~/Library/Application Support/com.mitchellh.ghostty/config.ghostty",
            "~/Library/Application Support/com.mitchellh.ghostty/config"
        ]
    );
    assert_eq!(
        FORMATS
            .iter()
            .find(|def| def.name == "kitty")
            .unwrap()
            .defaults,
        [
            "~/.config/kitty/kitty.conf",
            "$XDG_CONFIG_DIRS/kitty/kitty.conf",
            "~/.kitty.conf",
        ]
    );
    assert_eq!(
        FORMATS
            .iter()
            .find(|def| def.name == "alacritty")
            .unwrap()
            .defaults,
        [
            "~/.config/alacritty/alacritty.toml",
            "~/.config/alacritty.toml",
            "~/.config/alacritty/alacritty.yml",
            "~/.alacritty.toml",
            "/etc/alacritty/alacritty.toml",
        ]
    );
}

#[test]
fn reads_every_expected_slot() {
    for format in FORMAT_NAMES {
        let slots = read(format, fixture(format));
        let expected = expected_slots(format);
        let missing: Vec<&&str> = expected
            .iter()
            .filter(|name| !slots.contains_key(**name))
            .collect();
        assert!(missing.is_empty(), "{format} did not read {missing:?}");
    }
}

#[test]
fn reads_every_slot_the_format_carries() {
    // §10: reading every slot, per format — all 16 palette slots plus
    // every named slot the dialect maps (kitty has no cursor-text).
    for format in FORMAT_NAMES {
        let (text, want) = full_text(format);
        let found = read(format, &text);
        assert_eq!(found.len(), want.len(), "{format} slot count");
        for (slot, hex) in &want {
            assert_eq!(found.get(slot), Some(hex), "{format} {slot}");
        }
        // …and the full read writes back byte-identical.
        let out = write(format, &text, &slots_of(&found));
        assert_eq!(out, text, "{format} full no-op changed the bytes");
        assert_eq!(apply_write(&text, &out), None, "{format} full no-op");
        // …and a full round-trip survives a second read.
        let mut moved = slots_of(&found);
        moved.set(&want[0].0, "#010203");
        let out = write(format, &text, &moved);
        assert_eq!(
            read(format, &out).get(&want[0].0),
            Some(&"#010203".to_string()),
            "{format} round-trip"
        );
    }
}

#[test]
fn values_are_correct() {
    for format in FORMAT_NAMES {
        let slots = read(format, fixture(format));
        assert_eq!(
            slots.get("background"),
            Some(&"#0f0f1a".to_string()),
            "{format}"
        );
        assert_eq!(
            slots.get("palette-0"),
            Some(&"#0a0a13".to_string()),
            "{format}"
        );
        assert_eq!(
            slots.get("palette-15"),
            Some(&"#f8f8ff".to_string()),
            "{format}"
        );
    }
}

#[test]
fn write_is_a_no_op_when_nothing_changed() {
    // §6.2 rule 4 through the pure gate: `write` comes back byte-identical
    // and `apply_write` reports None, so the caller never opens the file
    // (the Python case pins this via mtime; here the None *is* the pin).
    for format in FORMAT_NAMES {
        let text = fixture(format);
        let slots = slots_of(&read(format, text));
        let out = write(format, text, &slots);
        assert_eq!(out, text, "{format} no-op write changed the bytes");
        assert_eq!(apply_write(text, &out), None, "{format} no-op was not None");
    }
}

#[test]
fn write_changes_only_colour_lines() {
    for format in FORMAT_NAMES {
        let text = fixture(format);
        let mut slots = slots_of(&read(format, text));
        slots.set("background", "#010203");
        slots.set("palette-0", "#040506");
        let out = write(format, text, &slots);
        assert!(
            apply_write(text, &out).is_some(),
            "{format} change was a no-op"
        );
        assert!(out.contains("#010203"), "{format} lost background");
        assert!(out.contains("#040506"), "{format} lost palette-0");
        let before: Vec<&str> = text.split('\n').collect();
        let after: Vec<&str> = out.split('\n').collect();
        assert_eq!(before.len(), after.len(), "{format} changed line count");
        let changed: Vec<usize> = before
            .iter()
            .zip(after.iter())
            .enumerate()
            .filter(|(_, (a, b))| a != b)
            .map(|(i, _)| i)
            .collect();
        assert_eq!(
            changed.len(),
            2,
            "{format} touched unexpected lines: {changed:?}"
        );
    }
}

#[test]
fn comments_survive_edits() {
    let markers = [
        ("ghostty", "# a comment that must survive"),
        ("kitty", "# kitty theme"),
        ("alacritty", "# alacritty config"),
    ];
    for (format, marker) in markers {
        let text = fixture(format);
        let mut slots = slots_of(&read(format, text));
        slots.set("foreground", "#ffffff");
        let out = write(format, text, &slots);
        assert!(out.contains(marker), "{format} lost its comment");
    }
}

#[test]
fn write_normalises_case_and_missing_hash_in_place() {
    // `#ABCDEF` / `FF0067` read lowercased and hashed; a write splices the
    // canonical token while the key, spacing and case around it survive.
    let text = "palette = 0=ABCDEF\nbackground = FF0067\n";
    let found = read("ghostty", text);
    assert_eq!(found.get("palette-0"), Some(&"#abcdef".to_string()));
    assert_eq!(found.get("background"), Some(&"#ff0067".to_string()));
    let mut slots = slots_of(&found);
    slots.set("palette-0", "#010203");
    // `slots_of` carries the read-back background too, so the write
    // normalises it as well — same as passing a full theme to `write_flat`.
    assert_eq!(
        write("ghostty", text, &slots),
        "palette = 0=#010203\nbackground = #ff0067\n"
    );
}

#[test]
fn seven_digit_hex_is_not_a_colour() {
    // `HEX` ends at `\b`: a seventh hex digit voids the token entirely —
    // the line reads as nothing and writes back untouched.
    assert!(!read("ghostty", "background = #abc1234\n").contains_key("background"));
    assert!(!read("kitty", "color0 #abc1234\n").contains_key("palette-0"));
    let slots = Slots::default();
    assert_eq!(
        apply_write(
            "background = #abc1234\n",
            &write("ghostty", "background = #abc1234\n", &slots)
        ),
        None
    );
}

#[test]
fn kitty_names_and_case_fold() {
    let found = read("kitty", "Color15=#FFFFFF\ncursor cafe12\ncursorx #0a0a13\n");
    assert_eq!(found.get("palette-15"), Some(&"#ffffff".to_string()));
    assert_eq!(found.get("cursor-color"), Some(&"#cafe12".to_string()));
    assert!(!found.contains_key("cursorx"));
}

#[test]
fn keyword_case_survives_verbatim_in_slot_names() {
    // Non-lowercase keywords match (rules are IGNORECASE) but keep the
    // file's spelling as the slot — so they never hit a theme's lowercase
    // slots on write. Probed against CPython: `Background` reads as
    // `Background`, and only the exact-lowercase kitty spellings rename.
    let ghostty = read("ghostty", "PALETTE = 0 = #ABCDEF\nBackground=#0f0f1a\n");
    assert_eq!(ghostty.get("palette-0"), Some(&"#abcdef".to_string()));
    assert_eq!(ghostty.get("Background"), Some(&"#0f0f1a".to_string()));
    assert!(!ghostty.contains_key("background"));
    let kitty = read(
        "kitty",
        "COLOR0=#0a0a13\nBackground #0F0F1A\nCursor #ededfe\n",
    );
    assert_eq!(kitty.get("palette-0"), Some(&"#0a0a13".to_string()));
    assert_eq!(kitty.get("Background"), Some(&"#0f0f1a".to_string()));
    assert_eq!(kitty.get("Cursor"), Some(&"#ededfe".to_string()));
    assert!(!kitty.contains_key("cursor-color"));
    // …and a full theme write leaves those lines untouched.
    let text = "Background = #0f0f1a\nbackground = #0f0f1a\n";
    let mut slots = Slots::default();
    slots.set("background", "#010203");
    assert_eq!(
        write("ghostty", text, &slots),
        "Background = #0f0f1a\nbackground = #010203\n"
    );
}

#[test]
fn alacritty_single_quotes_write_back_double() {
    // A single-quoted token is still quoted on write — with `"`, as
    // `'"%s"' % slots[slot]` formats. Probed against CPython.
    let text = "[colors.primary]\nbackground = '#0F0F1A'\n";
    assert_eq!(
        read("alacritty", text).get("background"),
        Some(&"#0f0f1a".to_string())
    );
    let mut slots = Slots::default();
    slots.set("background", "#010203");
    assert_eq!(
        write("alacritty", text, &slots),
        "[colors.primary]\nbackground = \"#010203\"\n"
    );
}

#[test]
fn alacritty_dotted_style() {
    let slots = read("alacritty", ALACRITTY_DOTTED);
    assert_eq!(slots.get("background"), Some(&"#0f0f1a".to_string()));
    assert_eq!(slots.get("palette-0"), Some(&"#0a0a13".to_string()));
}

#[test]
fn alacritty_named_palette() {
    let text = "[colors.normal]\nblack = \"#0a0a13\"\nwhite = \"#f8f8ff\"\n";
    let slots = read("alacritty", text);
    assert_eq!(slots.get("palette-0"), Some(&"#0a0a13".to_string()));
    assert_eq!(slots.get("palette-7"), Some(&"#f8f8ff".to_string()));
    let mut full = slots_of(&slots);
    full.set("palette-0", "#010203");
    let out = write("alacritty", text, &full);
    assert_eq!(
        read("alacritty", &out).get("palette-0"),
        Some(&"#010203".to_string())
    );
}

#[test]
fn alacritty_bare_hex_reads_six_digits() {
    // The bare `#hex` value has no trailing boundary: `#abc1234` reads
    // `#abc123`, exactly as the regex does.
    let slots = read("alacritty", "[colors.primary]\nbackground = #abc1234\n");
    assert_eq!(slots.get("background"), Some(&"#abc123".to_string()));
}

#[test]
fn alacritty_hashless_tokens_need_a_hash_on_the_line() {
    // The entries fast-path skips `#`-free lines: a quoted `#`-less hex
    // without any `#` on its line stays invisible.
    let slots = read("alacritty", "background = \"0f0f1a\"\n");
    assert!(!slots.contains_key("background"));
}

#[test]
fn alacritty_changed_write_normalises_crlf() {
    // Changed writes join with `\n` plus one trailing newline, as
    // `write_toml` writes; unchanged input comes back verbatim.
    let text = "[colors.primary]\r\nbackground = \"#0f0f1a\"\r\n";
    let mut slots = slots_of(&read("alacritty", text));
    slots.set("background", "#010203");
    assert_eq!(
        write("alacritty", text, &slots),
        "[colors.primary]\nbackground = \"#010203\"\n"
    );
    let same = slots_of(&read("alacritty", text));
    let out = write("alacritty", text, &same);
    assert_eq!(out, text);
    assert_eq!(apply_write(text, &out), None);
}

#[test]
fn alacritty_inline_table_writes_each_token() {
    // Two same-line entries with same-length tokens both land; quotes and
    // spacing survive.
    let text = "[colors]\nprimary = { background = \"#0f0f1a\", foreground = \"#ededfe\" }\n";
    let mut slots = slots_of(&read("alacritty", text));
    slots.set("background", "#010203");
    slots.set("foreground", "#040506");
    assert_eq!(
        write("alacritty", text, &slots),
        "[colors]\nprimary = { background = \"#010203\", foreground = \"#040506\" }\n"
    );
}

#[test]
fn alacritty_stale_span_quirk_matches_python() {
    // Inherited quirk, pinned for parity: `write_toml` parses entries from
    // the original text and splices with the original spans, so a
    // same-line replacement that changes the token's length (a quoted
    // `#`-less hex grows by two when normalised) shifts its neighbour's
    // span. Probes against CPython 3.x pin these exact bytes.
    let text = "[colors]\nprimary = { background = \"0f0f1a\", foreground = \"#ff0067\" }\n";
    let found = read("alacritty", text);
    assert_eq!(found.get("background"), Some(&"#0f0f1a".to_string()));
    let mut slots = slots_of(&found);
    slots.set("background", "#010203");
    slots.set("foreground", "#040506");
    assert_eq!(
        write("alacritty", text, &slots),
        "[colors]\nprimary = { background = \"#010203\", foreground =\"#040506\"\" }\n"
    );
}

#[test]
fn ghostty_remove_color_lines_deletes_whole_lines() {
    // The overrides-delete worker: every colour line goes, everything else
    // — spacing, comments, non-colour keys, the `theme =` pointer — stays
    // byte-identical.
    let text =
        "# keep me\nbackground = #0f0f1a\ntheme = eyes\npalette = 0=#0a0a13\nfont-family = \"x\"\n";
    let (kept, removed) = ghostty_remove_color_lines(text);
    assert_eq!(removed, 2);
    assert_eq!(kept, "# keep me\ntheme = eyes\nfont-family = \"x\"\n");
    // Nothing to remove: text back verbatim, count zero — the caller keeps
    // the mtime by not writing.
    let (kept, removed) = ghostty_remove_color_lines("# keep me\ntheme = eyes\n");
    assert_eq!((kept.as_str(), removed), ("# keep me\ntheme = eyes\n", 0));
}

#[test]
fn changed_write_appends_exactly_one_trailing_newline() {
    // A changed write ends the text with one `\n`, as `"\n".join + "\n"`
    // does — even when the input had none.
    let mut slots = Slots::default();
    slots.set("background", "#010203");
    assert_eq!(
        write("ghostty", "background = #0f0f1a", &slots),
        "background = #010203\n"
    );
}
