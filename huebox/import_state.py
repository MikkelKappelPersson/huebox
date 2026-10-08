"""Headless import state: cursor, selection, confirm mapping, no compositor.

Spec `docs/003-import-menu/spec.md` §§4.3–4.4+7–9, plan phase 3: import
behaviour testable without Textual — the `EditorState` + `apply_key`
split, repeated. This module owns ALL import-popup behaviour (provider
order, expanded set, cursor over open groups, toggled set, footer note,
read-once slots cache, confirm mapping); `app.py` (phase 4) is a thin
shell over it plus the shared preview units, the same split `editor.py`
keeps with the frame.

Candidate A (research §4 verdict, followed without re-litigating): one
`SelectionList` per `Collapsible`, single focused list, cursor moves set
`highlighted` + focus together; the cursor below walks up/down through
every OPEN group as one continuous list, collapsed groups skipped never
deselected; `Enter` confirms via priority screen binding (toggle is
`space` only); wheel moves highlight ±1; `SelectionHighlighted` has no
`index` attr (phase 4 reads `list.highlighted`).

Dependency rule: stdlib + `providers` only — never `themes`, `detect`,
`editor` or `app`. Listing/reading arrive injected through
`ImportLibrary` (built by the `cli` composition root in phase 5),
mirroring the picker's `Library` in `editor.py`. No IO here at all: the
reader is a callable, and there is no `open()` in this module.
"""

from __future__ import annotations

from typing import NamedTuple

from .providers import batch_unique, slugify

__all__ = [
    "ImportLibrary",
    "ImportPlan",
    "ImportState",
    "abandon",
    "clear_selection",
    "click_group",
    "click_row",
    "close_import",
    "collapse_cursor",
    "cursor_id",
    "cursor_slots",
    "expand_cursor",
    "handle_key",
    "move_down",
    "move_up",
    "open_import",
    "plan_confirm",
    "preview_slots",
    "select_all_visible",
    "toggle",
    "toggle_group",
    "visible_rows",
    "wheel",
]

#: `Enter` spellings `handle_key` confirms on (spec §4.6).
ENTER_KEYS = ("enter", "\r", "\n")

#: Close spellings: `Esc` / `I` / `Ctrl+C` all abandon, writing nothing.
CLOSE_KEYS = ("esc", "escape", "I", "\x03")


class ImportLibrary:
    """The two things the import state may ask of provider themes.

    Injected by cli and never imported: this module must not know where a
    provider's files live, and a test can hand it two lambdas. Mirrors the
    picker's `Library` (`editor.py`): `listing(provider)` is
    `[(display_name, path)]` in list order, `reader(provider, path)` is
    the slots dict (`{}` when the file holds no colours). Failure is
    reported by return value, never by an exception into the state — a
    listing that raises reads as the empty group, a read that raises as no
    colours (the providers contract: missing/unreadable is an empty group,
    never an exception). `formats` maps provider to format for the
    `source` (`fmt:path`) the confirm plan records; a provider missing
    from it sources under its own name.
    """

    def __init__(self, listing=None, reader=None, formats=None):
        self.listing = listing        # (provider) -> [(display, path)]
        self.reader = reader          # (provider, path) -> slots | {}
        self.formats = dict(formats or {})

    def entries(self, provider: str) -> list:
        if not self.listing:
            return []
        try:
            return list(self.listing(provider))
        except Exception:             # a listing that died mid-session
            return []

    def read(self, provider: str, path: str) -> dict:
        if not self.reader:
            return {}
        try:
            return self.reader(provider, path) or {}
        except Exception:
            return {}

    def format_of(self, provider: str) -> str:
        return self.formats.get(provider, provider)


