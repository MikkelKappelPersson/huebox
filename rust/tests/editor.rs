//! Ported `tests/test_editor.py`: behaviour without a terminal.
//!
//! Frame-painting suites (Floor, Readout, TopBlock, NormalFrame,
//! SidePanels) belong to `huebox-render` / the compositor and are not
//! repeated here — this file pins the state machine: `apply_key` on its
//! own, the picker, the first-run setup choice, the overrides warning,
//! staged saves, the status bar, dialog rows and hits.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::rc::Rc;

use huebox::core::Slots;
use huebox::editor::{
    apply_key, click_overrides, click_setup, click_slot, click_theme, enter_setup, head_label,
    needs_setup, overrides_hits, overrides_lines, report_session, session_path, setup_hits,
    setup_lines, slot_at, theme_hits, theme_lines, top_subject, EditorState, Library, WriteOutcome,
    APPLY_KEY, DIRTY_MARK,
};
use huebox::render::frame::{frame, FrameOpts};
use huebox::render::grid::grid_geometry;
use huebox::render::layout::row_visible;
use huebox::render::palette::SLOTS;

const SAVE: &str = "\x13";

fn full_slots() -> Slots {
    let mut slots = Slots::default();
    for name in SLOTS {
        slots.set(name, "#3f7a3f");
    }
    slots
}

fn slots_map(slots: &Slots) -> BTreeMap<String, String> {
    slots
        .iter()
        .map(|(n, h)| (n.to_owned(), h.to_owned()))
        .collect()
}

fn row_text(row: &[huebox::render::Cell]) -> String {
    row.iter().map(|c| c.ch).collect()
}

fn plain_rows(frame: &huebox::render::frame::FrameOut) -> Vec<String> {
    frame.rows.iter().map(|row| row_text(row)).collect()
}

type Writes = Rc<RefCell<Vec<Slots>>>;

/// One picker-session write: `(theme, path, slots)`.
type SubjectWrites = Rc<RefCell<Vec<(Option<String>, String, Slots)>>>;

fn recording_state(slots: Slots, writes: &Writes) -> EditorState {
    let w = writes.clone();
    EditorState::new(
        slots,
        Box::new(move |_, _, slots| {
            w.borrow_mut().push(slots.clone());
            Ok(WriteOutcome::ok())
        }),
    )
}

fn background_state(writes: &Writes) -> EditorState {
    let mut st = recording_state(full_slots(), writes);
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    st
}

fn dirty_and_adjust(st: &mut EditorState) -> String {
    let name = SLOTS[st.sel].to_owned();
    let start = st.slots.get(&name).to_owned();
    apply_key("w", st);
    assert_ne!(
        st.slots.get(name.as_str()),
        start.as_str(),
        "adjust moves the slot"
    );
    start
}

// ---------------------------------------------------------------------------
// apply_key on its own
// ---------------------------------------------------------------------------

#[test]
fn adjust_marks_dirty_and_writes_nothing() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    assert!(st.dirty());
    assert!(!st.written);
    assert!(writes.borrow().is_empty());
}

#[test]
fn save_writes_once_and_cleans_the_buffer() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert_eq!(writes.borrow()[0], st.slots);
    assert!(!st.dirty());
    assert!(st.written);
    assert_eq!(st.status, "saved");
    assert_eq!(st.saved, st.slots);
}

#[test]
fn every_buffer_edit_is_written_only_on_save() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    for key in ["e", "e", "d", "c"] {
        apply_key(key, &mut st);
    }
    assert!(writes.borrow().is_empty());
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert_eq!(
        writes.borrow()[0].get("background"),
        st.slots.get("background")
    );
}

#[test]
fn dirty_esc_arms_and_armed_esc_quits() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key("esc", &mut st);
    assert!(st.armed);
    assert!(!st.quit);
    assert!(st.status.contains("discard"));
    apply_key("esc", &mut st);
    assert!(st.quit);
    assert!(writes.borrow().is_empty()); // discarded, not saved
}

#[test]
fn another_key_disarms() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key("esc", &mut st);
    apply_key("f", &mut st);
    assert!(!st.armed);
    assert_eq!(st.status, "step size x5");
    apply_key("esc", &mut st); // still dirty: it arms again
    assert!(st.armed);
    assert!(!st.quit);
}

#[test]
fn a_clean_esc_quits_at_once() {
    for closer in ["esc", "escape"] {
        let writes: Writes = Rc::new(RefCell::new(Vec::new()));
        let mut st = recording_state(full_slots(), &writes);
        apply_key(closer, &mut st);
        assert!(st.quit, "closer {closer}");
        assert!(!st.armed, "closer {closer}");
        assert_eq!(st.status, "");
    }
}

#[test]
fn ctrl_c_takes_the_esc_path() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key("\x03", &mut st);
    assert!(st.armed);
    assert!(!st.quit);
    apply_key("\x03", &mut st);
    assert!(st.quit);

    let mut clean = recording_state(full_slots(), &writes);
    apply_key("\x03", &mut clean);
    assert!(clean.quit);
}

#[test]
fn revert_goes_to_the_last_save_not_session_start() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    let start = st.slots.clone();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    let checkpoint = st.slots.clone();
    assert_ne!(checkpoint, start);
    dirty_and_adjust(&mut st);
    apply_key("r", &mut st);
    assert_eq!(st.slots, checkpoint);
    assert_eq!(st.saved, checkpoint);
    assert!(!st.dirty());
    assert!(st.undo.is_empty());
    assert_eq!(st.status, "reverted to last save");
    assert_eq!(writes.borrow().len(), 1);
}

#[test]
fn revert_before_any_save_returns_to_the_start() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key("r", &mut st);
    assert_eq!(st.slots, full_slots());
    assert_eq!(st.status, "reverted to start");
    assert!(writes.borrow().is_empty());
}

#[test]
fn undo_survives_a_save() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    let start = dirty_and_adjust(&mut st);
    let middle = st.slots.get("background").to_owned();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st); // saving does not clear the log
    assert_eq!(st.undo.len(), 2);
    apply_key("u", &mut st);
    assert_eq!(st.slots.get("background"), middle);
    apply_key("u", &mut st);
    assert_eq!(st.slots.get("background"), start);
    assert!(st.undo.is_empty());
    assert_eq!(writes.borrow().len(), 1);
}

#[test]
fn hex_entry_goes_through_the_prompt_callback() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let asked: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let a = asked.clone();
    let mut st = background_state(&writes);
    st.prompt_hex = Some(Box::new(move |label| {
        a.borrow_mut().push(label.to_owned());
        Some("#AbCdEf".to_owned())
    }));
    dirty_and_adjust(&mut st);
    apply_key("h", &mut st);
    assert_eq!(
        *asked.borrow(),
        vec!["  new hex for background: ".to_owned()]
    );
    assert_eq!(st.slots.get("background"), "#abcdef");
    assert_eq!(st.undo.len(), 2); // the nudge plus the typed hex
    assert_eq!(st.status, "background = #abcdef");
    apply_key("u", &mut st);
    assert_eq!(st.status, "undid background");
    assert!(writes.borrow().is_empty());
    apply_key("X", &mut st);
    assert_eq!(asked.borrow().last().unwrap(), "  new hex for background: ");
}

#[test]
fn hex_entry_rejects_junk_and_cancellation() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    st.prompt_hex = Some(Box::new(|_| Some("not a colour".to_owned())));
    dirty_and_adjust(&mut st);
    let before = st.slots.clone();
    apply_key("h", &mut st);
    assert_eq!(st.slots, before);
    assert!(st.status.contains("valid"));

    let mut cancelled = recording_state(full_slots(), &writes);
    cancelled.prompt_hex = Some(Box::new(|_| None));
    apply_key("h", &mut cancelled);
    assert_eq!(cancelled.status, "");
    assert!(!cancelled.dirty());
}

