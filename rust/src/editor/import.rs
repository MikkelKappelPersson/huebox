//! Headless import state: cursor, selection, confirm mapping.
//!
//! Rust port of `huebox/import_state.py`: the `EditorState` + `apply_key`
//! split, repeated. This module owns ALL import-popup behaviour (provider
//! order, expanded set, cursor over open groups, toggled set, footer note,
//! read-once slots cache, confirm mapping); the compositor (P5) is a thin
//! shell over it. No I/O here at all: listing/reading arrive injected
//! through [`ImportLibrary`](crate::editor::ImportLibrary), and there is no
//! `open()` in this module.
//!
//! Slug mapping (`slugify` / `batch_unique`) ports `providers.py` — the
//! editor never imports providers, so the dozen lines live here.

use std::collections::{HashMap, HashSet};

use crate::core::Slots;

use crate::editor::seams::ImportLibrary;
use crate::editor::state::EditorState;

/// `Enter` spellings [`handle_key`] confirms on.
pub const ENTER_KEYS: [&str; 3] = ["enter", "\r", "\n"];
/// Close spellings: `Esc` / `i` / Ctrl+C all abandon, writing nothing.
pub const CLOSE_KEYS: [&str; 5] = ["esc", "escape", "i", "I", "\x03"];
/// Accepted theme-name length (letters, digits, `-` and `_`, 64 max).
pub const MAX_NAME_LEN: usize = 64;
/// Chars a slugified stem leaves for a `-N` batch suffix (`-2` .. `-9`).
const SUFFIX_ROOM: usize = 2;

/// One list row: `(provider, display)` — unique per provider, hashable
/// for the toggled set.
pub type ThemeId = (String, String);

/// Everything one import-popup session mutates.
///
/// `provider_order` is the group order — providers with no themes are
/// pruned at construction, so an empty provider never appears as a group.
/// `expanded` is the open groups (the first provider, when any remain).
/// `cursor` is `(provider, index)` over OPEN groups only. `selected` is
/// the toggled id set — hiding is not deselecting: collapsed groups keep
/// their selections. `slots_cache` is the read-once preview cache.
/// `is_open` is the interlock P5 reads.
#[derive(Default)]
pub struct ImportState {
    /// Group order (empty providers pruned at construction).
    pub provider_order: Vec<String>,
    /// The injected seam; `None` lists nothing.
    pub library: Option<Box<dyn ImportLibrary>>,
    /// Provider to its row ids, in list order.
    pub theme_ids: HashMap<String, Vec<ThemeId>>,
    /// Raw display name per id.
    pub display: HashMap<ThemeId, String>,
    /// File path per id.
    pub paths: HashMap<ThemeId, String>,
    /// Providers whose list is open.
    pub expanded: HashSet<String>,
    /// `(provider, index)`, open groups only.
    pub cursor: (String, usize),
    /// Toggled ids.
    pub selected: HashSet<ThemeId>,
    /// Footer note.
    pub note: String,
    /// Read-once preview cache (misses cached too).
    pub slots_cache: HashMap<ThemeId, Option<Slots>>,
    /// The interlock: route keys here only while open.
    pub is_open: bool,
}

impl ImportState {
    /// A popup session over `provider_order` with its injected seam.
    pub fn new(provider_order: Vec<String>, library: Option<Box<dyn ImportLibrary>>) -> Self {
        let mut st = Self {
            provider_order,
            library,
            cursor: (String::new(), 0),
            ..Self::default()
        };
        st.load();
        // Empty providers never appear as groups: prune them so the
        // cursor, the expanded set and the view agree on what exists.
        st.provider_order = std::mem::take(&mut st.provider_order)
            .into_iter()
            .filter(|provider| {
                st.theme_ids
                    .get(provider)
                    .is_some_and(|ids| !ids.is_empty())
            })
            .collect();
        st.seed();
        st
    }

    /// Fill `theme_ids` / `display` / `paths` from the injected list.
    fn load(&mut self) {
        let order = self.provider_order.clone();
        for provider in &order {
            let mut ids = Vec::new();
            let mut seen = HashSet::new();
            let entries = self
                .library
                .as_ref()
                .map(|library| library.entries(provider))
                .unwrap_or_default();
            for (display, path) in entries {
                if !seen.insert(display.clone()) {
                    continue; // same name twice: first wins
                }
                let id = (provider.clone(), display.clone());
                ids.push(id.clone());
                self.display.insert(id.clone(), display);
                self.paths.insert(id, path);
            }
            self.theme_ids.insert(provider.clone(), ids);
        }
    }

