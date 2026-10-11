//! Ported `tests/test_import.py` (seam half): slugify, cursor/selection,
//! confirm mapping, open/close keys, provider failure tolerance.
//!
//! Nothing here needs a compositor: `ImportState` + `handle_key` take the
//! injected [`ImportLibrary`](huebox_editor::ImportLibrary) only.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use huebox_core::Slots;
use huebox_editor::{
    abandon, batch_unique, clear_selection, click_group, click_row, close_import, collapse_cursor,
    confirm_import, cursor_id, cursor_slots, expand_cursor, handle_key, import_may_open, move_down,
    move_up, open_import, plan_confirm, preview_slots, select_all_visible, slugify, toggle,
    toggle_group, unique_slug, visible_rows, wheel, ImportLibrary, ImportState,
};
use huebox_editor::{EditorState, WriteOutcome};

fn slots_with(bg: &str) -> Slots {
    let mut slots = Slots::default();
    for name in huebox_render::palette::SLOTS {
        slots.set(name, bg);
    }
    slots
}

struct FakeImport {
    entries: HashMap<String, Vec<(String, String)>>,
    by_path: HashMap<String, Slots>,
    formats: HashMap<String, String>,
    reads: Rc<RefCell<Vec<String>>>,
}

impl FakeImport {
    fn new() -> (Self, Rc<RefCell<Vec<String>>>) {
        let reads = Rc::new(RefCell::new(Vec::new()));
        (
            Self {
                entries: HashMap::new(),
                by_path: HashMap::new(),
                formats: HashMap::new(),
                reads: reads.clone(),
            },
            reads,
        )
    }

    fn with_provider(mut self, provider: &str, rows: Vec<(&str, &str, Option<&str>)>) -> Self {
        let mut entries = Vec::new();
        for (display, path, bg) in rows {
            entries.push((display.to_owned(), path.to_owned()));
            if let Some(bg) = bg {
                self.by_path.insert(path.to_owned(), slots_with(bg));
            }
        }
        self.entries.insert(provider.to_owned(), entries);
        self.formats
            .insert(provider.to_owned(), provider.to_owned());
        self
    }
}

impl ImportLibrary for FakeImport {
    fn entries(&self, provider: &str) -> Vec<(String, String)> {
        self.entries.get(provider).cloned().unwrap_or_default()
    }

    fn read(&self, _provider: &str, path: &str) -> Option<Slots> {
        self.reads.borrow_mut().push(path.to_owned());
        self.by_path.get(path).cloned()
    }

    fn format_of(&self, provider: &str) -> String {
        self.formats
            .get(provider)
            .cloned()
            .unwrap_or_else(|| provider.to_owned())
    }
}

fn open_state(lib: FakeImport, order: Vec<&str>) -> ImportState {
    let mut st = ImportState::new(
        order.into_iter().map(str::to_owned).collect(),
        Some(Box::new(lib)),
    );
    open_import(&mut st);
    st
}

fn ghostty_lib() -> FakeImport {
    FakeImport::new()
        .0
        .with_provider("ghostty", vec![("Alpha", "/a", None)])
}

// ---------------------------------------------------------------------------
// slugify
// ---------------------------------------------------------------------------

#[test]
fn runs_collapse_to_one_dash() {
    assert_eq!(slugify("Catppuccin Mocha"), "catppuccin-mocha");
}

#[test]
fn leading_dash_stripped() {
    assert_eq!(slugify("  --Foo"), "foo");
}

#[test]
fn pure_punctuation_is_empty() {
    assert_eq!(slugify("!!!"), "");
}

#[test]
fn stem_leaves_suffix_room() {
    assert_eq!(slugify(&"a".repeat(100)).len(), 64 - 2);
}

#[test]
fn batch_suffixes() {
    assert_eq!(
        batch_unique(&["ember".to_owned(), "ember".to_owned(), "ember".to_owned()]),
        vec![
            "ember".to_owned(),
            "ember-2".to_owned(),
            "ember-3".to_owned()
        ]
    );
}

