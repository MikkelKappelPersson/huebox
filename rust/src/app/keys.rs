//! One translation table: crossterm's structured keys → editor key names.
//!
//! Rust port of `app.py`'s `KEYS` plus the fall-through (`KEYS.get(key,
//! key)`). The state machine keeps its own names (`esc`, `\x03`, arrows,
//! `\r`, …); this function is the only place the two vocabularies meet.
//!
//! Rules, matching the Python shell:
//!
//! * `esc`, arrows, `Enter` (`\r`), backspace and space keep their editor
//!   spellings (`space`, not `" "`: the setup naming field names it).
//!   Home/End/page keys answer themselves too: the import list jumps
//!   first/last on them, everywhere else they are inert.
//! * Only the Ctrl-held `c`/`s`/`a`/`d` map to control codes (`\x03`,
//!   `\x13`, `\x01`, `\x04`) — plain `c`/`s`/`a`/`d` are colour keys.
//!   Any other Ctrl-held key answers `""`.
//! * Anything with Alt/Super/Hyper/Meta held answers `""`.
//! * Plain characters answer themselves (typable `?` still answers `"?"`);
//!   anything else (function keys, Tab, media keys, non-ASCII)
//!   answers `""` — a no-op in `apply_key` that the setup-name field also
//!   swallows (`""` is not one char), never a panic. `"?"` stays the
//!   typable key's own name, never the unknown sentinel.
//!
//! Key *kinds* are the caller's business: Press and Repeat apply, Release
//! is ignored (research §2) — see [`crate::app::shell::run`].

use ratatui::crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

/// Every ASCII printable as its own editor key name, in `c - 0x20` order.
///
/// A static table, not a leak: single characters intern without
/// allocating (research §3 binds). Non-ASCII answers `""` — theme names
/// are ASCII-only and hex entry is ASCII, so nothing bound is lost.
const ASCII_NAMES: &str = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~";

/// The static name for an ASCII printable, or `None` outside `0x20..0x7E`.
fn ascii_name(c: char) -> Option<&'static str> {
    let i = (c as u32).checked_sub(0x20)? as usize;
    if i >= ASCII_NAMES.len() {
        return None;
    }
    Some(&ASCII_NAMES[i..i + 1])
}