    /// Expand the first provider and park the cursor on its first row.
    fn seed(&mut self) {
        let Some(first) = self.provider_order.first().cloned() else {
            return;
        };
        self.expanded.insert(first.clone());
        let rows = visible_rows(self);
        if let Some((provider, index, _)) = rows.first() {
            self.cursor = (provider.clone(), *index);
        } else {
            self.cursor = (first, 0);
        }
    }
}

impl std::fmt::Debug for ImportState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ImportState")
            .field("provider_order", &self.provider_order)
            .field("theme_ids", &self.theme_ids)
            .field("expanded", &self.expanded)
            .field("cursor", &self.cursor)
            .field("selected", &self.selected)
            .field("note", &self.note)
            .field("is_open", &self.is_open)
            .finish_non_exhaustive()
    }
}

/// Map a provider theme name to a huebox-name candidate.
///
/// Lowercase, runs of non-`[a-z0-9]` collapse to one `-`, leading `-`
/// stripped, stem cut to leave room for a `-N` suffix within 64 chars.
/// Pure: a clash with the library is the caller's to report, never
/// renamed here. Returns `""` when nothing survives; the caller reports
/// that as a bad name.
pub fn slugify(name: &str) -> String {
    let mut slug = String::new();
    let mut dash = false;
    for ch in name.to_lowercase().chars() {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch);
            dash = false;
        } else if !dash && !slug.is_empty() {
            // Runs collapse to one `-`; leading runs strip (nothing to
            // append to yet), so the result starts alphanumeric.
            slug.push('-');
            dash = true;
        }
    }
    // A trailing `-` is a collapsed trailing run; Python's `sub` leaves
    // it (`"Foo "` -> `"foo-"`), so keep it — only the length cuts.
    let _ = dash;
    slug.chars().take(MAX_NAME_LEN - SUFFIX_ROOM).collect()
}

/// First free slug for `base`: `base`, else `base-2`, `base-3`, ….
///
/// `taken_lower` holds the lowercased names this batch already claimed;
/// the candidate (lowercased) joins it, so a loop over the batch never
/// reuses a name. The stem is re-cut per suffix so every candidate stays
/// within 64 chars. An empty `base` falls back to `"theme"`.
pub fn unique_slug(base: &str, taken_lower: &mut HashSet<String>) -> String {
    let stem: String = (if base.is_empty() { "theme" } else { base })
        .chars()
        .take(MAX_NAME_LEN)
        .collect();
    let mut candidate = stem.clone();
    let mut number = 1;
    while taken_lower.contains(&candidate.to_lowercase()) {
        number += 1;
        let suffix = format!("-{number}");
        let keep = MAX_NAME_LEN - suffix.len();
        candidate = format!("{}{suffix}", stem.chars().take(keep).collect::<String>());
    }
    taken_lower.insert(candidate.to_lowercase());
    candidate
}

/// [`unique_slug`] over `bases` in order; returns the candidates.
pub fn batch_unique(bases: &[String]) -> Vec<String> {
    let mut taken = HashSet::new();
    bases
        .iter()
        .map(|base| unique_slug(base, &mut taken))
        .collect()
}

/// One continuous list over every OPEN group, in provider order.
///
/// Each row is `(provider, index, id)`. Collapsed groups contribute
/// nothing — skipped, never deselected. This is what the cursor walks and
/// what `a` selects.
pub fn visible_rows(st: &ImportState) -> Vec<(String, usize, ThemeId)> {
    let mut rows = Vec::new();
    for provider in &st.provider_order {
        if !st.expanded.contains(provider) {
            continue;
        }
        for (index, id) in st
            .theme_ids
            .get(provider)
            .cloned()
            .unwrap_or_default()
            .iter()
            .enumerate()
        {
            rows.push((provider.clone(), index, id.clone()));
        }
    }
    rows
}

/// The id under the cursor, or `None` when it stands nowhere.
///
/// `None` means the cursor's group is closed (or empty): no row to
/// toggle, no theme to preview — callers hold still.
pub fn cursor_id(st: &ImportState) -> Option<ThemeId> {
    let (provider, index) = &st.cursor;
    let ids = st.theme_ids.get(provider)?;
    if !st.expanded.contains(provider) || *index >= ids.len() {
        return None;
    }
    Some(ids[*index].clone())
}

