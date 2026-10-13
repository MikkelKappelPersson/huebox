//! Alacritty's TOML dialect: dotted keys, tables, inline tables (001 spec §6).
//!
//! Reads normalise every path form to one canonical slot; writes go back
//! to the form already present in the file, replacing each colour's value
//! token while keeping quotes and spacing intact.

use std::collections::BTreeMap;

use crate::core::Slots;

use crate::format::base::{format_is_hex, format_normalize_hex, splice_line, split_lines};

/// Dotted TOML path → canonical slot, as `ALACRITTY_PATHS` holds: the six
/// named paths, `colors.normal.0`–`15`, and the same eight under their
/// everyday names (`black` … `white`, hand-written configs say `black`,
/// not `0`).
fn lookup_path(path: &str) -> Option<&'static str> {
    Some(match path {
        "colors.primary.background" => "background",
        "colors.primary.foreground" => "foreground",
        "colors.cursor.text" => "cursor-text",
        "colors.cursor.cursor" => "cursor-color",
        "colors.selection.background" => "selection-background",
        "colors.selection.foreground" => "selection-foreground",
        "colors.normal.0" | "colors.normal.black" => "palette-0",
        "colors.normal.1" | "colors.normal.red" => "palette-1",
        "colors.normal.2" | "colors.normal.green" => "palette-2",
        "colors.normal.3" | "colors.normal.yellow" => "palette-3",
        "colors.normal.4" | "colors.normal.blue" => "palette-4",
        "colors.normal.5" | "colors.normal.magenta" => "palette-5",
        "colors.normal.6" | "colors.normal.cyan" => "palette-6",
        "colors.normal.7" | "colors.normal.white" => "palette-7",
        "colors.normal.8" => "palette-8",
        "colors.normal.9" => "palette-9",
        "colors.normal.10" => "palette-10",
        "colors.normal.11" => "palette-11",
        "colors.normal.12" => "palette-12",
        "colors.normal.13" => "palette-13",
        "colors.normal.14" => "palette-14",
        "colors.normal.15" => "palette-15",
        _ => return None,
    })
}

/// One colour found in the text: its line, its slot, its value token's
/// byte span within that line, and whether the token was quoted.
struct Entry {
    line: usize,
    slot: String,
    start: usize,
    end: usize,
    quoted: bool,
}

/// `_toml_key` verbatim: strip whitespace, then strip `"` and `'` from
/// both ends (all of them — `"'a'"` keys yield `a`).
fn toml_key(text: &str) -> &str {
    text.trim().trim_matches(|c| c == '"' || c == '\'')
}

/// Bytes a `_TOML_PAIR` key may hold: `[A-Za-z0-9_.\-]`, quoted or bare.
fn is_key_byte(b: u8) -> bool {
    b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-')
}

/// `^\s*\[([^\]]+)\]`: a section header opens a new table. The closing
/// `]` is required; the inner text runs to the first one.
fn match_header(line: &str) -> Option<String> {
    let bytes = line.as_bytes();
    let mut pos = 0;
    while pos < bytes.len() && bytes[pos].is_ascii_whitespace() {
        pos += 1;
    }
    if bytes.get(pos) != Some(&b'[') {
        return None;
    }
    pos += 1;
    let start = pos;
    while pos < bytes.len() && bytes[pos] != b']' {
        pos += 1;
    }
    if start == pos || bytes.get(pos) != Some(&b']') {
        return None;
    }
    Some(toml_key(&line[start..pos]).to_string())
}

/// `^\s*prefix\s*=\s*\{`: an inline table's key, for `prefix.key` paths.
fn match_inline_prefix(line: &str) -> Option<String> {
    let bytes = line.as_bytes();
    let mut pos = 0;
    while pos < bytes.len() && bytes[pos].is_ascii_whitespace() {
        pos += 1;
    }
    let start = pos;
    while pos < bytes.len() && is_key_byte(bytes[pos]) {
        pos += 1;
    }
    if start == pos {
        return None;
    }
    let prefix = &line[start..pos];
    while pos < bytes.len() && bytes[pos].is_ascii_whitespace() {
        pos += 1;
    }
    if bytes.get(pos) != Some(&b'=') {
        return None;
    }
    pos += 1;
    while pos < bytes.len() && bytes[pos].is_ascii_whitespace() {
        pos += 1;
    }
    if bytes.get(pos) != Some(&b'{') {
        return None;
    }
    Some(toml_key(prefix).to_string())
}