class ImportState:
    """Everything one import-popup session mutates (spec §§4.3–4.4, §9).

    `provider_order` is the group order; `theme_ids` maps provider to its
    row ids in list order, where an id is the `(provider, display)` tuple
    (unique per provider, hashable for the toggled set); `display` /
    `paths` keep the raw display name and file path alongside each id.
    `expanded` is the open groups (at least one on a non-empty order —
    the first provider with themes, else the first provider). `cursor`
    is `(provider, index)` over OPEN groups only. `selected` is the
    toggled id set — hiding is not deselecting: collapsed groups keep
    their selections. `note` is the footer note. `slots_cache` is the
    read-once preview cache (see `preview_slots`). `is_open` is the
    interlock phase 4/5 read (see `handle_key`). `library` is the injected
    seam; `None` lists nothing, so a bare state is an empty popup.
    """

    def __init__(self, provider_order=None, library=None):
        self.provider_order = list(provider_order or [])
        self.library = library              # ImportLibrary | None
        self.theme_ids: dict = {}           # provider -> [id], list order
        self.display: dict = {}             # id -> raw display name
        self.paths: dict = {}               # id -> file path
        self.expanded: set = set()          # providers whose list is open
        self.cursor = ("", 0)               # (provider, index), open only
        self.selected: set = set()          # toggled ids
        self.note = ""                      # footer note
        self.slots_cache: dict = {}         # id -> slots, read-once
        self.is_open = False                # the interlock
        self._load()
        self._seed()

    def _load(self) -> None:
        """Fill `theme_ids` / `display` / `paths` from the injected list."""
        library = self.library
        for provider in self.provider_order:
            ids = []
            seen = set()
            entries = library.entries(provider) if library else []
            for display, path in entries or []:
                if display in seen:
                    continue                # same name twice: first wins
                seen.add(display)
                theme_id = (provider, display)
                ids.append(theme_id)
                self.display[theme_id] = display
                self.paths[theme_id] = path
            self.theme_ids[provider] = ids

    def _seed(self) -> None:
        """Expand one provider and park the cursor on the first open row."""
        if not self.provider_order:
            return
        first = next((provider for provider in self.provider_order
                      if self.theme_ids.get(provider)),
                     self.provider_order[0])
        self.expanded.add(first)
        rows = visible_rows(self)
        if rows:
            self.cursor = (rows[0][0], rows[0][1])
        else:
            self.cursor = (self.provider_order[0], 0)


def visible_rows(st) -> list:
    """One continuous list over every OPEN group, in provider order.

    Each row is `(provider, index, id)`. Collapsed groups contribute
    nothing — skipped, never deselected — and empty groups contribute
    nothing either (there is no row to stand on). This is what the cursor
    walks and what `a` selects.
    """
    rows = []
    for provider in st.provider_order:
        if provider not in st.expanded:
            continue
        for index, theme_id in enumerate(st.theme_ids.get(provider, [])):
            rows.append((provider, index, theme_id))
    return rows


def cursor_id(st):
    """The id under the cursor, or `None` when it stands nowhere.

    `None` means the cursor's group is closed (or empty): there is no row
    to toggle and no theme to preview, so callers hold still.
    """
    provider, index = st.cursor
    ids = st.theme_ids.get(provider, [])
    if provider not in st.expanded or not 0 <= index < len(ids):
        return None
    return ids[index]


def _place(st, flat: int) -> None:
    """Park the cursor on the `flat`-th visible row (clamped)."""
    rows = visible_rows(st)
    if not rows:
        return
    flat = min(max(flat, 0), len(rows) - 1)
    st.cursor = (rows[flat][0], rows[flat][1])


def _step(st, delta: int) -> None:
    """Walk `delta` visible rows; clamps at both ends (no wrap).

    Clamp-at-ends is the documented choice: the list has a first and a
    last row the footer count already describes, so running past either
    end holds still instead of teleporting the preview somewhere
    unrelated. A cursor standing nowhere (closed/empty group) jumps to
    the nearer end in the direction of travel.
    """
    rows = visible_rows(st)
    if not rows:
        return
    here = cursor_id(st)
    if here is None:
        _place(st, 0 if delta > 0 else len(rows) - 1)
        return
    pos = next(index for index, (_, _, rid) in enumerate(rows)
               if rid == here)
    _place(st, pos + delta)


def move_up(st) -> None:
    """One theme up, across open groups (collapsed skipped)."""
    _step(st, -1)


def move_down(st) -> None:
    """One theme down, across open groups (collapsed skipped)."""
    _step(st, +1)


def wheel(st, delta: int) -> None:
    """A wheel tick over the list: the highlight follows, ±1 per detent.

    `delta > 0` steps down, `< 0` steps up (one row per unit of `delta`,
    clamped at the ends like the arrow keys); `0` holds still. Native
    viewport scroll never moves the highlight (research §4), so phase 4
    routes its scroll events here with the tick stopped.
    """
    if not delta:
        return
    for _ in range(abs(delta)):
        _step(st, 1 if delta > 0 else -1)


def _collapse(st, provider: str) -> None:
    """Close `provider`, re-homing a cursor left inside it.

    The closed group's selections stay selected; only the cursor moves —
    to the row that took the closed group's place (the next open group's
    first row at the same flat position), else the last row above it.
    """
    if provider not in st.expanded:
        return
    rows = visible_rows(st)
    here = cursor_id(st)
    if here is not None and st.cursor[0] == provider:
        flat = next(pos for pos, (_, _, rid) in enumerate(rows)
                    if rid == here)
    else:
        positions = [pos for pos, (prov, _, _) in enumerate(rows)
                     if prov == provider]
        flat = positions[0] if positions else 0
    st.expanded.discard(provider)
    _place(st, flat)