/// Park the cursor on the `flat`-th visible row (clamped).
fn place(st: &mut ImportState, flat: usize) {
    let rows = visible_rows(st);
    if rows.is_empty() {
        return;
    }
    let flat = flat.min(rows.len() - 1);
    st.cursor = (rows[flat].0.clone(), rows[flat].1);
}

/// Walk `delta` visible rows; at either end the next group in order
/// opens and the cursor lands on its boundary row (first going down,
/// last going up) — a shut group stays shut only until the cursor
/// reaches for it. Past the last provider the cursor clamps (no wrap).
///
/// A cursor standing nowhere (closed/empty group) jumps to the nearer end
/// in the direction of travel.
fn step(st: &mut ImportState, delta: i32) {
    let rows = visible_rows(st);
    if rows.is_empty() {
        return;
    }
    let Some(here) = cursor_id(st) else {
        place(st, if delta > 0 { 0 } else { usize::MAX });
        return;
    };
    let pos = rows.iter().position(|(_, _, id)| *id == here).unwrap_or(0);
    if delta > 0 && pos + 1 >= rows.len() && !enter_next(st) {
        place(st, usize::MAX);
    } else if delta < 0 && pos == 0 && !enter_prev(st) {
        place(st, 0);
    } else {
        place(st, (pos as i32 + delta).max(0) as usize);
    }
}

/// Open the next provider in order and park on its first row.
///
/// Only fires at the end of the visible list, so the next provider is
/// always a shut one (an open one's rows would follow) — `false` means
/// there is no next provider and the cursor clamps.
fn enter_next(st: &mut ImportState) -> bool {
    let Some(at) = st
        .provider_order
        .iter()
        .position(|name| *name == st.cursor.0)
    else {
        return false;
    };
    let Some(next) = st.provider_order.get(at + 1).cloned() else {
        return false;
    };
    if st.theme_ids.get(&next).map(Vec::len).unwrap_or(0) == 0 {
        return false;
    }
    st.expanded.insert(next.clone());
    st.cursor = (next, 0);
    true
}

/// Open the previous provider in order and park on its last row.
///
/// Mirrors [`enter_next`] at the head of the visible list.
fn enter_prev(st: &mut ImportState) -> bool {
    let Some(at) = st
        .provider_order
        .iter()
        .position(|name| *name == st.cursor.0)
    else {
        return false;
    };
    let Some(prev) = at
        .checked_sub(1)
        .and_then(|i| st.provider_order.get(i))
        .cloned()
    else {
        return false;
    };
    let len = st.theme_ids.get(&prev).map(Vec::len).unwrap_or(0);
    if len == 0 {
        return false;
    }
    st.expanded.insert(prev.clone());
    st.cursor = (prev, len - 1);
    true
}

/// One theme up, across open groups (collapsed skipped).
pub fn move_up(st: &mut ImportState) {
    step(st, -1);
}

/// One theme down, across open groups (collapsed skipped).
pub fn move_down(st: &mut ImportState) {
    step(st, 1);
}

/// `home` / `pageup`: park the cursor on the first visible row.
///
/// The lists are exact-fit, so a native page always lands on an end.
pub fn move_first(st: &mut ImportState) {
    let rows = visible_rows(st);
    if let Some((provider, index, _)) = rows.first() {
        st.cursor = (provider.clone(), *index);
    }
}

/// `end` / `pagedown`: park the cursor on the last visible row.
///
/// The lists are exact-fit, so a native page always lands on an end.
pub fn move_last(st: &mut ImportState) {
    let rows = visible_rows(st);
    if let Some((provider, index, _)) = rows.last() {
        st.cursor = (provider.clone(), *index);
    }
}

/// A wheel tick over the list: the highlight follows, ±1 per detent.
///
/// `delta > 0` steps down, `< 0` steps up; `0` holds still.
pub fn wheel(st: &mut ImportState, delta: i32) {
    if delta == 0 {
        return;
    }
    for _ in 0..delta.unsigned_abs() {
        step(st, if delta > 0 { 1 } else { -1 });
    }
}

/// Close `provider`, re-homing a cursor left inside it.
///
/// The closed group's selections stay selected; only the cursor moves —
/// to the row that took the closed group's place, else the last row above.
fn collapse(st: &mut ImportState, provider: &str) {
    if !st.expanded.contains(provider) {
        return;
    }
    let rows = visible_rows(st);
    let flat = match cursor_id(st) {
        Some(here) if st.cursor.0 == provider => {
            rows.iter().position(|(_, _, id)| *id == here).unwrap_or(0)
        }
        _ => rows
            .iter()
            .position(|(prov, _, _)| prov == provider)
            .unwrap_or(0),
    };
    st.expanded.remove(provider);
    place(st, flat);
}