/// One `_TOML_PAIR` (`key = "…" | '…' | #hex`) at `pos`: the raw key text,
/// the value token's span, and the offset past it. A bare `#hex` value
/// takes exactly six digits with no trailing boundary — `#abc1234` reads
/// `#abc123`, as the regex does.
fn match_pair_at(line: &str, pos: usize) -> Option<(String, usize, usize, usize)> {
    let bytes = line.as_bytes();
    let (key, mut next) = match bytes.get(pos) {
        Some(b'"') => {
            let start = pos + 1;
            let mut end = start;
            while end < bytes.len() && is_key_byte(bytes[end]) {
                end += 1;
            }
            if start == end || bytes.get(end) != Some(&b'"') {
                return None;
            }
            (line[pos..=end].to_string(), end + 1)
        }
        _ => {
            let start = pos;
            let mut end = start;
            while end < bytes.len() && is_key_byte(bytes[end]) {
                end += 1;
            }
            if start == end {
                return None;
            }
            (line[start..end].to_string(), end)
        }
    };
    while next < bytes.len() && bytes[next].is_ascii_whitespace() {
        next += 1;
    }
    if bytes.get(next) != Some(&b'=') {
        return None;
    }
    next += 1;
    while next < bytes.len() && bytes[next].is_ascii_whitespace() {
        next += 1;
    }
    let (vstart, vend) = match bytes.get(next) {
        Some(b'"') => {
            let mut end = next + 1;
            while end < bytes.len() && bytes[end] != b'"' {
                end += 1;
            }
            if end >= bytes.len() {
                return None;
            }
            (next, end + 1)
        }
        Some(b'\'') => {
            let mut end = next + 1;
            while end < bytes.len() && bytes[end] != b'\'' {
                end += 1;
            }
            if end >= bytes.len() {
                return None;
            }
            (next, end + 1)
        }
        Some(b'#') => {
            let start = next + 1;
            let mut end = start;
            while end < bytes.len() && bytes[end].is_ascii_hexdigit() && end - start < 6 {
                end += 1;
            }
            if end - start != 6 {
                return None;
            }
            (next, end)
        }
        _ => return None,
    };
    Some((key, vstart, vend, vend))
}

/// Every `_TOML_PAIR` in `line`, left to right, as `finditer` yields them:
/// a failed start advances one character, a match continues past its end.
fn find_pairs(line: &str) -> Vec<(String, usize, usize)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while pos < line.len() {
        match match_pair_at(line, pos) {
            Some((key, vstart, vend, end)) => {
                out.push((key, vstart, vend));
                pos = end;
            }
            None => {
                pos += line[pos..].chars().next().map_or(1, |c| c.len_utf8());
            }
        }
    }
    out
}

/// Strip `\x1b\[[0-9;?]*[A-Za-z]` escape sequences, as the entries
/// fast-path does before asking whether a whole line is itself a hex.
fn strip_ansi(line: &str) -> String {
    let bytes = line.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut pos = 0;
    while pos < bytes.len() {
        if bytes[pos] == 0x1b && bytes.get(pos + 1) == Some(&b'[') {
            let mut end = pos + 2;
            while end < bytes.len()
                && (bytes[end].is_ascii_digit() || matches!(bytes[end], b';' | b'?'))
            {
                end += 1;
            }
            if end < bytes.len() && bytes[end].is_ascii_alphabetic() {
                pos = end + 1;
                continue;
            }
        }
        // Byte 0x1b is a char boundary; any other byte is copied whole
        // with its full character below.
        let ch = line[pos..].chars().next().unwrap_or('\u{FFFD}');
        out.push(ch);
        pos += ch.len_utf8();
    }
    out
}

/// `(index, slot, value span)` for every colour in the file, covering the
/// three spellings configs actually use: dotted keys, `[section]` tables,
/// and `{ inline = tables }`.
///
/// Lines with no `#` that are not themselves a hex are skipped unread, as
/// the fast-path guard does — so a quoted `#`-less hex (`"0f0f1a"`) on an
/// otherwise `#`-free line stays invisible, byte-for-byte like Python.
fn toml_entries(lines: &[&str]) -> Vec<Entry> {
    let mut out = Vec::new();
    let mut section = String::new();
    for (index, line) in lines.iter().enumerate() {
        if let Some(header) = match_header(line) {
            section = header;
            continue;
        }
        if !line.contains('#') && !format_is_hex(&strip_ansi(line)) {
            continue;
        }
        let prefix = match_inline_prefix(line);
        for (raw_key, vstart, vend) in find_pairs(line) {
            let raw = &line[vstart..vend];
            let token = raw.trim_matches(|c| c == '"' || c == '\'');
            if !format_is_hex(token) {
                continue;
            }
            let key = toml_key(&raw_key);
            let mut slot = lookup_path(key);
            if slot.is_none() {
                if let Some(prefix) = &prefix {
                    let dotted = if section.is_empty() {
                        format!("{prefix}.{key}")
                    } else {
                        format!("{section}.{prefix}.{key}")
                    };
                    slot = lookup_path(&dotted);
                } else if !section.is_empty() {
                    slot = lookup_path(&format!("{section}.{key}"));
                }
            }
            if let Some(slot) = slot {
                let quoted = matches!(raw.as_bytes().first(), Some(b'"') | Some(b'\''));
                out.push(Entry {
                    line: index,
                    slot: slot.to_string(),
                    start: vstart,
                    end: vend,
                    quoted,
                });
            }
        }
    }
    out
}