#[test]
fn batch_suffix_stays_within_64() {
    let names = batch_unique(&["a".repeat(64), "a".repeat(64)]);
    assert_eq!(names, vec!["a".repeat(64), format!("{}-2", "a".repeat(62))]);
    for name in &names {
        assert!(name.len() <= 64, "{name}");
    }
}

#[test]
fn unique_slug_falls_back_to_theme_for_empty_base() {
    let mut taken = HashSet::new();
    assert_eq!(unique_slug("", &mut taken), "theme");
    assert_eq!(unique_slug("", &mut taken), "theme-2");
}

// ---------------------------------------------------------------------------
// open/close keys
// ---------------------------------------------------------------------------

#[test]
fn i_and_uppercase_i_open() {
    for key in ["i", "I"] {
        let mut st = ImportState::new(vec!["ghostty".to_owned()], Some(Box::new(ghostty_lib())));
        assert_eq!(handle_key(&mut st, key), "open");
        assert!(st.is_open);
    }
}

#[test]
fn other_keys_ignored_while_closed() {
    let mut st = ImportState::new(vec!["ghostty".to_owned()], Some(Box::new(ghostty_lib())));
    for key in ["h", "t", "enter", "space"] {
        assert_eq!(handle_key(&mut st, key), "ignored");
    }
    assert!(!st.is_open);
}

#[test]
fn close_spellings_abandon() {
    for key in ["esc", "escape", "i", "I", "\x03"] {
        let mut st = ImportState::new(vec!["ghostty".to_owned()], Some(Box::new(ghostty_lib())));
        open_import(&mut st);
        toggle(&mut st);
        assert_eq!(handle_key(&mut st, key), "close");
        assert!(!st.is_open);
        assert!(st.selected.is_empty());
    }
}

#[test]
fn abandon_is_close_without_confirm() {
    let mut st = ImportState::new(vec!["ghostty".to_owned()], Some(Box::new(ghostty_lib())));
    open_import(&mut st);
    toggle(&mut st);
    abandon(&mut st);
    assert!(!st.is_open);
    assert!(st.selected.is_empty());
}

#[test]
fn import_may_not_open_over_other_popups() {
    let mut st = EditorState::new(Slots::default(), Box::new(|_, _, _| Ok(WriteOutcome::ok())));
    assert!(import_may_open(&st));
    st.setup = Some(0);
    assert!(!import_may_open(&st));
    st.setup = None;
    st.overlay = Some(vec!["ash".to_owned()]);
    assert!(!import_may_open(&st));
    st.overlay = None;
    st.overrides = Some(huebox_editor::OverridesState {
        main: "/tmp/main".to_owned(),
        keys: vec!["background".to_owned()],
    });
    assert!(!import_may_open(&st));
}

// ---------------------------------------------------------------------------
// cursor and selection
// ---------------------------------------------------------------------------

fn two_provider_lib() -> FakeImport {
    FakeImport::new().0.with_provider(
        "ghostty",
        vec![
            ("Alpha", "/a", Some("#101010")),
            ("Beta", "/b", Some("#202020")),
        ],
    )
}

#[test]
fn cursor_walks_open_groups_and_clamps() {
    let mut st = open_state(two_provider_lib(), vec!["ghostty", "kitty"]);
    // Empty kitty pruned: only ghostty is a group.
    assert_eq!(st.provider_order, vec!["ghostty".to_owned()]);
    assert_eq!(cursor_id(&st).unwrap().1, "Alpha");
    move_down(&mut st);
    assert_eq!(cursor_id(&st).unwrap().1, "Beta");
    move_down(&mut st); // clamp at the end, no wrap
    assert_eq!(cursor_id(&st).unwrap().1, "Beta");
    move_up(&mut st);
    assert_eq!(cursor_id(&st).unwrap().1, "Alpha");
    move_up(&mut st);
    assert_eq!(cursor_id(&st).unwrap().1, "Alpha");
}

#[test]
fn wheel_moves_the_highlight() {
    let mut st = open_state(two_provider_lib(), vec!["ghostty"]);
    wheel(&mut st, 1);
    assert_eq!(cursor_id(&st).unwrap().1, "Beta");
    wheel(&mut st, -1);
    assert_eq!(cursor_id(&st).unwrap().1, "Alpha");
    wheel(&mut st, 0);
    assert_eq!(cursor_id(&st).unwrap().1, "Alpha");
}