def collapse_cursor(st) -> None:
    """`left`: collapse the group under the cursor (selections kept)."""
    _collapse(st, st.cursor[0])


def expand_cursor(st) -> None:
    """`right`: expand the group under the cursor, cursor unmoved.

    Expanding never steals the cursor: it stays on its row (clamped into
    the group if the group changed shape underneath it). On an already
    open group this is a no-op.
    """
    provider, index = st.cursor
    if provider not in st.provider_order or provider in st.expanded:
        return
    st.expanded.add(provider)
    ids = st.theme_ids.get(provider, [])
    st.cursor = (provider, min(index, max(len(ids) - 1, 0)))


def toggle_group(st, provider: str) -> None:
    """A group-title click: collapse an open group, expand a closed one.

    Unknown providers are ignored. Collapsing re-homes a cursor left
    inside (see `_collapse`); expanding never moves the cursor.
    """
    if provider not in st.provider_order:
        return
    if provider in st.expanded:
        _collapse(st, provider)
    else:
        st.expanded.add(provider)


def toggle(st) -> None:
    """`space`: toggle the row under the cursor; nowhere, nothing."""
    theme_id = cursor_id(st)
    if theme_id is None:
        return
    if theme_id in st.selected:
        st.selected.discard(theme_id)
    else:
        st.selected.add(theme_id)


def select_all_visible(st) -> None:
    """`a`: select every row in every OPEN group (spec §8.3, decision 9).

    What you see is what `Enter` takes: collapsed groups keep exactly the
    selections they had — hidden rows are neither added nor dropped.
    """
    for _, _, theme_id in visible_rows(st):
        st.selected.add(theme_id)


def clear_selection(st) -> None:
    """`n`: clear the whole toggled set, open groups and hidden alike."""
    st.selected.clear()


def click_row(st, provider: str, index: int) -> None:
    """Click a theme row: move the cursor there, then toggle it.

    One call, not a second implementation — keyboard `space` and the mouse
    share `toggle`, mirroring the picker's click-is-Enter rule. A click on
    a collapsed group's title is `click_group`, never this: closed rows
    are not on screen. Defensively, a click naming a closed group opens it
    first, then moves and toggles; unknown providers and empty groups are
    ignored, and an out-of-range index clamps to the nearest row first.
    """
    ids = st.theme_ids.get(provider, [])
    if provider not in st.provider_order or not ids:
        return
    if provider not in st.expanded:
        st.expanded.add(provider)
    st.cursor = (provider, min(max(index, 0), len(ids) - 1))
    toggle(st)


def click_group(st, provider: str) -> None:
    """Click a group title: the mouse spelling of collapse/expand."""
    toggle_group(st, provider)


def preview_slots(st, theme_id) -> dict:
    """Slots for the preview: cache hit, else one injected read, then cached.

    Read-once per popup session: the injected reader fires only on the
    first highlight of a theme; every repaint after that hits the cache —
    even a `{}` miss is cached, so an unreadable file reads once and
    previews empty rather than retrying every frame. Unknown ids read as
    `{}` without touching the reader.
    """
    if theme_id not in st.slots_cache:
        path = st.paths.get(theme_id)
        if path is None or st.library is None:
            return {}
        st.slots_cache[theme_id] = st.library.read(theme_id[0], path)
    return st.slots_cache[theme_id]


def cursor_slots(st) -> dict:
    """The highlighted theme's slots for the preview repaint.

    `{}` when the cursor stands nowhere — phase 4 paints the empty
    preview rather than the last theme's.
    """
    theme_id = cursor_id(st)
    return preview_slots(st, theme_id) if theme_id is not None else {}


def open_import(st) -> None:
    """`I` with the popup down: take the surface.

    Opening is never blocked (not by a dirty buffer — importing adds
    library files, it never retargets the session) and touches nothing
    but the interlock: the note clears and a cursor standing nowhere
    parks on the first open row.
    """
    st.is_open = True
    st.note = ""
    if cursor_id(st) is None:
        rows = visible_rows(st)
        if rows:
            st.cursor = (rows[0][0], rows[0][1])


def close_import(st) -> None:
    """Abandon the popup: shut it, writing nothing.

    The choice (spec §§4.2/4.6): the selection lives for the popup
    session and dies with it — closing discards the toggled set, the
    footer note and the slots cache without writing a byte, so a fresh
    open re-lists and re-reads. Cursor and expanded set survive on the
    state object (phase 5 rebuilds per open either way, so this is moot
    there and handy in tests).
    """
    st.is_open = False
    st.selected.clear()
    st.note = ""
    st.slots_cache.clear()


#: Backwards-name for `close_import`: `Esc` abandons, it never confirms.
abandon = close_import