/// Every colour `text` holds, later spellings winning on repeats.
pub(crate) fn read_text(text: &str) -> BTreeMap<String, String> {
    let lines = split_lines(text);
    let mut slots = BTreeMap::new();
    for entry in toml_entries(&lines) {
        let raw = &lines[entry.line][entry.start..entry.end];
        let token = raw.trim_matches(|c| c == '"' || c == '\'');
        slots.insert(entry.slot, format_normalize_hex(token));
    }
    slots
}

/// The `(table, key)` an inserted slot is spelled as: the canonical
/// spelling the round-trip fixtures use — file keywords under
/// `[colors.primary]` / `[colors.cursor]` / `[colors.selection]`, quoted
/// numbers under `[colors.normal]` — or `None` for a name no table
/// carries (no canonical slot reaches there; the filter keeps it total).
fn insert_slot(slot: &str) -> Option<(&'static str, String)> {
    Some(match slot {
        "background" => ("colors.primary", "background".to_owned()),
        "foreground" => ("colors.primary", "foreground".to_owned()),
        "cursor-text" => ("colors.cursor", "text".to_owned()),
        "cursor-color" => ("colors.cursor", "cursor".to_owned()),
        "selection-background" => ("colors.selection", "background".to_owned()),
        "selection-foreground" => ("colors.selection", "foreground".to_owned()),
        _ => (
            "colors.normal",
            format!("\"{}\"", slot.strip_prefix("palette-")?),
        ),
    })
}

/// Append one line per missing slot under its `[colors.*]` table.
///
/// `missing` arrives in canonical order; rows join the end of the
/// section their table already opens, or a table created at end of file
/// when no header names it — tables created in first-appearance order,
/// each behind one blank line unless the file already ends on one.
/// Values are quoted, like every value the writer keeps.
pub(crate) fn insert_text(text: &str, missing: &[(&str, &str)]) -> String {
    if missing.is_empty() {
        return text.to_owned();
    }
    let mut tables: Vec<(&str, Vec<(String, &str)>)> = Vec::new();
    for (slot, hex) in missing {
        let Some((table, key)) = insert_slot(slot) else {
            continue;
        };
        match tables.iter_mut().find(|(name, _)| *name == table) {
            Some((_, rows)) => rows.push((key, hex)),
            None => tables.push((table, vec![(key, hex)])),
        }
    }
    if tables.is_empty() {
        return text.to_owned();
    }
    let mut lines: Vec<String> = if text.is_empty() {
        Vec::new()
    } else {
        split_lines(text)
            .iter()
            .map(|line| line.to_string())
            .collect()
    };
    for (table, rows) in &tables {
        let rows: Vec<String> = rows
            .iter()
            .map(|(key, hex)| format!("{key} = \"{hex}\""))
            .collect();
        match lines
            .iter()
            .rposition(|line| match_header(line).as_deref() == Some(*table))
        {
            Some(head) => {
                let end = lines
                    .iter()
                    .skip(head + 1)
                    .position(|line| match_header(line).is_some())
                    .map(|offset| head + 1 + offset)
                    .unwrap_or(lines.len());
                lines.splice(end..end, rows);
            }
            None => {
                if !lines.is_empty() && !lines.last().is_some_and(|line| line.is_empty()) {
                    lines.push(String::new());
                }
                lines.push(format!("[{table}]"));
                lines.extend(rows);
            }
        }
    }
    let mut out = lines.join("\n");
    out.push('\n');
    out
}

/// Replace each colour's value token, keeping quotes and spacing intact —
/// or return the input untouched when nothing changed (§6.2 rule 4).
///
/// Entries come from the original text and apply in file order with their
/// original spans, as `write_toml` does: a same-line replacement that
/// changes the token's length shifts its neighbours' spans (a quoted
/// `#`-less hex grows by two when normalised). The splice clamps instead
/// of panicking, and on ASCII text the bytes come out exactly as Python's
/// do — including that quirk.
pub(crate) fn write_text(text: &str, slots: &Slots) -> String {
    let table: BTreeMap<&str, &str> = slots.iter().collect();
    let before = split_lines(text);
    let mut lines: Vec<String> = before.iter().map(|line| line.to_string()).collect();
    for entry in toml_entries(&before) {
        if let Some(hex) = table.get(entry.slot.as_str()) {
            // `'"%s"' % slots[slot]` when the token was quoted, else bare.
            let replacement = if entry.quoted {
                format!("\"{hex}\"")
            } else {
                (*hex).to_string()
            };
            let line = &mut lines[entry.line];
            *line = splice_line(line, entry.start, entry.end, &replacement);
        }
    }
    if lines.iter().map(String::as_str).eq(before.iter().copied()) {
        return text.to_owned();
    }
    let mut out = lines.join("\n");
    out.push('\n');
    out
}