#[test]
fn without_a_prompt_callback_hex_entry_is_a_no_op() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    let before = st.slots.clone();
    apply_key("h", &mut st);
    assert_eq!(st.slots, before);
}

#[test]
fn i_and_vim_keys_are_inert_in_apply_key() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    st.prompt_hex = Some(Box::new(|_| Some("#ffffff".to_owned())));
    dirty_and_adjust(&mut st);
    let before = st.slots.clone();
    for key in ["i", "j", "k", "l"] {
        apply_key(key, &mut st);
    }
    assert_eq!(st.slots, before);
    assert!(writes.borrow().is_empty());
    assert!(!st.quit);
}

#[test]
fn movement_and_step_size_stay_clean() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.sel = 0;
    st.grid = grid_geometry(80, "  ");
    apply_key("down", &mut st);
    assert_eq!(SLOTS[st.sel], "palette-8");
    apply_key("up", &mut st);
    assert_eq!(st.sel, 0);
    apply_key("right", &mut st);
    assert_eq!(st.sel, 1);
    apply_key("left", &mut st);
    apply_key("f", &mut st);
    assert_eq!(st.mult, 5);
    assert!(!st.dirty());
    assert!(writes.borrow().is_empty());
}

#[test]
fn the_keys_follow_the_terminal_the_frame_is_drawn_for() {
    // 40 columns puts four swatches to a row, so one arrow down from
    // palette-5 is palette-9 — not the palette-13 a fixed eight-slot
    // stride would pick.
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.grid = grid_geometry(40, "  ");
    apply_key("down", &mut st);
    apply_key("s", &mut st);
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert_ne!(writes.borrow()[0].get("palette-9"), "#3f7a3f");
    assert_eq!(writes.borrow()[0].get("palette-13"), "#3f7a3f");
}

#[test]
fn an_absent_slot_is_not_adjustable() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = EditorState::from_pairs(
        &[("palette-0", "#123456")],
        Box::new({
            let w = writes.clone();
            move |_, _, slots| {
                w.borrow_mut().push(slots.clone());
                Ok(WriteOutcome::ok())
            }
        }),
    );
    st.grid = grid_geometry(80, "  ");
    apply_key("right", &mut st); // palette-1: no value in this file
    apply_key("w", &mut st);
    apply_key("h", &mut st);
    assert_eq!(
        slots_map(&st.slots).get("palette-0"),
        Some(&"#123456".to_owned())
    );
    assert!(!st.dirty());
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert!(!st.dirty());
    apply_key("esc", &mut st);
    assert!(st.quit); // Esc still quits
}

#[test]
fn failed_save_leaves_saved_untouched() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = EditorState::new(
        full_slots(),
        Box::new(|_, _, _| Err("disk full".to_owned())),
    );
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    assert!(st.dirty()); // buffer stays dirty, Esc keeps guarding
    assert!(!st.written);
    assert_eq!(st.status, "write failed: disk full");
    assert!(writes.borrow().is_empty());
}

// ---------------------------------------------------------------------------
// preview apply (Ctrl+A)
// ---------------------------------------------------------------------------

fn theme_state(writes: &Writes, pushes: &Rc<RefCell<Vec<Slots>>>) -> EditorState {
    let p = pushes.clone();
    let mut st = recording_state(full_slots(), writes);
    st.theme = Some("ember".to_owned());
    st.path = "/tmp/huebox/ember.toml".to_owned();
    st.apply = Some(Box::new(move |_, _, slots| {
        p.borrow_mut().push(slots.clone());
        Ok(WriteOutcome::status("applied"))
    }));
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    st
}

#[test]
fn apply_pushes_dirty_buffer_without_saving() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let pushes: Rc<RefCell<Vec<Slots>>> = Rc::new(RefCell::new(Vec::new()));
    let mut st = theme_state(&writes, &pushes);
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert_eq!(pushes.borrow().len(), 1);
    assert_eq!(pushes.borrow()[0], st.slots);
    assert!(writes.borrow().is_empty());
    assert!(st.dirty()); // truth untouched: Esc still guards
    assert!(!st.written);
    assert_eq!(st.status, "applied");
}

#[test]
fn apply_accepts_the_compositor_key_name() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let pushes: Rc<RefCell<Vec<Slots>>> = Rc::new(RefCell::new(Vec::new()));
    let mut st = theme_state(&writes, &pushes);
    dirty_and_adjust(&mut st);
    apply_key("ctrl+a", &mut st);
    assert_eq!(pushes.borrow().len(), 1);
    assert!(writes.borrow().is_empty());
    assert!(st.dirty());
}

#[test]
fn apply_without_seam_reports_nothing() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.theme = Some("ember".to_owned());
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert!(st.dirty());
    assert_eq!(st.status, "nothing to apply");
}

#[test]
fn apply_failure_keeps_dirty() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.theme = Some("ember".to_owned());
    st.apply = Some(Box::new(|_, _, _| Err("no terminal to push to".to_owned())));
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert!(st.dirty());
    assert!(st.status.contains("apply failed"));
}

#[test]
fn apply_in_direct_mode_saves() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert_eq!(writes.borrow().len(), 1); // the config is the truth there
    assert!(!st.dirty());
}

#[test]
fn save_after_apply_cleans_the_buffer() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let pushes: Rc<RefCell<Vec<Slots>>> = Rc::new(RefCell::new(Vec::new()));
    let mut st = theme_state(&writes, &pushes);
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert!(st.dirty());
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert!(!st.dirty());
    assert!(st.written);
}

// ---------------------------------------------------------------------------
// Ghostty overrides warning
// ---------------------------------------------------------------------------

fn overrides_state() -> (EditorState, Rc<RefCell<Vec<String>>>) {
    let deleted: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let d = deleted.clone();
    let mut st = EditorState::new(
        full_slots(),
        Box::new(|_, _, _| {
            Ok(WriteOutcome {
                status: "saved ember → ghostty".to_owned(),
                overrides: Some(huebox::editor::OverridesReport {
                    main: "/tmp/main".to_owned(),
                    keys: vec!["background".to_owned()],
                }),
            })
        }),
    );
    st.theme = Some("ember".to_owned());
    st.path = "/tmp/huebox/ember.toml".to_owned();
    st.delete_overrides = Some(Box::new(move |main| {
        d.borrow_mut().push(main.to_owned());
        Ok(format!(
            "removed 1 override(s) from {main} - theme now shows"
        ))
    }));
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    (st, deleted)
}

#[test]
fn a_save_with_overrides_arms_the_warning() {
    let (mut st, _) = overrides_state();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    assert!(!st.dirty());
    assert!(st.written);
    let over = st.overrides.as_ref().expect("warning armed");
    assert_eq!(over.main, "/tmp/main");
    assert_eq!(over.keys, vec!["background".to_owned()]);
    assert_eq!(st.overrides_index, 1); // the safe default: Enter keeps
}

#[test]
fn delete_removes_and_keeps_the_buffer_clean() {
    let (mut st, deleted) = overrides_state();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    apply_key("y", &mut st);
    assert_eq!(*deleted.borrow(), vec!["/tmp/main".to_owned()]);
    assert!(st.overrides.is_none());
    assert!(st.status.contains("removed 1"));
    assert!(!st.dirty());
    assert!(st.written);
}

#[test]
fn keep_leaves_the_file_and_says_shadowed() {
    let (mut st, deleted) = overrides_state();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    apply_key("esc", &mut st);
    assert!(deleted.borrow().is_empty());
    assert!(st.overrides.is_none());
    assert!(st.status.contains("kept 1"));
    assert!(st.status.contains("shadowed"));
}