/// `left`: collapse the group under the cursor (selections kept).
pub fn collapse_cursor(st: &mut ImportState) {
    let provider = st.cursor.0.clone();
    collapse(st, &provider);
}

/// `right`: expand the group under the cursor, cursor unmoved.
///
/// Expanding never steals the cursor. On an already open group, a no-op.
pub fn expand_cursor(st: &mut ImportState) {
    let (provider, index) = st.cursor.clone();
    if !st.provider_order.contains(&provider) || st.expanded.contains(&provider) {
        return;
    }
    st.expanded.insert(provider.clone());
    let len = st.theme_ids.get(&provider).map(Vec::len).unwrap_or(0);
    st.cursor = (provider, index.min(len.saturating_sub(1)));
}

/// A group-title click: collapse an open group, expand a closed one.
///
/// Unknown providers are ignored. Collapsing re-homes a cursor left
/// inside; expanding never moves the cursor.
pub fn toggle_group(st: &mut ImportState, provider: &str) {
    if !st.provider_order.contains(&provider.to_owned()) {
        return;
    }
    if st.expanded.contains(provider) {
        collapse(st, provider);
    } else {
        st.expanded.insert(provider.to_owned());
    }
}

/// `space`: toggle the row under the cursor; nowhere, nothing.
pub fn toggle(st: &mut ImportState) {
    let Some(id) = cursor_id(st) else {
        return;
    };
    if !st.selected.remove(&id) {
        st.selected.insert(id);
    }
}

/// `a`: select every row in every OPEN group.
///
/// What you see is what `Enter` takes: collapsed groups keep exactly the
/// selections they had — hidden rows are neither added nor dropped.
pub fn select_all_visible(st: &mut ImportState) {
    for (_, _, id) in visible_rows(st) {
        st.selected.insert(id);
    }
}

/// `n`: clear the whole toggled set, open groups and hidden alike.
pub fn clear_selection(st: &mut ImportState) {
    st.selected.clear();
}

/// Click a theme row: move the cursor there, then toggle it.
///
/// A click on a closed group opens it first, then moves and toggles;
/// unknown providers and empty groups are ignored, an out-of-range index
/// clamps to the nearest row first.
pub fn click_row(st: &mut ImportState, provider: &str, index: usize) {
    let len = st.theme_ids.get(provider).map(Vec::len).unwrap_or(0);
    if !st.provider_order.contains(&provider.to_owned()) || len == 0 {
        return;
    }
    st.expanded.insert(provider.to_owned());
    st.cursor = (provider.to_owned(), index.min(len - 1));
    toggle(st);
}

/// Click a group title: the mouse spelling of collapse/expand.
pub fn click_group(st: &mut ImportState, provider: &str) {
    toggle_group(st, provider);
}

/// Slots for the preview: cache hit, else one injected read, then cached.
///
/// Read-once per popup session: the injected reader fires only on the
/// first highlight of a theme; every repaint after that hits the cache —
/// even a miss is cached, so an unreadable file reads once and previews
/// empty rather than retrying every frame. Unknown ids read as empty
/// without touching the reader (and are not cached).
pub fn preview_slots(st: &mut ImportState, id: &ThemeId) -> Slots {
    if let Some(cached) = st.slots_cache.get(id) {
        return cached.clone().unwrap_or_default();
    }
    let path = st.paths.get(id).cloned();
    let read = match (&st.library, path) {
        (Some(library), Some(path)) => library.read(&id.0, &path),
        _ => return Slots::default(),
    };
    st.slots_cache.insert(id.clone(), read.clone());
    read.unwrap_or_default()
}

/// The highlighted theme's slots for the preview repaint.
///
/// Empty when the cursor stands nowhere — the shell paints the empty
/// preview rather than the last theme's.
pub fn cursor_slots(st: &mut ImportState) -> Slots {
    match cursor_id(st) {
        Some(id) => preview_slots(st, &id),
        None => Slots::default(),
    }
}

/// `i` (or `I`) with the popup down: take the surface.
///
/// Opening is never blocked (not by a dirty buffer — importing adds
/// library files, it never retargets the session) and touches nothing but
/// the interlock: the note clears and a cursor standing nowhere parks on
/// the first open row.
pub fn open_import(st: &mut ImportState) {
    st.is_open = true;
    st.note.clear();
    if cursor_id(st).is_none() {
        let rows = visible_rows(st);
        if let Some((provider, index, _)) = rows.first() {
            st.cursor = (provider.clone(), *index);
        }
    }
}