#[test]
fn space_toggles_the_row_under_the_cursor() {
    let mut st = open_state(two_provider_lib(), vec!["ghostty"]);
    assert_eq!(handle_key(&mut st, "space"), "toggled");
    assert_eq!(st.selected.len(), 1);
    assert_eq!(handle_key(&mut st, " "), "toggled");
    assert!(st.selected.is_empty());
}

#[test]
fn collapse_and_expand_keep_selections() {
    let mut st = open_state(two_provider_lib(), vec!["ghostty"]);
    toggle(&mut st);
    let id = cursor_id(&st).unwrap();
    collapse_cursor(&mut st);
    assert!(!st.expanded.contains("ghostty"));
    assert!(st.selected.contains(&id)); // hiding is not deselecting
    expand_cursor(&mut st);
    assert!(st.expanded.contains("ghostty"));
}

#[test]
fn select_all_takes_open_groups_only_then_n_clears() {
    let (lib, _) = FakeImport::new();
    let mut lib = lib;
    lib.entries.insert(
        "ghostty".to_owned(),
        vec![
            ("Alpha".to_owned(), "/a".to_owned()),
            ("Beta".to_owned(), "/b".to_owned()),
        ],
    );
    lib.entries.insert(
        "kitty".to_owned(),
        vec![("Gamma".to_owned(), "/g".to_owned())],
    );
    lib.formats
        .insert("ghostty".to_owned(), "ghostty".to_owned());
    lib.formats.insert("kitty".to_owned(), "kitty".to_owned());
    let mut st = open_state(lib, vec!["ghostty", "kitty"]);
    toggle_group(&mut st, "kitty"); // both open now
    collapse_cursor(&mut st); // ghostty shut; cursor re-homes to kitty
    assert!(!st.expanded.contains("ghostty"));
    select_all_visible(&mut st);
    assert_eq!(st.selected.len(), 1); // only kitty's visible row
    assert!(st
        .selected
        .contains(&("kitty".to_owned(), "Gamma".to_owned())));
    toggle_group(&mut st, "ghostty"); // reopen ghostty (selection kept)
    select_all_visible(&mut st);
    assert_eq!(st.selected.len(), 3);
    clear_selection(&mut st);
    assert!(st.selected.is_empty());
}

#[test]
fn click_row_moves_and_toggles_click_group_collapses() {
    let mut st = open_state(two_provider_lib(), vec!["ghostty"]);
    click_row(&mut st, "ghostty", 1);
    assert_eq!(cursor_id(&st).unwrap().1, "Beta");
    assert_eq!(st.selected.len(), 1);
    click_group(&mut st, "ghostty");
    assert!(!st.expanded.contains("ghostty"));
    click_group(&mut st, "ghostty");
    assert!(st.expanded.contains("ghostty"));
    click_row(&mut st, "bogus", 0); // unknown provider ignored
    assert_eq!(st.selected.len(), 1);
}

#[test]
fn preview_reads_once_then_hits_the_cache() {
    let (lib, reads) = FakeImport::new();
    let lib = lib.with_provider("ghostty", vec![("Alpha", "/a", Some("#101010"))]);
    let mut st = open_state(lib, vec!["ghostty"]);
    let id = cursor_id(&st).unwrap();
    let first = preview_slots(&mut st, &id);
    assert_eq!(first.get("background"), "#101010");
    let second = preview_slots(&mut st, &id);
    assert_eq!(second, first);
    assert_eq!(reads.borrow().len(), 1); // read-once
    assert_eq!(cursor_slots(&mut st).get("background"), "#101010");
}

#[test]
fn unreadable_previews_empty_and_reads_once() {
    let (lib, reads) = FakeImport::new();
    let lib = lib.with_provider("ghostty", vec![("Bad", "/bad", None)]);
    let mut st = open_state(lib, vec!["ghostty"]);
    let id = cursor_id(&st).unwrap();
    assert_eq!(preview_slots(&mut st, &id), Slots::default());
    assert_eq!(preview_slots(&mut st, &id), Slots::default());
    assert_eq!(reads.borrow().len(), 1);
}