#[test]
fn enter_confirms_the_highlighted_choice() {
    let (mut st, deleted) = overrides_state();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    apply_key("up", &mut st); // onto delete
    assert_eq!(st.overrides_index, 0);
    apply_key("\r", &mut st);
    assert_eq!(*deleted.borrow(), vec!["/tmp/main".to_owned()]);
    assert!(st.overrides.is_none());
}

#[test]
fn the_warning_owns_the_surface() {
    let (mut st, _) = overrides_state();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    let before = st.slots.clone();
    apply_key("e", &mut st); // a colour key behind the warning
    assert_eq!(st.slots, before);
    assert!(st.overrides.is_some());
    assert!(!st.quit);
    apply_key("esc", &mut st); // quits never happen behind it
    assert!(!st.quit);
    assert!(st.overrides.is_none());
}

#[test]
fn the_picker_does_not_open_behind_the_warning() {
    let (mut st, deleted) = overrides_state();
    // A library exists, but `t` behind the warning must not stack a picker.
    st.library = Some(Box::new(PickerLibrary::new(vec!["ash"], "", vec![])));
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    assert!(st.overrides.is_some()); // the warning armed on save
    apply_key("t", &mut st); // no picker stacks: `t` keeps instead
    assert!(st.overlay.is_none());
    assert!(st.overrides.is_none());
    assert!(deleted.borrow().is_empty()); // keep deletes nothing
}

#[test]
fn an_apply_with_overrides_stays_dirty_either_way() {
    let pushes: Rc<RefCell<Vec<Slots>>> = Rc::new(RefCell::new(Vec::new()));
    let p = pushes.clone();
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.theme = Some("ember".to_owned());
    st.path = "/tmp/huebox/ember.toml".to_owned();
    st.apply = Some(Box::new(move |_, _, slots| {
        p.borrow_mut().push(slots.clone());
        Ok(WriteOutcome {
            status: "applied (unsaved) → ghostty".to_owned(),
            overrides: Some(huebox::editor::OverridesReport {
                main: "/tmp/main".to_owned(),
                keys: vec!["background".to_owned()],
            }),
        })
    }));
    st.delete_overrides = Some(Box::new(|_| Ok("removed 1".to_owned())));
    st.sel = SLOTS.iter().position(|n| *n == "background").unwrap();
    dirty_and_adjust(&mut st);
    apply_key(APPLY_KEY, &mut st);
    assert!(st.dirty());
    assert!(st.overrides.is_some());
    apply_key("n", &mut st);
    assert!(st.dirty()); // keep never touches truth
    assert!(st.overrides.is_none());
    assert_eq!(pushes.borrow().len(), 1);
}

#[test]
fn delete_without_a_seam_closes_with_a_reason() {
    let (mut st, _) = overrides_state();
    st.delete_overrides = None;
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    apply_key("y", &mut st);
    assert!(st.overrides.is_none());
    assert_eq!(st.status, "cannot delete overrides here");
}

// ---------------------------------------------------------------------------
// backup seam
// ---------------------------------------------------------------------------

#[test]
fn first_save_snapshots_once_and_never_again() {
    let calls: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let c = calls.clone();
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.backup_path = Some("/tmp/kitty.conf".to_owned());
    st.backup = Some(Box::new(move |path| {
        c.borrow_mut().push(path.to_owned());
        true
    }));
    apply_key(SAVE, &mut st);
    assert_eq!(*calls.borrow(), vec!["/tmp/kitty.conf".to_owned()]);
    assert!(st.backup_made);
    apply_key("w", &mut st);
    apply_key(SAVE, &mut st);
    assert_eq!(calls.borrow().len(), 1); // never overwritten
}

#[test]
fn edits_alone_never_snapshot() {
    let calls: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let c = calls.clone();
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.backup_path = Some("/tmp/kitty.conf".to_owned());
    st.backup = Some(Box::new(move |path| {
        c.borrow_mut().push(path.to_owned());
        true
    }));
    for key in ["e", "d", "c", "w"] {
        apply_key(key, &mut st);
    }
    apply_key("esc", &mut st);
    apply_key("esc", &mut st);
    assert!(calls.borrow().is_empty());
    assert!(!st.backup_made);
}

#[test]
fn theme_files_get_no_backup() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.theme = Some("ember".to_owned());
    st.backup_path = None; // huebox owns it: no .bak
    apply_key(SAVE, &mut st);
    assert_eq!(writes.borrow().len(), 1);
    assert!(!st.backup_made);
}

// ---------------------------------------------------------------------------
// theme picker
// ---------------------------------------------------------------------------

fn theme_slots(seed: u32) -> Slots {
    let mut slots = full_slots();
    slots.set("background", &format!("#{seed:02x}2f4f"));
    slots.set("foreground", &format!("#{seed:02x}bad0"));
    slots
}

fn seed_of(name: &str) -> u32 {
    name.chars().map(|c| c as u32).sum()
}

struct PickerLibrary {
    names_in: RefCell<Vec<String>>,
    problem: String,
    misses: Vec<String>,
    calls: RefCell<Vec<String>>,
}

impl PickerLibrary {
    fn new(names: Vec<&str>, problem: &str, misses: Vec<&str>) -> Self {
        Self {
            names_in: RefCell::new(names.iter().map(|s| s.to_string()).collect()),
            problem: problem.to_owned(),
            misses: misses.iter().map(|s| s.to_string()).collect(),
            calls: RefCell::new(Vec::new()),
        }
    }
}

impl Library for PickerLibrary {
    fn names(&self) -> Vec<String> {
        self.names_in.borrow().clone()
    }

    fn load(&self, name: &str) -> Option<(Slots, String)> {
        self.calls.borrow_mut().push(format!("load {name}"));
        if self.misses.iter().any(|m| m == name) {
            return None;
        }
        Some((theme_slots(seed_of(name)), format!("/themes/{name}.toml")))
    }

    fn create(&self, name: &str, slots: &Slots, force: bool) -> Result<String, String> {
        self.calls.borrow_mut().push(format!(
            "create {name} force={force} bg={}",
            slots.get("background")
        ));
        if !self.problem.is_empty() {
            return Err(self.problem.clone());
        }
        self.names_in.borrow_mut().push(name.to_owned());
        Ok(format!("/themes/{name}.toml"))
    }
}

struct PickerFixture {
    st: EditorState,
    labels: Rc<RefCell<Vec<String>>>,
    library: Rc<PickerLibrary>,
    writes: SubjectWrites,
}

fn picker_state(
    names: Vec<&str>,
    theme: Option<&str>,
    prompts: Vec<Option<&str>>,
    problem: &str,
    misses: Vec<&str>,
) -> PickerFixture {
    let writes: SubjectWrites = Rc::new(RefCell::new(Vec::new()));
    let labels: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let library = Rc::new(PickerLibrary::new(names, problem, misses));
    let answers: Rc<RefCell<Vec<Option<String>>>> = Rc::new(RefCell::new(
        prompts.into_iter().map(|p| p.map(str::to_owned)).collect(),
    ));
    let mut st = EditorState::new(full_slots(), {
        let w = writes.clone();
        Box::new(move |theme: Option<&str>, path: &str, slots: &Slots| {
            w.borrow_mut()
                .push((theme.map(str::to_owned), path.to_owned(), slots.clone()));
            Ok(WriteOutcome::status(&format!(
                "saved {} → ghostty",
                theme.unwrap_or("?")
            )))
        })
    });
    st.theme = theme.map(str::to_owned);
    st.fmt = "ghostty".to_owned();
    st.path = "/themes/ember.toml".to_owned();
    let l = labels.clone();
    let a = answers.clone();
    st.prompt_name = Some(Box::new(move |label| {
        l.borrow_mut().push(label.to_owned());
        let mut answers = a.borrow_mut();
        if answers.is_empty() {
            None
        } else {
            answers.remove(0)
        }
    }));
    st.library = Some({
        let lib = library.clone();
        // Box the Rc as the trait object via a forwarding wrapper.
        struct Forward(Rc<PickerLibrary>);
        impl Library for Forward {
            fn names(&self) -> Vec<String> {
                self.0.names()
            }
            fn load(&self, name: &str) -> Option<(Slots, String)> {
                self.0.load(name)
            }
            fn create(&self, name: &str, slots: &Slots, force: bool) -> Result<String, String> {
                self.0.create(name, slots, force)
            }
        }
        Box::new(Forward(lib))
    });
    // Silence unused-path warning: the write closure records themes instead.
    let _ = &st.path;
    PickerFixture {
        st,
        labels,
        library,
        writes,
    }
}