/// Abandon the popup: shut it, writing nothing.
///
/// The selection lives for the popup session and dies with it — closing
/// discards the toggled set, the footer note and the slots cache without
/// writing a byte, so a fresh open re-lists and re-reads. Cursor and
/// expanded set survive on the state object.
pub fn close_import(st: &mut ImportState) {
    st.is_open = false;
    st.selected.clear();
    st.note.clear();
    st.slots_cache.clear();
}

/// `Esc` abandons, it never confirms.
pub fn abandon(st: &mut ImportState) {
    close_import(st);
}

/// Whether the import popup may open over this editor state.
///
/// Popups never stack: the `no theme detected` choice, the first-run
/// choice, the picker and the overrides warning each own the surface while
/// up, and a second `i` while any of them is up is a no-op. The shell also
/// checks the import is not already open (`is_open`); opening is otherwise
/// never blocked, not even by a dirty buffer.
pub fn import_may_open(st: &EditorState) -> bool {
    st.no_theme.is_none() && st.setup.is_none() && st.overlay.is_none() && st.overrides.is_none()
}

/// One keypress against the import popup.
///
/// Returns what happened — `"open"`, `"close"`, `"confirm"`, `"moved"`,
/// `"toggled"`, `"collapsed"`, `"expanded"`, `"selected-all"`,
/// `"cleared"` or `"ignored"` — so the shell routes without
/// re-implementing the surface. Anything without a branch is `"ignored"`
/// with the state untouched: every editor colour key is inert by
/// construction, and so is the picker's `t` while the popup is up.
///
/// `Enter` returns `"confirm"` and leaves the write to the caller
/// ([`plan_confirm`], then the writer per plan); with nothing selected the
/// caller stays open on the `"nothing selected"` note.
pub fn handle_key(st: &mut ImportState, key: &str) -> &'static str {
    if !st.is_open {
        if key == "i" || key == "I" {
            open_import(st);
            return "open";
        }
        return "ignored";
    }
    if CLOSE_KEYS.contains(&key) {
        close_import(st);
        return "close";
    }
    if ENTER_KEYS.contains(&key) {
        return "confirm";
    }
    if key == "up" || key == "down" {
        if key == "up" {
            move_up(st);
        } else {
            move_down(st);
        }
        return "moved";
    }
    if key == "home" || key == "pageup" {
        move_first(st);
        return "moved";
    }
    if key == "end" || key == "pagedown" {
        move_last(st);
        return "moved";
    }
    if key == "left" {
        collapse_cursor(st);
        return "collapsed";
    }
    if key == "right" {
        expand_cursor(st);
        return "expanded";
    }
    if key == "space" || key == " " {
        toggle(st);
        return "toggled";
    }
    if key == "a" {
        select_all_visible(st);
        return "selected-all";
    }
    if key == "n" {
        clear_selection(st);
        return "cleared";
    }
    "ignored"
}

/// One confirmed import: plain data, nothing written.
///
/// `theme_id` is the `(provider, display)` row it came from, `name` the
/// slugified unique huebox name, `slots` its colours, `source` the
/// `fmt:path` origin the writer records.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImportPlan {
    /// The `(provider, display)` row the plan came from.
    pub theme_id: ThemeId,
    /// Slugified unique huebox name.
    pub name: String,
    /// The theme's colours.
    pub slots: Slots,
    /// `fmt:path` origin the writer records.
    pub source: String,
}