/// Crossterm's key → the name `apply_key` expects.
///
/// Total: every `KeyEvent` answers something, and `""` is always safe.
pub fn translate(key: &KeyEvent) -> &'static str {
    use KeyCode::{
        Backspace, Char, Delete, Down, End, Enter, Esc, Home, Insert, Left, PageDown, PageUp,
        Right, Up,
    };
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        if let Char(c) = key.code {
            match c.to_ascii_lowercase() {
                'c' => return "\x03",
                's' => return "\x13",
                'a' => return "\x01",
                'd' => return "\x04",
                _ => return "",
            }
        }
        return "";
    }
    if key.modifiers.intersects(
        KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::HYPER | KeyModifiers::META,
    ) {
        return "";
    }
    match key.code {
        Esc => "esc",
        Enter => "\r",
        Left => "left",
        Right => "right",
        Up => "up",
        Down => "down",
        Backspace => "backspace",
        // Named, as Textual named it: the setup field matches `space`.
        Char(' ') => "space",
        Char(c) => ascii_name(c).unwrap_or(""),
        // Deliberately unnamed: Tab, Insert/Delete, function and media
        // keys have no branch anywhere. Home/End/page keys are named:
        // the import list jumps first/last on them, like the native
        // list it mirrors; everywhere else they are inert.
        Home => "home",
        End => "end",
        PageUp => "pageup",
        PageDown => "pagedown",
        Delete | Insert => "",
        _ => "",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn mod_key(code: KeyCode, mods: KeyModifiers) -> KeyEvent {
        KeyEvent::new(code, mods)
    }

    #[test]
    fn ascii_table_covers_every_printable() {
        assert_eq!(ASCII_NAMES.len(), 95);
        for v in 0x20u32..=0x7Eu32 {
            let c = char::from_u32(v).expect("printable");
            assert_eq!(
                ascii_name(c),
                Some(&ASCII_NAMES[(v - 0x20) as usize..(v - 0x20) as usize + 1])
            );
        }
        assert_eq!(ascii_name('\x1f'), None);
        assert_eq!(ascii_name('\x7f'), None);
        assert_eq!(ascii_name('é'), None);
    }

    #[test]
    fn special_keys_keep_editor_spellings() {
        let cases = [
            (KeyCode::Esc, "esc"),
            (KeyCode::Enter, "\r"),
            (KeyCode::Left, "left"),
            (KeyCode::Right, "right"),
            (KeyCode::Up, "up"),
            (KeyCode::Down, "down"),
            (KeyCode::Backspace, "backspace"),
            (KeyCode::Char(' '), "space"),
            (KeyCode::Home, "home"),
            (KeyCode::End, "end"),
            (KeyCode::PageUp, "pageup"),
            (KeyCode::PageDown, "pagedown"),
        ];
        for (code, want) in cases {
            assert_eq!(translate(&key(code)), want, "{code:?}");
        }
    }

    #[test]
    fn plain_chars_answer_themselves() {
        for c in [
            'w', 'e', 's', 'd', 'x', 'c', 'f', 'h', 'u', 'r', 't', 'N', 'i', 'Q', 'q', 'n', 'y',
            'E', 'D', 'C', 'I', 'a', '5', '/', '.', '-',
        ] {
            assert_eq!(
                translate(&key(KeyCode::Char(c))),
                c.to_string().as_str(),
                "{c}"
            );
        }
    }

    #[test]
    fn ctrl_held_csad_only() {
        let cases = [('c', "\x03"), ('s', "\x13"), ('a', "\x01"), ('d', "\x04")];
        for (c, want) in cases {
            assert_eq!(
                translate(&mod_key(KeyCode::Char(c), KeyModifiers::CONTROL)),
                want,
                "ctrl+{c}"
            );
            // Shift rides along on a real terminal (uppercase reports);
            // the control code still wins.
            assert_eq!(
                translate(&mod_key(
                    KeyCode::Char(c.to_ascii_uppercase()),
                    KeyModifiers::CONTROL | KeyModifiers::SHIFT
                )),
                want,
                "ctrl+shift+{c}"
            );
        }
        // Plain colour keys are untouched by this branch.
        assert_eq!(translate(&key(KeyCode::Char('c'))), "c");
        assert_eq!(translate(&key(KeyCode::Char('s'))), "s");
    }

    #[test]
    fn other_ctrl_is_a_noop() {
        for c in ['w', 'e', 'x', 'f', 'h', 'u', 'r', 't', 'n', 'z', 'q'] {
            assert_eq!(
                translate(&mod_key(KeyCode::Char(c), KeyModifiers::CONTROL)),
                "",
                "ctrl+{c}"
            );
        }
        assert_eq!(
            translate(&mod_key(KeyCode::Enter, KeyModifiers::CONTROL)),
            ""
        );
        assert_eq!(translate(&mod_key(KeyCode::Esc, KeyModifiers::CONTROL)), "");
        assert_eq!(translate(&mod_key(KeyCode::Up, KeyModifiers::CONTROL)), "");
    }

    #[test]
    fn modifiers_outside_shift_are_noops() {
        assert_eq!(
            translate(&mod_key(KeyCode::Char('i'), KeyModifiers::ALT)),
            ""
        );
        assert_eq!(translate(&mod_key(KeyCode::Enter, KeyModifiers::ALT)), "");
        assert_eq!(translate(&mod_key(KeyCode::Esc, KeyModifiers::ALT)), "");
        assert_eq!(
            translate(&mod_key(KeyCode::Char('x'), KeyModifiers::SUPER)),
            ""
        );
        // Shift alone rides through on characters (uppercase keys exist).
        assert_eq!(
            translate(&mod_key(KeyCode::Char('N'), KeyModifiers::SHIFT)),
            "N"
        );
    }

    #[test]
    fn unknown_keys_never_panic() {
        let codes = [
            KeyCode::Tab,
            KeyCode::BackTab,
            KeyCode::Delete,
            KeyCode::Insert,
            KeyCode::F(1),
            KeyCode::F(12),
            KeyCode::Null,
            KeyCode::CapsLock,
            KeyCode::Char('é'),
            KeyCode::Char('\u{7f}'),
            KeyCode::Media(ratatui::crossterm::event::MediaKeyCode::Play),
        ];
        for code in codes {
            assert_eq!(translate(&key(code)), "", "{code:?}");
        }
        // The typable `?` keeps its own name: the sentinel is `""`, so an
        // unknown key never types into the setup-name field while `?`
        // still does.
        assert_eq!(translate(&key(KeyCode::Char('?'))), "?");
    }
}