#[test]
fn t_opens_the_picker_on_the_session_theme() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    assert_eq!(
        fx.st.overlay,
        Some(vec![
            "ash".to_owned(),
            "ember".to_owned(),
            "frost".to_owned()
        ])
    );
    assert_eq!(fx.st.overlay_index, 1); // ember, what we are editing
    assert_eq!(fx.st.status, "");
    assert_eq!(
        fx.st.picker_frame(),
        Some((
            vec!["ash".to_owned(), "ember".to_owned(), "frost".to_owned()],
            1,
            "ember".to_owned()
        ))
    );
}

#[test]
fn a_direct_session_opens_on_the_first_row() {
    let mut fx = picker_state(vec!["ash", "ember", "frost"], None, vec![], "", vec![]);
    apply_key("t", &mut fx.st);
    assert_eq!(fx.st.overlay_index, 0);
    assert_eq!(fx.st.picker_frame().unwrap().2, ""); // nothing to mark yet
}

#[test]
fn arrows_move_the_selection_and_stop_at_the_edges() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("up", &mut fx.st);
    assert_eq!(fx.st.overlay_index, 0);
    apply_key("up", &mut fx.st);
    assert_eq!(fx.st.overlay_index, 0);
    apply_key("down", &mut fx.st);
    apply_key("down", &mut fx.st);
    apply_key("down", &mut fx.st);
    assert_eq!(fx.st.overlay_index, 2);
    assert_eq!(fx.st.sel, huebox::editor::state::INITIAL_SEL); // slots untouched
}

#[test]
fn enter_opens_the_theme_saves_it_and_resets_the_session() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("down", &mut fx.st); // a selection to lose
    apply_key("w", &mut fx.st); // and an edit to lose
    apply_key("r", &mut fx.st); // clean again
    apply_key("t", &mut fx.st);
    apply_key("down", &mut fx.st);
    fx.st.armed = true; // a pending discard must not follow the switch
    apply_key("\r", &mut fx.st);
    assert_eq!(fx.st.theme, Some("frost".to_owned()));
    assert_eq!(fx.st.path, "/themes/frost.toml");
    assert_eq!(fx.st.slots, theme_slots(seed_of("frost")));
    assert_eq!(fx.st.saved, fx.st.slots); // a fresh theme is clean
    assert!(fx.st.undo.is_empty());
    assert_eq!(fx.st.sel, huebox::editor::state::INITIAL_SEL);
    assert!(!fx.st.armed);
    assert!(fx.st.overlay.is_none());
    // The switch is the save: the theme is what the terminal gets.
    assert_eq!(
        written_subjects(&fx),
        vec![(Some("frost".to_owned()), "/themes/frost.toml".to_owned())]
    );
    assert_eq!(written_slots(&fx)[0], fx.st.slots);
    assert_eq!(fx.st.status, "saved frost → ghostty");
    assert_eq!(*fx.library.calls.borrow(), vec!["load frost".to_owned()]);
}

fn written_slots(fx: &PickerFixture) -> Vec<Slots> {
    fx.writes
        .borrow()
        .iter()
        .map(|(_, _, slots)| slots.clone())
        .collect()
}

fn written_subjects(fx: &PickerFixture) -> Vec<(Option<String>, String)> {
    fx.writes
        .borrow()
        .iter()
        .map(|(theme, path, _)| (theme.clone(), path.clone()))
        .collect()
}

#[test]
fn the_compositor_enter_opens_the_selected_theme() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("down", &mut fx.st);
    apply_key("enter", &mut fx.st);
    assert_eq!(fx.st.theme, Some("frost".to_owned()));
    assert!(fx.st.overlay.is_none());
    assert_eq!(fx.writes.borrow().len(), 1);
}

#[test]
fn a_dirty_switch_is_blocked_with_the_exact_status() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st); // dirty
    let dirty = fx.st.slots.clone();
    apply_key("t", &mut fx.st);
    apply_key("down", &mut fx.st);
    apply_key("\r", &mut fx.st);
    assert_eq!(fx.st.status, "save (Ctrl+S) or revert (r) first");
    assert_eq!(fx.st.theme, Some("ember".to_owned()));
    assert_eq!(fx.st.slots, dirty);
    assert!(fx.st.overlay.is_some()); // the picker stays up
    assert!(fx.library.calls.borrow().is_empty()); // nothing was even loaded
    assert!(fx.writes.borrow().is_empty());
}

#[test]
fn esc_and_t_put_the_editor_back_without_quitting() {
    for closer in ["esc", "t", "Q", "\x03"] {
        let mut fx = picker_state(
            vec!["ash", "ember", "frost"],
            Some("ember"),
            vec![],
            "",
            vec![],
        );
        apply_key("w", &mut fx.st); // dirty: Esc must not quit
        apply_key("t", &mut fx.st);
        apply_key(closer, &mut fx.st);
        assert!(fx.st.overlay.is_none(), "closer {closer}");
        assert!(!fx.st.quit);
        assert!(!fx.st.armed);
        assert_eq!(fx.st.status, "");
        assert_eq!(fx.st.theme, Some("ember".to_owned()));
    }
}

#[test]
fn the_picker_swallows_the_rest_of_the_key_surface() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    let before = fx.st.slots.clone();
    apply_key("t", &mut fx.st);
    // `n` / `N` are the picker's own creation keys, tested below; the
    // rest of the surface never reaches the buffer behind the list.
    for key in ["e", "d", "c", "u", "r", "f", SAVE, "h"] {
        apply_key(key, &mut fx.st);
    }
    assert_eq!(fx.st.slots, before);
    assert_eq!(fx.st.mult, 1);
    assert!(fx.writes.borrow().is_empty());
    assert_eq!(fx.st.status, "");
    assert!(!fx.st.quit);
    assert_eq!(fx.st.overlay_index, 1); // only arrows moved it
}

#[test]
fn capital_n_in_the_picker_creates_saves_and_stays_open() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("dusk")],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st); // dirty: creation is fine
    let buffer = fx.st.slots.clone();
    apply_key("t", &mut fx.st);
    apply_key("N", &mut fx.st);
    let calls = fx.library.calls.borrow();
    assert!(calls[0].starts_with("create dusk force=false"), "{calls:?}");
    // ... and then through the ordinary save pipeline, push included,
    // with the picker still up on the new row.
    assert_eq!(written_slots(&fx).len(), 1);
    assert_eq!(written_slots(&fx)[0], buffer);
    assert_eq!(written_slots(&fx)[0], fx.st.slots);
    assert_eq!(fx.st.status, "saved dusk → ghostty");
    assert!(fx.st.written);
    assert!(!fx.st.dirty());
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
    assert_eq!(fx.st.path, "/themes/dusk.toml");
    assert_eq!(fx.st.created, Some("dusk".to_owned()));
    assert_eq!(fx.st.overlay.as_ref().unwrap().last().unwrap(), "dusk");
    assert_eq!(
        fx.st.overlay_index,
        fx.st.overlay.as_ref().unwrap().len() - 1
    );
    assert_eq!(*fx.labels.borrow(), vec!["  new theme name: ".to_owned()]);
}

