# huebox

A terminal theme editor with live preview.

`huebox` shows you the colours your terminal is actually using, and lets you
change them from a keyboard-driven interface — no hex codes, no hand-editing,
no restarting.

```
  huebox              TUI when stdout is a terminal, static preview otherwise
  huebox edit         force the interactive editor
  huebox show         force the static preview
  huebox --dump       print the resolved colours and exit
  huebox --formats    list supported formats
```

## Install

```sh
git clone https://github.com/MikkelKappelPersson/huebox
cd huebox
pipx install .          # or: pip install --user .
```

No third-party dependencies. [Pygments](https://pygments.org) is optional and
only improves the code sample in the editor:

```sh
pipx install '.[highlight]'
```

## Editing

Run `huebox edit` and drive it with the keyboard.

| Key | Action |
| --- | --- |
| arrows | move between slots |
| `q` / `w` | hue −/+ |
| `a` / `s` | saturation −/+ |
| `z` / `x` | lightness −/+ |
| `f` | cycle step size ×1 → ×5 → ×20 |
| `i` | type a hex value |
| `Ctrl+S` | save — the session's only write to the file |
| `u` / `r` | undo / revert to the last save |
| `Esc` | quit — twice if there are unsaved changes |

Edits live in an in-memory buffer: nothing is written until you press
`Ctrl+S`. The editor *renders* from that buffer, so everything on screen —
palette, interface, code sample, and the background / selection / cursor
examples — is live and truecolor before the file changes. Quit with unsaved
changes and huebox asks for a second `Esc` first; `r` throws the buffer away
and goes back to your last save. The first save of a session drops a
`<config>.huebox.bak` beside the config holding its pre-save state.

Reload your terminal — Ghostty `Ctrl+Shift+,`, kitty `Ctrl+Shift+F5`,
alacritty picks changes up automatically — and the new colours are live. The
code sample inside the editor is rendered in truecolor from the values you are
editing, so it updates *before* you reload.

## Supported terminals

| Format | Config |
| --- | --- |
| Ghostty | `~/.config/ghostty/config.ghostty`, including `config-file` includes and `theme = Name` indirection |
| kitty | `~/.config/kitty/kitty.conf`, `~/.kitty.conf` |
| Alacritty | `~/.config/alacritty/alacritty.toml`, dotted keys, `[section]` tables and inline tables |

huebox only offers a terminal whose config actually contains colours, so a
leftover `ALACRITTY_SOCKET` from a session last week will not hijack your
Ghostty config. Override the guess with `--format`:

```sh
huebox edit --format kitty
huebox show --config ~/dotfiles/alacritty.toml
```

## Safety

Writes are line-level: only the colour tokens are replaced, so comments,
ordering, alignment and every unrelated setting survive untouched. A no-op
write is byte-identical to the input, which the test suite asserts for every
format.

## Development

```sh
python3 -m unittest discover -s tests
```

The tests cover reading every slot, round-tripping without drift, changing
only the intended lines, and keeping the layout inside narrow terminals.

## License

MIT