// ---------------------------------------------------------------------------
// confirm mapping
// ---------------------------------------------------------------------------

fn confirm_lib() -> FakeImport {
    FakeImport::new().0.with_provider(
        "ghostty",
        vec![
            (
                "Catppuccin Mocha",
                "/ship/Catppuccin Mocha",
                Some("#101010"),
            ),
            ("Gruvbox", "/user/Gruvbox", Some("#202020")),
        ],
    )
}

#[test]
fn toggled_set_maps_to_names_and_sources() {
    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st);
    move_down(&mut st);
    toggle(&mut st);
    let (plans, skips, failures) = plan_confirm(&mut st, &[]);
    assert!(failures.is_empty());
    assert!(skips.is_empty());
    assert_eq!(
        plans
            .iter()
            .map(|p| (p.name.clone(), p.source.clone()))
            .collect::<Vec<_>>(),
        vec![
            (
                "catppuccin-mocha".to_owned(),
                "ghostty:/ship/Catppuccin Mocha".to_owned()
            ),
            ("gruvbox".to_owned(), "ghostty:/user/Gruvbox".to_owned()),
        ]
    );
    assert_eq!(plans[0].slots.get("background"), "#101010");
}

#[test]
fn library_clash_skips_case_insensitively() {
    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st);
    move_down(&mut st);
    toggle(&mut st);
    clear_selection(&mut st);
    toggle(&mut st); // cursor still on Beta: Gruvbox only
    let (plans, skips, failures) = plan_confirm(&mut st, &["other"]);
    assert_eq!(
        plans.iter().map(|p| p.name.clone()).collect::<Vec<_>>(),
        vec!["gruvbox".to_owned()]
    );
    assert!(skips.is_empty());
    assert!(failures.is_empty());
    let (plans, skips, failures) = plan_confirm(&mut st, &["GRUVBOX"]);
    assert!(plans.is_empty());
    assert_eq!(skips, vec!["already in library: gruvbox".to_owned()]);
    assert!(failures.is_empty());
}

#[test]
fn unreadable_source_is_a_note_not_an_abort() {
    let lib = FakeImport::new().0.with_provider(
        "ghostty",
        vec![
            ("Good", "/user/Good", Some("#101010")),
            ("Bad", "/user/Bad", None),
        ],
    );
    let mut st = open_state(lib, vec!["ghostty"]);
    toggle(&mut st);
    move_down(&mut st);
    toggle(&mut st);
    let (plans, skips, failures) = plan_confirm(&mut st, &[]);
    assert_eq!(
        plans.iter().map(|p| p.name.clone()).collect::<Vec<_>>(),
        vec!["good".to_owned()]
    );
    assert!(skips.is_empty());
    assert_eq!(failures.len(), 1);
    assert!(failures[0].contains("Bad"));
}

#[test]
fn invalid_slug_is_a_note_never_a_fallback_name() {
    let lib = FakeImport::new()
        .0
        .with_provider("ghostty", vec![("!!!", "/user/Bang", Some("#101010"))]);
    let mut st = open_state(lib, vec!["ghostty"]);
    toggle(&mut st);
    let (plans, skips, failures) = plan_confirm(&mut st, &[]);
    assert!(plans.is_empty());
    assert!(skips.is_empty());
    assert_eq!(failures.len(), 1);
    assert!(failures[0].contains("!!!")); // reported, never renamed to theme-2
}

#[test]
fn empty_selection_stays_open_with_note() {
    let (lib, _) = FakeImport::new();
    let mut st = open_state(lib, vec!["ghostty"]);
    let (plans, skips, failures) = plan_confirm(&mut st, &[]);
    assert!(plans.is_empty() && skips.is_empty() && failures.is_empty());
    assert_eq!(st.note, "nothing selected");
}