def handle_key(st, key: str):
    """One keypress against the import popup (spec §4.6).

    Returns what happened — `"open"`, `"close"`, `"confirm"`, `"moved"`,
    `"toggled"`, `"collapsed"`, `"expanded"`, `"selected-all"`,
    `"cleared"` or `"ignored"` — so the phase-4 shell routes without
    re-implementing the surface. Mutates only through the transitions
    above; anything without a branch is `"ignored"` with the state
    untouched. In particular every editor colour key
    (`w/e/s/d/x/c/f/i/u/r/t/N/…`) is inert by construction — there is
    simply no branch for it — and so is the picker's `t` while the popup
    is up.

    Interlock (phase 4/5 enforce with `is_open`): route keys here only
    while the import popup owns the surface. While it is up, `t`/picker
    keys must not reach `apply_key`; while the picker is up, `I` must not
    reach this (the closed branch below answers only to `I`). `Enter`
    returns `"confirm"` and leaves the write to the caller
    (`plan_confirm`, then `themes.create` per plan in phase 5); with
    nothing selected the caller stays open on the `"nothing selected"`
    note `plan_confirm` sets.
    """
    if not st.is_open:
        if key == "I":
            open_import(st)
            return "open"
        return "ignored"
    if key in CLOSE_KEYS:
        close_import(st)
        return "close"
    if key in ENTER_KEYS:
        return "confirm"
    if key in ("up", "down"):
        move_up(st) if key == "up" else move_down(st)
        return "moved"
    if key == "left":
        collapse_cursor(st)
        return "collapsed"
    if key == "right":
        expand_cursor(st)
        return "expanded"
    if key in ("space", " "):
        toggle(st)
        return "toggled"
    if key == "a":
        select_all_visible(st)
        return "selected-all"
    if key == "n":
        clear_selection(st)
        return "cleared"
    return "ignored"


class ImportPlan(NamedTuple):
    """One confirmed import: plain data, nothing written.

    `theme_id` is the `(provider, display)` row it came from, `name` the
    slugified unique huebox name, `slots` its colours, `source` the
    `fmt:path` origin `themes.create` records (spec §7).
    """
    theme_id: tuple
    name: str
    slots: dict
    source: str


def plan_confirm(st, existing_lower=None) -> tuple:
    """Map the toggled set to `(plans, skips, failures)` (spec §7, §8.4).

    Steps, in order: toggled ids sorted by `(provider, name)` so batch
    `-2`/`-3` suffixes are stable run to run; each display name through
    `providers.slugify`; a `""` slug becomes a per-theme failure note
    here — `""` bases are filtered BEFORE `batch_unique`, never passed
    through it, so a bad name can never surface as `"theme"`/`"theme-2"`
    (the `unique_slug` fallback such bases would otherwise hit); the
    surviving bases through one `batch_unique`; each candidate compared
    case-insensitively against the library — a clash becomes a skip entry
    (`"already in library: X"`), never an overwrite, and there is no
    force in the popup; each remaining candidate's slots read from the
    preview cache (reader on first touch only) with `source` recorded as
    `fmt:path`; an unreadable file (no colours) becomes a per-theme
    failure note, never an abort — the readable themes still land.

    Returns plain data and writes nothing: phase 5 turns each plan into
    `themes.create(name, slots, source=...)`, folds skips into the status
    line and failures into the session notes. Empty selection returns
    `([], [], [])` with `st.note` set to `"nothing selected"` — the
    caller stays open, it neither errors nor closes.
    """
    existing = {str(name).lower() for name in (existing_lower or [])}
    if not st.selected:
        st.note = "nothing selected"
        return ([], [], [])
    ordered = sorted(st.selected,
                     key=lambda theme_id: (theme_id[0],
                                           st.display.get(theme_id,
                                                          "").lower(),
                                           st.display.get(theme_id, "")))
    good, bases, failures = [], [], []
    for theme_id in ordered:
        base = slugify(st.display.get(theme_id, ""))
        if not base:
            failures.append(f"{st.display.get(theme_id, theme_id)}: "
                            f"invalid theme name, skipped")
        else:
            good.append(theme_id)
            bases.append(base)
    plans, skips = [], []
    for theme_id, name in zip(good, batch_unique(bases)):
        if name.lower() in existing:
            skips.append(f"already in library: {name}")
            continue
        slots = preview_slots(st, theme_id)
        if not slots:
            failures.append(f"{st.display.get(theme_id, theme_id)}: "
                            f"no colours found, skipped")
            continue
        provider = theme_id[0]
        fmt = st.library.format_of(provider) if st.library else provider
        plans.append(ImportPlan(theme_id, name, dict(slots),
                                f"{fmt}:{st.paths.get(theme_id, '')}"))
    return (plans, skips, failures)
