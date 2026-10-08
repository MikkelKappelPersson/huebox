# huebox 003 — Textual research

Checked 2026-10-08 against <https://textual.textualize.io>. Widget gallery:
<https://textual.textualize.io/widget_gallery/>. This note backs
`plan.md` phases 0 and 4; it ships no product code.

## 1. Widget APIs used

### 1.1 `SelectionList` — the multi-toggle list

Page: <https://textual.textualize.io/widgets/selection_list/>

- Purpose-built for toggle-multiple. Items are `(prompt, value)` tuples,
  `(prompt, value, initial_selected)` triples, or `Selection` objects.
  Prompts may be Rich `Text`.
- `space` toggles the highlighted item. Inherits `OptionList` navigation:
  `up` / `down` / `home` / `end` / `pagedown` / `pageup`, `enter` selects.
- `selected` property returns the value list. `SelectedChanged` message
  fires on any change (single message for bulk ops like `select_all`);
  also `SelectionHighlighted` / `SelectionToggled`.
- Reactive `highlighted: int | None`. Component classes:
  `selection-list--button[-selected][-highlighted]` plus the inherited
  `option-list--*` set — the theme-closure styling surface (plan phase 4.4).
- `select_all` / `deselect_all` exist for the `a` / `n` keys.

### 1.2 `Collapsible` — the provider group

Page: <https://textual.textualize.io/widgets/collapsible/>

- Container with `title`, `collapsed` (default `True` — pass
  `collapsed=False` for the initially-open provider), optional
  `collapsed_symbol` / `expanded_symbol` (defaults `▶` / `▼`).
- Title click or focused `Enter` toggles. Messages: `Collapsible.Toggled`
  (with `Collapsed` / `Expanded` specialisations) — persist into an
  expanded-set across redraws, same pattern as `Live._collapsed`.
- Compose via constructor children or `with Collapsible(title=…):` block;
  either nests a `SelectionList` directly.

### 1.3 Screens — modal vs. mounted takeover

Pages: <https://textual.textualize.io/guide/screens/>,
API: <https://textual.textualize.io/api/screen/>

- `ModalScreen` dims the screen underneath; its bindings take precedence
  over app bindings. `push_screen(ImportScreen())` opens,
  `dismiss(result)` closes with a confirm payload.
- The mounted alternative is what the picker does today: replace the frame
  while up, restore on close, focus follows the surface. Plan phase 0
  spikes both and picks one — the criterion is focus ownership, `Esc` path,
  and resize behaviour, not taste.

### 1.4 `Button` — entry + footer

Page: <https://textual.textualize.io/widgets/button/>

- Click or focused `Enter`; handled via `on_button_pressed` reading
  `event.button` (`id` distinguishes `import-button` from footer
  confirm/cancel). `flat=True` + CSS carries the entry-button look, same
  as `ThemesButton` (which deliberately keeps the default variant and does
  flatness in CSS — see its class docstring — because the `flat` variant's
  `auto 90%` colour reroutes the label).
- Footer confirm/cancel are ordinary focusable buttons; the entry `Import`
  button mirrors `Themes` (unfocusable, selection-pair paint).

### 1.5 Carriers — `Static` / `Vertical` / `Horizontal`

Gallery: <https://textual.textualize.io/widget_gallery/>

- Preview rows (phase-2 pure units) mount in `Static`/`Vertical`, not as
  controls — same role `Frame` plays for `draw_editor` rows today. No
  bindings, no focus, no selection.

## 2. Spike script (plan phase 0)

Throwaway app: three `Collapsible`s × one `SelectionList` each (~30 rows),
plus a preview `Static` and a footer count. Toggle `ONE_LIST` to compare
against a single `SelectionList` with disabled header rows. Judge only the
spec behaviours: continuous cursor, cross-provider toggle, collapsed skip,
preview repaint, footer count.

```python
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Collapsible, SelectionList, Static
from textual.widgets.selection_list import Selection

PROVIDERS = {
    "Ghostty": [f"ghostty-{i:02d}" for i in range(30)],
    "kitty": [f"kitty-{i:02d}" for i in range(30)],
    "Alacritty": [f"alacritty-{i:02d}" for i in range(30)],
}


class Spike(App[None]):
    CSS = """
    Screen { overflow: hidden; }
    #left { width: 1fr; }
    #right { width: 1fr; border: round $border; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="left"):
                for n, (provider, themes) in enumerate(PROVIDERS.items()):
                    with Collapsible(
                        title=f"{provider} ({len(themes)})",
                        collapsed=(n != 0),
                    ):
                        yield SelectionList(
                            *[Selection(name, (provider, name))
                              for name in themes],
                            id=f"list-{provider}",
                        )
            yield Static("preview: move the cursor", id="right")
        # No Footer: product runs with empty BINDINGS and no footer by design,
        # so the spike keeps the key surface honest (arrows/space/enter only).

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        total = sum(
            len(lst.selected)
            for lst in self.query(SelectionList)
        )
        self.query_one("#right", Static).update(f"{total} selected")

    def on_selection_list_selection_highlighted(
        self, event: SelectionList.SelectionHighlighted
    ) -> None:
        # Attribute names (`selection_list`, `index`) to verify in the spike
        # against the installed Textual before phase 4 relies on them.
        self.query_one("#right", Static).update(
            f"cursor: {event.selection_list.id} [{event.index}]"
        )


if __name__ == "__main__":
    Spike().run()
```

Spike extensions (same sitting): move `highlighted` programmatically
across lists for candidate A; for candidate B replace the three lists with
one list using disabled `Selection`s as headers and note every nav path
that must skip them; wrap the whole `Horizontal` in a `ModalScreen` pushed
over a dummy editor and compare with a mounted swap.

## 3. Message-flow sketch (phase 4 target)

- `SelectionList.SelectedChanged` → footer count (`sum(selected)` over the
  mounted lists) + nothing else. Preview does **not** follow selection.
- Cursor move (`SelectionHighlighted` or headless-state cursor calls) →
  preview repaint from phase-3 lookup (provider id → slots → phase-2 rows).
- `Collapsible.Toggled` → expanded-set add/discard; redraw keeps it (the
  `Live._collapsed` pattern in `app.py`).
- Footer `Button.Pressed` (confirm) → phase-3 confirm mapping → phase-5
  `themes.create` loop → `dismiss(plan)` or status + close.
- `Esc` / `I` / `Ctrl+C` while up → abandon: `dismiss(None)` or state
  clear, buffer untouched.