#[test]
fn empty_providers_never_appear_as_groups() {
    let lib = FakeImport::new()
        .0
        .with_provider("ghostty", vec![("Ember", "/ship/Ember", Some("#101010"))]);
    let mut st = open_state(lib, vec!["ghostty", "kitty", "alacritty"]);
    assert_eq!(st.provider_order, vec!["ghostty".to_owned()]);
    assert!(st.expanded.contains("ghostty"));
    assert_eq!(
        visible_rows(&st)
            .iter()
            .map(|(p, _, _)| p.clone())
            .collect::<Vec<_>>(),
        vec!["ghostty".to_owned()]
    );
    toggle_group(&mut st, "kitty"); // unknown-as-group: ignored
    assert_eq!(st.provider_order, vec!["ghostty".to_owned()]);
    assert!(st.expanded.contains("ghostty"));
}

#[test]
fn all_empty_leaves_no_groups_and_no_cursor() {
    let (lib, _) = FakeImport::new();
    let mut st = open_state(lib, vec!["ghostty", "kitty"]);
    assert!(st.provider_order.is_empty());
    assert!(st.expanded.is_empty());
    assert!(visible_rows(&st).is_empty());
    assert!(cursor_id(&st).is_none());
    assert_eq!(cursor_slots(&mut st), Slots::default());
}

#[test]
fn mapping_writes_nothing_and_keeps_the_session() {
    let lib = FakeImport::new()
        .0
        .with_provider("ghostty", vec![("Ember", "/ship/Ember", Some("#101010"))]);
    let mut st = open_state(lib, vec!["ghostty"]);
    toggle(&mut st);
    let (selected, cursor, note) = (st.selected.clone(), st.cursor.clone(), st.note.clone());
    let (plans, _skips, _failures) = plan_confirm(&mut st, &[]);
    assert_eq!(plans.len(), 1);
    assert_eq!((st.selected, st.cursor, st.note), (selected, cursor, note));
}

#[test]
fn confirm_writes_each_plan_and_folds_the_status() {
    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st);
    move_down(&mut st);
    toggle(&mut st);
    let written: Rc<RefCell<Vec<(String, String)>>> = Rc::new(RefCell::new(Vec::new()));
    let w = written.clone();
    let mut writer = Box::new(move |name: &str, _slots: &Slots, source: &str| {
        w.borrow_mut().push((name.to_owned(), source.to_owned()));
        Ok(())
    });
    let outcome = confirm_import(&mut st, &[], &mut writer);
    assert_eq!(
        outcome.imported,
        vec!["catppuccin-mocha".to_owned(), "gruvbox".to_owned()]
    );
    assert!(
        outcome.status.starts_with("imported 2 themes: "),
        "{0}",
        outcome.status
    );
    assert!(outcome.notes.is_empty());
    assert_eq!(written.borrow().len(), 2);
}

#[test]
fn confirm_folds_skips_and_problems() {
    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st); // Catppuccin only
    let mut writer = Box::new(|_: &str, _: &Slots, _: &str| Err("already exists".to_owned()));
    let outcome = confirm_import(&mut st, &[], &mut writer);
    assert!(outcome.imported.is_empty());
    assert_eq!(outcome.status, "nothing imported - see session notes");
    assert_eq!(
        outcome.notes,
        vec!["catppuccin-mocha: already exists".to_owned()]
    );

    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st);
    let mut writer = Box::new(|_: &str, _: &Slots, _: &str| Ok(()));
    let outcome = confirm_import(&mut st, &["catppuccin-mocha"], &mut writer);
    assert!(outcome.imported.is_empty());
    assert_eq!(outcome.status, "already in library: catppuccin-mocha");
}

#[test]
fn close_clears_selection_note_and_cache() {
    let mut st = open_state(confirm_lib(), vec!["ghostty"]);
    toggle(&mut st);
    let id = cursor_id(&st).unwrap();
    let _ = preview_slots(&mut st, &id);
    st.note = "something".to_owned();
    close_import(&mut st);
    assert!(!st.is_open);
    assert!(st.selected.is_empty());
    assert!(st.note.is_empty());
    assert!(st.slots_cache.is_empty());
}