#[test]
fn capital_n_in_the_picker_cancelled_writes_nothing() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![None],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st);
    apply_key("t", &mut fx.st);
    apply_key("N", &mut fx.st);
    assert!(fx.library.calls.borrow().is_empty());
    assert!(fx.writes.borrow().is_empty());
    assert!(fx.st.dirty()); // the buffer is untouched
    assert_eq!(fx.st.status, "cancelled - no theme created");
    assert_eq!(fx.st.theme, Some("ember".to_owned()));
    assert!(fx.st.overlay.is_some()); // the picker stays up
}

#[test]
fn capital_n_in_the_picker_confirms_a_taken_name_in_words() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("frost"), Some("y")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("N", &mut fx.st);
    assert!(
        fx.library.calls.borrow()[0].contains("force=true"),
        "{:?}",
        fx.library.calls.borrow()
    );
    assert!(fx.labels.borrow()[1].contains("frost exists"));
    assert_eq!(fx.st.status, "saved frost → ghostty");
    assert!(fx.st.overlay.is_some());
}

#[test]
fn enter_on_an_empty_picker_writes_nothing_and_never_panics() {
    let mut fx = picker_state(vec!["ash"], Some("ember"), vec![], "", vec![]);
    fx.st.overlay = Some(vec![]); // nothing listed: no row to open
    for key in ["\r", "\n", "enter"] {
        apply_key(key, &mut fx.st);
        assert!(fx.writes.borrow().is_empty(), "key {key:?}");
        assert!(fx.library.calls.borrow().is_empty(), "key {key:?}");
        assert_eq!(
            fx.st.status, "no themes yet - N makes one from this buffer",
            "key {key:?}"
        );
        assert!(fx.st.overlay.is_some(), "key {key:?}");
    }
}

#[test]
fn the_headless_escape_closes_the_picker_like_esc() {
    for closer in ["esc", "escape"] {
        let mut fx = picker_state(
            vec!["ash", "ember", "frost"],
            Some("ember"),
            vec![],
            "",
            vec![],
        );
        apply_key("w", &mut fx.st); // dirty: closing must not quit
        apply_key("t", &mut fx.st);
        apply_key(closer, &mut fx.st);
        assert!(fx.st.overlay.is_none(), "closer {closer}");
        assert!(!fx.st.quit, "closer {closer}");
        assert_eq!(fx.st.theme, Some("ember".to_owned()));
    }
}

#[test]
fn a_theme_that_will_not_open_keeps_the_buffer() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec!["frost"],
    );
    apply_key("t", &mut fx.st);
    apply_key("down", &mut fx.st);
    apply_key("\r", &mut fx.st);
    assert_eq!(fx.st.status, "frost could not be opened");
    assert_eq!(fx.st.theme, Some("ember".to_owned()));
    assert_eq!(fx.st.slots, full_slots());
    assert!(fx.st.overlay.is_some());
}

#[test]
fn an_empty_library_says_so_and_opens_nothing() {
    let mut fx = picker_state(vec![], Some("ember"), vec![], "", vec![]);
    apply_key("t", &mut fx.st);
    assert!(fx.st.overlay.is_none());
    assert!(fx.st.status.contains("no themes yet"));
}

#[test]
fn without_a_library_the_picker_is_a_no_op() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    apply_key("t", &mut st);
    assert!(st.overlay.is_none());
    assert_eq!(st.status, "no theme library in this session");
    apply_key("N", &mut st);
    assert_eq!(st.status, "no theme library in this session");
    assert!(st.theme.is_none());
}

#[test]
fn n_creates_a_theme_from_the_buffer_and_stays_up() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("dusk")],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st); // dirty: creation is fine
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    let calls = fx.library.calls.borrow();
    assert!(calls[0].starts_with("create dusk force=false"), "{calls:?}");
    assert_eq!(fx.st.theme, Some("dusk".to_owned())); // the session follows
    assert_eq!(fx.st.path, "/themes/dusk.toml");
    assert_eq!(fx.st.overlay.as_ref().unwrap().last().unwrap(), "dusk");
    assert_eq!(
        fx.st.overlay_index,
        fx.st.overlay.as_ref().unwrap().len() - 1
    );
    assert_eq!(fx.st.status, "created dusk - current now");
    assert!(!fx.st.dirty());
    assert_eq!(*fx.labels.borrow(), vec!["  new theme name: ".to_owned()]);
}

#[test]
fn n_keeps_the_overlay_usable() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("dusk")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    apply_key("esc", &mut fx.st);
    assert!(fx.st.overlay.is_none());
    assert!(!fx.st.quit);
}

#[test]
fn a_taken_name_is_confirmed_in_words_not_a_modal() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("ember"), Some("y")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    assert!(
        fx.library.calls.borrow()[0].contains("force=true"),
        "{:?}",
        fx.library.calls.borrow()
    );
    assert!(fx.labels.borrow()[1].contains("ember exists"));
    assert!(fx.labels.borrow()[1].contains("y overwrites"));
}

#[test]
fn another_name_at_the_prompt_replaces_the_answer() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("ember"), Some("dusk")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    assert!(fx.library.calls.borrow()[0].starts_with("create dusk force=false"));
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
}

#[test]
fn a_cancelled_confirm_leaves_the_taken_theme_alone() {
    for answer in [Some(""), None] {
        let mut fx = picker_state(
            vec!["ash", "ember", "frost"],
            Some("ember"),
            vec![Some("ember"), answer],
            "",
            vec![],
        );
        apply_key("t", &mut fx.st);
        apply_key("n", &mut fx.st);
        assert!(fx.library.calls.borrow().is_empty());
        assert_eq!(fx.st.status, "cancelled - ember is untouched");
        assert_eq!(fx.st.theme, Some("ember".to_owned()));
    }
}

#[test]
fn a_cancelled_prompt_creates_nothing() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    assert!(fx.library.calls.borrow().is_empty());
    assert_eq!(fx.st.status, "cancelled - no theme created");
}

#[test]
fn a_retype_that_is_also_taken_creates_nothing() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("ember"), Some("frost")],
        "",
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    assert!(fx.library.calls.borrow().is_empty());
    assert!(fx.st.status.contains("frost exists too"));
}

#[test]
fn a_refusal_comes_back_as_a_status_not_an_exception() {
    let problem = "invalid theme name (letters, digits, - and _, 64 max)";
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("not a name")],
        problem,
        vec![],
    );
    apply_key("t", &mut fx.st);
    apply_key("n", &mut fx.st);
    assert_eq!(fx.st.status, problem);
    assert_eq!(fx.st.theme, Some("ember".to_owned()));
    assert!(fx.writes.borrow().is_empty());
}

#[test]
fn capital_n_saves_the_buffer_as_a_new_theme() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![Some("dusk")],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st);
    apply_key("N", &mut fx.st);
    assert!(fx.library.calls.borrow()[0].starts_with("create dusk force=false"));
    // ... and then through the ordinary save pipeline, push included
    assert_eq!(written_slots(&fx).len(), 1);
    assert_eq!(written_slots(&fx)[0], fx.st.slots);
    assert_eq!(fx.st.status, "saved dusk → ghostty");
    assert!(fx.st.written);
    assert!(!fx.st.dirty());
    assert!(fx.st.undo.is_empty());
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
    assert_eq!(fx.st.path, "/themes/dusk.toml");
}

#[test]
fn capital_n_without_a_name_writes_nothing() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![None],
        "",
        vec![],
    );
    apply_key("w", &mut fx.st);
    apply_key("N", &mut fx.st); // prompt cancelled: no name
    assert!(fx.writes.borrow().is_empty());
    assert!(!fx.st.written);
    assert!(fx.st.dirty()); // the buffer is untouched
    assert_eq!(fx.st.status, "cancelled - no theme created");
    assert_eq!(fx.st.theme, Some("ember".to_owned()));
    assert!(fx.library.calls.borrow().is_empty());
}