/// Map the toggled set to `(plans, skips, failures)`.
///
/// Steps, in order: toggled ids sorted by `(provider, name)` so batch
/// `-2`/`-3` suffixes are stable run to run; each display name through
/// [`slugify`]; a `""` slug becomes a per-theme failure note here — `""`
/// bases are filtered BEFORE [`batch_unique`], never passed through it, so
/// a bad name can never surface as `"theme"`/`"theme-2"`; the surviving
/// bases through one [`batch_unique`]; each candidate compared
/// case-insensitively against the library — a clash becomes a skip entry
/// (`"already in library: X"`), never an overwrite, and there is no force
/// in the popup; each remaining candidate's slots read from the preview
/// cache (reader on first touch only) with `source` as `fmt:path`; an
/// unreadable file (no colours) becomes a per-theme failure note, never an
/// abort — the readable themes still land.
///
/// Returns plain data and writes nothing. Empty selection returns empties
/// with `st.note` set to `"nothing selected"` — the caller stays open, it
/// neither errors nor closes.
pub fn plan_confirm(
    st: &mut ImportState,
    existing_lower: &[&str],
) -> (Vec<ImportPlan>, Vec<String>, Vec<String>) {
    let existing: HashSet<String> = existing_lower
        .iter()
        .map(|name| name.to_lowercase())
        .collect();
    if st.selected.is_empty() {
        st.note = "nothing selected".to_owned();
        return (vec![], vec![], vec![]);
    }
    let mut ordered: Vec<ThemeId> = st.selected.iter().cloned().collect();
    ordered.sort_by(|a, b| {
        let da = st.display.get(a).map(String::as_str).unwrap_or("");
        let db = st.display.get(b).map(String::as_str).unwrap_or("");
        (a.0.clone(), da.to_lowercase(), da.to_owned()).cmp(&(
            b.0.clone(),
            db.to_lowercase(),
            db.to_owned(),
        ))
    });
    let mut good = Vec::new();
    let mut bases = Vec::new();
    let mut failures = Vec::new();
    for id in ordered {
        let display = st.display.get(&id).cloned().unwrap_or_default();
        let base = slugify(&display);
        if base.is_empty() {
            failures.push(format!("{display}: invalid theme name, skipped"));
        } else {
            good.push(id);
            bases.push(base);
        }
    }
    let mut plans = Vec::new();
    let mut skips = Vec::new();
    for (id, name) in good.into_iter().zip(batch_unique(&bases)) {
        if existing.contains(&name.to_lowercase()) {
            skips.push(format!("already in library: {name}"));
            continue;
        }
        let display = st.display.get(&id).cloned().unwrap_or_default();
        preview_slots(st, &id);
        let Some(slots) = st.slots_cache.get(&id).cloned().flatten() else {
            failures.push(format!("{display}: no colours found, skipped"));
            continue;
        };
        let fmt = st
            .library
            .as_ref()
            .map(|library| library.format_of(&id.0))
            .unwrap_or_else(|| id.0.clone());
        let path = st.paths.get(&id).cloned().unwrap_or_default();
        let _ = slots;
        plans.push(ImportPlan {
            theme_id: id,
            name,
            slots,
            source: format!("{fmt}:{path}"),
        });
    }
    (plans, skips, failures)
}

/// What confirming wrote: names that landed, the status line, the notes.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct ConfirmOutcome {
    /// Names that landed.
    pub imported: Vec<String>,
    /// Status line for the editor (`""` leaves it untouched).
    pub status: String,
    /// Per-theme failures plus writer problems, for the exit report.
    pub notes: Vec<String>,
}

/// Confirm the popup: each plan through the writer, folds like the shell.
///
/// Successes and skips fold into the status line (`imported N themes: …`
/// / `already in library: …`); per-theme failures plus writer problems
/// join the notes for the exit report. An empty confirm (nothing
/// selected) returns an empty status — the shell leaves the status line
/// untouched and stays open. Closing never touches the buffer:
/// no branch here assigns slots, sel, theme or path.
pub fn confirm_import(
    st: &mut ImportState,
    existing_lower: &[&str],
    writer: &mut dyn FnMut(&str, &Slots, &str) -> Result<(), String>,
) -> ConfirmOutcome {
    let (plans, skips, failures) = plan_confirm(st, existing_lower);
    let mut imported = Vec::new();
    let mut problems = Vec::new();
    for plan in &plans {
        match writer(&plan.name, &plan.slots, &plan.source) {
            Ok(()) => imported.push(plan.name.clone()),
            Err(problem) => problems.push(format!("{}: {problem}", plan.name)),
        }
    }
    let mut notes = failures;
    notes.extend(problems);
    let status = if !imported.is_empty() {
        let plural = if imported.len() == 1 { "" } else { "s" };
        let mut parts = vec![format!(
            "imported {} theme{plural}: {}",
            imported.len(),
            imported.join(", ")
        )];
        parts.extend(skips);
        parts.join("; ")
    } else if !skips.is_empty() {
        if skips.len() == 1 {
            skips[0].clone()
        } else {
            format!("{} themes already in library", skips.len())
        }
    } else if !notes.is_empty() {
        "nothing imported - see session notes".to_owned()
    } else {
        String::new()
    };
    ConfirmOutcome {
        imported,
        status,
        notes,
    }
}