#[test]
fn capital_n_migrates_a_direct_session_off_the_config() {
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        None,
        vec![Some("dusk")],
        "",
        vec![],
    );
    fx.st.path = "/home/you/.config/kitty/kitty.conf".to_owned();
    fx.st.backup_path = Some("/home/you/.config/kitty/kitty.conf".to_owned());
    apply_key("w", &mut fx.st);
    apply_key("N", &mut fx.st);
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
    assert_eq!(fx.st.path, "/themes/dusk.toml");
    assert!(fx.st.backup_path.is_none()); // huebox owns it now: no .bak
    assert_eq!(head_label(&fx.st), "dusk ghostty"); // saved
}

// ---------------------------------------------------------------------------
// first-run setup choice
// ---------------------------------------------------------------------------

fn setup_state(prompts: Vec<Option<&str>>, problem: &str) -> PickerFixture {
    let mut fx = picker_state(vec![], None, prompts, problem, vec![]);
    assert!(enter_setup(&mut fx.st));
    assert_eq!(fx.st.setup, Some(0));
    fx
}

// ---------------------------------------------------------------------------
// first-run setup choice
// ---------------------------------------------------------------------------

#[test]
fn enter_setup_opens_on_import_and_stays_put() {
    let mut fx = picker_state(vec![], None, vec![], "", vec![]);
    assert!(enter_setup(&mut fx.st));
    assert_eq!(fx.st.setup, Some(0));
    assert_eq!(fx.st.status, "");
    assert!(enter_setup(&mut fx.st)); // idempotent
    assert_eq!(fx.st.setup, Some(0));
}

#[test]
fn no_setup_when_the_library_has_themes() {
    let mut fx = picker_state(vec!["ash"], Some("ash"), vec![], "", vec![]);
    assert!(!enter_setup(&mut fx.st));
    assert!(fx.st.setup.is_none());
}

#[test]
fn no_setup_without_a_library() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    assert!(!enter_setup(&mut st));
    assert!(!needs_setup(&st));
}

#[test]
fn setup_arrows_move_between_the_two_choices() {
    let mut fx = setup_state(vec![], "");
    apply_key("down", &mut fx.st);
    assert_eq!(fx.st.setup, Some(1));
    apply_key("down", &mut fx.st);
    assert_eq!(fx.st.setup, Some(1));
    apply_key("right", &mut fx.st);
    assert_eq!(fx.st.setup, Some(1));
    apply_key("up", &mut fx.st);
    assert_eq!(fx.st.setup, Some(0));
    apply_key("up", &mut fx.st);
    assert_eq!(fx.st.setup, Some(0));
    apply_key("left", &mut fx.st);
    assert_eq!(fx.st.setup, Some(0));
    assert_eq!(fx.st.sel, huebox::editor::state::INITIAL_SEL); // slots untouched
}

#[test]
fn enter_on_import_asks_the_shell_for_the_popup() {
    for key in ["\r", "\n", "enter"] {
        let mut fx = setup_state(vec![], "");
        let before = fx.st.slots.clone();
        apply_key(key, &mut fx.st);
        assert!(fx.st.setup.is_none(), "key {key:?}");
        assert!(fx.st.import_pending);
        assert_eq!(fx.st.slots, before);
        assert!(!fx.st.quit);
        assert_eq!(fx.st.status, "");
    }
}

#[test]
fn i_imports_from_either_row() {
    for key in ["i", "I"] {
        let mut fx = setup_state(vec![], "");
        apply_key("down", &mut fx.st);
        apply_key(key, &mut fx.st);
        assert!(fx.st.setup.is_none(), "key {key:?}");
        assert!(fx.st.import_pending, "key {key:?}");
    }
}

#[test]
fn n_opens_the_inline_field_and_typing_names_it() {
    let mut fx = setup_state(vec![], "");
    apply_key("n", &mut fx.st);
    assert_eq!(fx.st.setup_name, Some(String::new()));
    assert_eq!(fx.st.setup, Some(1)); // still choosing underneath
    for key in ["d", "u", "s", "k"] {
        apply_key(key, &mut fx.st);
    }
    assert_eq!(fx.st.setup_name, Some("dusk".to_owned()));
    apply_key("\r", &mut fx.st);
    let calls = fx.library.calls.borrow();
    assert!(calls[0].starts_with("create dusk force=false"), "{calls:?}");
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
    assert_eq!(fx.st.path, "/themes/dusk.toml");
    assert_eq!(fx.st.created, Some("dusk".to_owned()));
    assert!(fx.st.setup.is_none());
    assert!(fx.st.setup_name.is_none());
    assert!(!fx.st.import_pending);
    assert!(!fx.st.dirty());
    assert_eq!(fx.st.status, "created dusk - current now");
    assert!(fx.labels.borrow().is_empty()); // typed, never prompted
}

#[test]
fn enter_on_the_new_row_names_too() {
    let mut fx = setup_state(vec![], "");
    apply_key("down", &mut fx.st);
    apply_key("\r", &mut fx.st);
    assert_eq!(fx.st.setup_name, Some(String::new()));
    apply_key("enter", &mut fx.st); // empty: back to the choices
    assert!(fx.st.setup_name.is_none());
    assert_eq!(fx.st.setup, Some(1));
    assert_eq!(fx.st.status, "cancelled - no theme created");
    assert!(fx.st.theme.is_none());
}

#[test]
fn esc_backs_out_of_naming_to_the_choices() {
    let mut fx = setup_state(vec![], "");
    apply_key("n", &mut fx.st);
    apply_key("d", &mut fx.st);
    apply_key("u", &mut fx.st);
    apply_key("esc", &mut fx.st);
    assert!(fx.st.setup_name.is_none());
    assert_eq!(fx.st.setup, Some(1));
    assert_eq!(fx.st.status, "");
    assert!(fx.st.theme.is_none());
    assert!(!fx.st.quit); // quitting stays a row away
}

#[test]
fn backspace_deletes_and_arrows_are_swallowed() {
    let mut fx = setup_state(vec![], "");
    apply_key("n", &mut fx.st);
    for key in ["d", "u", "s", "k", "x"] {
        apply_key(key, &mut fx.st);
    }
    apply_key("backspace", &mut fx.st);
    assert_eq!(fx.st.setup_name, Some("dusk".to_owned()));
    let before = fx.st.slots.clone();
    for key in ["up", "down", "left", "right", "t", SAVE, "\x03"] {
        apply_key(key, &mut fx.st);
    }
    assert_eq!(fx.st.setup_name, Some("duskt".to_owned())); // printables type;
                                                            // arrows and control codes never reach the buffer or the session
    assert_eq!(fx.st.setup, Some(1));
    assert_eq!(fx.st.slots, before);
    assert!(!fx.st.quit);
}

#[test]
fn a_taken_name_stays_in_the_field_with_the_reason() {
    let mut fx = setup_state(vec![], "");
    fx.library.names_in.borrow_mut().push("dusk".to_owned());
    apply_key("n", &mut fx.st);
    for key in ["d", "u", "s", "k"] {
        apply_key(key, &mut fx.st);
    }
    apply_key("\r", &mut fx.st);
    assert!(fx.library.calls.borrow().is_empty());
    assert_eq!(fx.st.status, "dusk exists - type another name");
    assert_eq!(fx.st.setup_name, Some("dusk".to_owned())); // kept, to fix not retype
    assert_eq!(fx.st.setup, Some(1));
    assert!(fx.st.theme.is_none());
}

#[test]
fn a_setup_refusal_comes_back_as_a_status_not_an_exception() {
    let problem = "invalid theme name (letters, digits, - and _, 64 max)";
    let mut fx = setup_state(vec![], problem);
    apply_key("n", &mut fx.st);
    for key in ["d", "u", "s", "k"] {
        apply_key(key, &mut fx.st);
    }
    apply_key("\r", &mut fx.st);
    assert_eq!(fx.st.status, problem);
    assert_eq!(fx.st.setup_name, Some("dusk".to_owned()));
    assert_eq!(fx.st.setup, Some(1));
    assert!(fx.st.theme.is_none());
    assert!(fx.writes.borrow().is_empty());
}

#[test]
fn esc_quits_instead_of_revealing_an_empty_editor() {
    for closer in ["esc", "escape", "Q", "\x03"] {
        let mut fx = setup_state(vec![], "");
        apply_key(closer, &mut fx.st);
        assert!(fx.st.quit, "closer {closer}");
        assert!(fx.st.setup.is_none());
    }
}

#[test]
fn the_choice_swallows_the_rest_of_the_key_surface() {
    let mut fx = setup_state(vec![], "");
    let before = fx.st.slots.clone();
    for key in ["w", "e", "s", "d", "x", "c", "u", "r", "f", SAVE, "h", "t"] {
        apply_key(key, &mut fx.st);
    }
    assert_eq!(fx.st.slots, before);
    assert_eq!(fx.st.mult, 1);
    assert!(fx.writes.borrow().is_empty());
    assert!(!fx.st.quit);
    assert_eq!(fx.st.setup, Some(0));
    assert_eq!(fx.st.status, "");
    assert!(fx.st.overlay.is_none()); // no picker behind it
}

#[test]
fn setup_after_import_re_arms_an_empty_library() {
    let mut fx = setup_state(vec![], "");
    apply_key("\r", &mut fx.st); // import chosen
    assert!(fx.st.import_pending);
    // Nothing landed: the choice is owed again.
    huebox::editor::keys::setup_after_import(&mut fx.st);
    assert_eq!(fx.st.setup, Some(0));
}

// ---------------------------------------------------------------------------
// status bar
// ---------------------------------------------------------------------------

fn headed_state(theme: Option<&str>, fmt: &str, path: &str) -> EditorState {
    let mut st = EditorState::new(
        full_slots(),
        Box::new(|_, _, _| Ok(WriteOutcome::status("saved"))),
    );
    st.theme = theme.map(str::to_owned);
    st.fmt = fmt.to_owned();
    st.path = path.to_owned();
    st
}

#[test]
fn a_clean_theme_session_names_the_theme_and_the_target() {
    assert_eq!(
        head_label(&headed_state(
            Some("ember"),
            "ghostty",
            "/themes/ember.toml"
        )),
        "ember ghostty"
    );
}

#[test]
fn a_dirty_buffer_puts_a_dot_between_them() {
    let mut st = headed_state(Some("ember"), "ghostty", "/themes/ember.toml");
    apply_key("w", &mut st);
    assert!(st.dirty());
    assert_eq!(head_label(&st), format!("ember {DIRTY_MARK} ghostty"));
    apply_key(SAVE, &mut st); // saved: clean again
    assert_eq!(head_label(&st), "ember ghostty");
}

#[test]
fn with_no_named_target_only_the_theme_is_shown() {
    assert_eq!(
        head_label(&headed_state(Some("ember"), "", "/themes/ember.toml")),
        "ember"
    );
    let mut st = headed_state(Some("ember"), "", "/themes/ember.toml");
    apply_key("w", &mut st);
    assert_eq!(head_label(&st), format!("ember {DIRTY_MARK}"));
}

#[test]
fn a_direct_session_names_the_config() {
    let st = headed_state(None, "ghostty", "/tmp/kitty.conf");
    assert_eq!(head_label(&st), "direct:/tmp/kitty.conf");
    assert_eq!(session_path(&st), ""); // not printed twice
    assert_eq!(
        session_path(&headed_state(
            Some("ember"),
            "ghostty",
            "/themes/ember.toml"
        )),
        "/themes/ember.toml"
    );
}

#[test]
fn top_subject_prices_the_name_not_the_path() {
    let st = headed_state(None, "ghostty", "/home/you/.config/kitty/kitty.conf");
    let head = "direct:/home/you/.config/kitty/kitty.conf";
    assert_eq!(
        top_subject(&st, head),
        (
            "direct".to_owned(),
            "/home/you/.config/kitty/kitty.conf".to_owned()
        )
    );
    let st = headed_state(Some("ember"), "ghostty", "/themes/ember.toml");
    assert_eq!(
        top_subject(&st, "ember ghostty"),
        ("ember ghostty".to_owned(), "/themes/ember.toml".to_owned())
    );
    let st = headed_state(None, "ghostty", "");
    assert_eq!(
        top_subject(&st, "ghostty"),
        ("ghostty".to_owned(), String::new())
    );
}

// ---------------------------------------------------------------------------
// dialog rows
// ---------------------------------------------------------------------------

#[test]
fn theme_lines_name_themes_and_mark_current() {
    let names = vec!["ash".to_owned(), "ember".to_owned(), "frost".to_owned()];
    let out = theme_lines(&names, 0, "ember", 80, 24, "", &full_slots(), true);
    let body: String = out
        .rows
        .iter()
        .map(|row| row_text(row))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("themes"));
    assert!(body.contains("* ember")); // the session's current
    assert!(body.contains("ash"));
    assert!(body.contains("> ash")); // and the selection
    assert!(body.contains("Enter use"));
}

#[test]
fn theme_lines_hold_the_width_and_height() {
    let names: Vec<String> = (0..34).map(|i| format!("theme-{i:02}")).collect();
    for (cols, rows) in [(100, 30), (80, 24), (60, 16), (40, 12)] {
        let out = theme_lines(&names, 17, "theme-17", cols, rows, "", &full_slots(), true);
        assert!(out.rows.len() <= rows, "at {cols}x{rows}");
        for row in &out.rows {
            assert!(row_visible(row) <= cols, "at {cols}x{rows}");
        }
    }
}

#[test]
fn an_empty_library_says_how_to_start_one() {
    let out = theme_lines(&[], 0, "", 80, 24, "", &full_slots(), true);
    let body: String = out
        .rows
        .iter()
        .map(|row| row_text(row))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("no themes yet"));
    assert!(body.contains("N makes one from this buffer"));
}

#[test]
fn the_status_line_is_the_last_row() {
    let names = vec!["ash".to_owned()];
    let out = theme_lines(
        &names,
        0,
        "ash",
        80,
        24,
        "save (Ctrl+S) or revert (r) first",
        &full_slots(),
        true,
    );
    let last = row_text(out.rows.last().unwrap());
    assert!(last.contains("save (Ctrl+S) or revert (r) first"));
}

#[test]
fn theme_hits_name_library_rows() {
    let names = vec!["ash".to_owned(), "ember".to_owned(), "frost".to_owned()];
    let hits = theme_hits(&names, 1, "ember", 80, 24, "", &full_slots());
    assert_eq!(
        hits.iter().map(|h| h.slot).collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
    assert_eq!(slot_at(&hits, 2, hits[1].y), Some(1));
    assert!(slot_at(&hits, 0, 0).is_none()); // chrome selects nothing
}

#[test]
fn setup_lines_are_a_clipped_frame_with_two_clickable_rows() {
    let out = setup_lines(0, 80, 24, "", &full_slots(), None, true);
    assert!(out.rows.len() <= 24);
    for row in &out.rows {
        assert!(row_visible(row) <= 80);
    }
    let body: String = out
        .rows
        .iter()
        .map(|row| row_text(row))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("Import themes"));
    assert!(body.contains("Create new theme"));
    let hits = setup_hits(1, 80, 24, "", &full_slots());
    assert_eq!(hits.iter().map(|h| h.slot).collect::<Vec<_>>(), vec![0, 1]);
}

#[test]
fn setup_naming_lines_show_the_field_and_no_choices() {
    let out = setup_lines(1, 80, 24, "", &full_slots(), Some("dusk"), true);
    assert!(out.rows.len() <= 24);
    let body: String = out
        .rows
        .iter()
        .map(|row| row_text(row))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("dusk_")); // the typed name + cursor
    assert!(!body.contains("Import themes"));
    assert!(!body.contains("Create new theme"));
    assert!(body.contains("save")); // the field's own footer
    assert!(out.hits.is_empty()); // nothing clickable while naming
}

#[test]
fn overrides_lines_name_the_file_and_both_choices() {
    let keys = vec!["background".to_owned()];
    let out = overrides_lines("/tmp/main", &keys, 1, 80, 24, "", &full_slots(), true);
    let body: String = out
        .rows
        .iter()
        .map(|row| row_text(row))
        .collect::<Vec<_>>()
        .join("\n");
    assert!(body.contains("/tmp/main"));
    assert!(body.contains("background"));
    assert!(body.contains("yes, delete"));
    assert!(body.contains("no, keep overrides"));
    let hits = overrides_hits("/tmp/main", &keys, 1, 80, 24, "", &full_slots());
    assert_eq!(hits.iter().map(|h| h.slot).collect::<Vec<_>>(), vec![0, 1]);
}

// ---------------------------------------------------------------------------
// clicks through hits, then through apply_key
// ---------------------------------------------------------------------------

fn editor_frame(sel: usize, cols: usize, rows: usize) -> huebox::render::frame::FrameOut {
    frame(
        &FrameOpts {
            label: "ghostty".to_owned(),
            path: String::new(),
            sel,
            undo_len: 0,
            status: String::new(),
            cols,
            rows,
            use_banner: Some(false),
            indent: "  ".to_owned(),
            footer: true,
        },
        &full_slots(),
    )
}

#[test]
fn every_hit_claims_a_cell_the_frame_marks() {
    for (cols, rows) in [(120, 30), (100, 30), (80, 24), (60, 16), (40, 12)] {
        let out = editor_frame(5, cols, rows);
        for hit in &out.hits {
            let painted = plain_rows(&editor_frame(hit.slot, cols, rows));
            assert!(hit.y < painted.len(), "hit past the frame at {cols}x{rows}");
            let cell: String = painted[hit.y].chars().skip(hit.x0 + 1).take(1).collect();
            assert_eq!(
                cell, ">",
                "row {} col {} is no `>` marker at {cols}x{rows}",
                hit.y, hit.x0
            );
        }
    }
}

#[test]
fn a_swatch_click_selects_through_hits_not_directly() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.grid = grid_geometry(80, "  ");
    let out = editor_frame(st.sel, 80, 24);
    // The first palette cell of the row below the selection.
    let target = out.hits.iter().find(|h| h.slot == 8).unwrap().slot;
    let (x0, y) = {
        let hit = out.hits.iter().find(|h| h.slot == 8).unwrap();
        (hit.x0, hit.y)
    };
    assert_eq!(slot_at(&out.hits, x0, y), Some(8));
    click_slot(&mut st, target);
    assert_eq!(st.sel, 8);
    assert!(!st.dirty()); // selection, not an edit
    assert_eq!(st.status, "");
}

#[test]
fn dialog_clicks_confirm_through_the_key_surface() {
    // Picker: the row moves onto the click and opens.
    let mut fx = picker_state(
        vec!["ash", "ember", "frost"],
        Some("ember"),
        vec![],
        "",
        vec![],
    );
    apply_key("r", &mut fx.st); // clean (no-op) then open
    apply_key("t", &mut fx.st);
    click_theme(&mut fx.st, 0);
    assert_eq!(fx.st.theme, Some("ash".to_owned()));
    assert!(fx.st.overlay.is_none());

    // Setup: the choice moves onto the click and confirms.
    let mut sx = setup_state(vec![], "");
    click_setup(&mut sx.st, 0);
    assert!(sx.st.import_pending);

    // Overrides: the choice moves onto the click and confirms (keep).
    let (mut ox, _) = overrides_state();
    dirty_and_adjust(&mut ox);
    apply_key(SAVE, &mut ox);
    click_overrides(&mut ox, 1);
    assert!(ox.overrides.is_none());
    assert!(ox.status.contains("kept") || ox.status.contains("shadowed"));
}

// ---------------------------------------------------------------------------
// exit report
// ---------------------------------------------------------------------------

#[test]
fn report_session_says_what_happened() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = recording_state(full_slots(), &writes);
    st.theme = Some("ember".to_owned());
    st.path = "/themes/ember.toml".to_owned();
    dirty_and_adjust(&mut st);
    apply_key(SAVE, &mut st);
    let report = report_session(&st, &["pushed".to_owned()], &[]);
    assert_eq!(
        report.stdout,
        vec![
            "  saved theme ember  /themes/ember.toml".to_owned(),
            String::new()
        ]
    );
    assert_eq!(report.stderr, vec!["huebox: pushed".to_owned()]);

    let mut dirty = recording_state(full_slots(), &writes);
    dirty_and_adjust(&mut dirty);
    let report = report_session(&dirty, &[], &[]);
    assert_eq!(
        report.stdout,
        vec!["  nothing saved - the buffer was discarded\n".to_owned()]
    );

    let clean = recording_state(full_slots(), &writes);
    let report = report_session(&clean, &[], &[]);
    assert_eq!(report.stdout, vec!["  no changes\n".to_owned()]);
}

// ---------------------------------------------------------------------------
// the loop contract: one geometry per frame, buffer draws, save writes once
// ---------------------------------------------------------------------------

/// The headless drive: what `tests/session.py` does minus the paint —
/// grid per frame, key through `apply_key`, stop on quit or spent keys.
fn drive(st: &mut EditorState, keys: &[&str], cols: usize) {
    st.grid = grid_geometry(cols, "  ");
    if st.overlay.is_none() {
        enter_setup(st);
    }
    for key in keys {
        st.grid = grid_geometry(cols, "  ");
        if *key != "resize" {
            apply_key(key, st);
        }
        if st.quit {
            break;
        }
    }
}

#[test]
fn the_loop_draws_from_the_buffer_and_saves_once() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    st.path = "/tmp/kitty.conf".to_owned();
    drive(&mut st, &["w", SAVE, "esc"], 80);
    assert_eq!(writes.borrow().len(), 1);
    assert_eq!(
        writes.borrow()[0].get("palette-5"),
        st.slots.get("palette-5")
    );
    // Direct mode names the config in the head.
    assert_eq!(head_label(&st), "direct:/tmp/kitty.conf");
}

#[test]
fn a_dirty_quit_takes_two_escapes() {
    let writes: Writes = Rc::new(RefCell::new(Vec::new()));
    let mut st = background_state(&writes);
    drive(&mut st, &["w", "esc", "esc"], 80);
    assert!(writes.borrow().is_empty()); // discarded, not saved
    assert!(st.status.contains("unsaved changes") || st.quit);
    assert!(st.quit);
}

#[test]
fn setup_opens_for_an_empty_library_and_quits_clean() {
    let mut fx = picker_state(vec![], None, vec![], "", vec![]);
    drive(&mut fx.st, &["esc"], 80);
    assert!(fx.st.quit);
}

#[test]
fn choosing_new_in_a_driven_session_names_and_adopts() {
    let mut fx = picker_state(vec![], None, vec![], "", vec![]);
    drive(&mut fx.st, &["n", "d", "u", "s", "k", "\r", "esc"], 80);
    assert_eq!(fx.st.theme, Some("dusk".to_owned()));
    assert_eq!(fx.st.created, Some("dusk".to_owned()));
    assert!(fx.st.quit);
    // The buffer is what got written.
    let calls = fx.library.calls.borrow();
    assert!(calls[0].starts_with("create dusk force=false"), "{calls:?}");
}
