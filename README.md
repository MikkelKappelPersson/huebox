# huebox

A terminal theme editor with live preview.

`huebox` shows you the colours your terminal is actually using, and lets you
change them from a keyboard-driven interface — no hex codes, no hand-editing,
no restarting.

```
  huebox              TUI when stdout is a terminal, static preview otherwise
  huebox edit [name]  force the interactive editor (a theme if named)
  huebox show [name]  force the static preview (of a theme if named)
  huebox --dump [name]  print the resolved colours and exit
  huebox --formats    list supported formats
  huebox new <name>   create a theme from your terminal (or a built-in ramp)
  huebox list         list your themes, current one marked
  huebox use <name>   make a theme current and push it to your terminal
  huebox import <name>  snapshot the detected terminal into a theme

Flags on top of the v1 set: `--to ghostty,kitty` chooses push targets,
`--no-push` writes the theme file only, `--no-reload` leaves the terminal's
own reload to you, `--ghostty-in-place` keeps a Ghostty
push in the file the colours already live in, `--force` replaces a theme
`new` / `import` would not overwrite, `--from <fmt>` names the terminal to
read from.
```

## Install

```sh
git clone https://github.com/MikkelKappelPersson/huebox
cd huebox
pipx install .          # or: pip install --user .
```

The one dependency beyond the stdlib is [Pygments](https://pygments.org),
which powers the editor's live code sample — it installs automatically,
and every colour in the sample comes from the theme's own palette.

## What's new in 0.2

**Your themes live in one place, not in three configs.** `~/.config/huebox` is
the truth: `huebox import dusk` snapshots a terminal into a theme,
`huebox edit dusk` works on it, and every save or `huebox use dusk` pushes it
back to the terminal you are in. `t` inside the editor switches themes, `N`
turns a direct-config session into a library one, and a Ghostty config that
keeps its colours in a theme file is pushed there rather than into whatever
file it happens to point at. Editing is staged too: keystrokes live in a
buffer, `Ctrl+S` is the only write.

Five changes you might notice on upgrade:

- A push now ends with your terminal reloading its config, so the colours
  are live when the command finishes, and opening a theme in the picker
  (`t`, `Enter`) saves and pushes it instead of only loading it into the
  buffer. `--no-reload` turns the reload off.
- If your Ghostty config has a `theme =` line, saving a huebox theme now
  writes that theme's own file under `~/.config/ghostty/themes/` and swaps
  that one line, where before it spliced the colours into whichever theme
  file the config pointed at — so saving a theme named `test` used to
  rewrite `Nightspice`. A `theme =` naming a file that is missing is now
  repaired by the save rather than refused. Configs with inline colours are
  edited in place as before; `--ghostty-in-place` restores the old
  behaviour where it is safe to, and refuses it where it is not.
- kitty's config-path override is `KITTY_CONFIG_DIRECTORY`, which is what
  kitty itself reads. The old `KITTY_CONFIG_DIR` spelling still works for one
  more release, and is probed second.
- `ALACRITTY_CONFIG_DIR` and `ALACRITTY_CONFIG` are gone — Alacritty documents
  no such variable, so they never pointed at anything Alacritty read. Use
  `--config`.
- Alacritty configs are now also looked for at
  `$XDG_CONFIG_HOME/alacritty.toml`, which is the second path Alacritty's own
  search order checks. The legacy `alacritty.yml` still counts.

Nothing above changes what huebox writes to a config: colours only, line by
line — and a save that changes nothing no longer even touches the file, so its
mtime survives too.

## Themes

`~/.config/huebox` is the truth: one file per theme in `themes/<name>.toml`,
plus `state.toml` naming the one you are working on. A terminal config is a
push target, not the place your theme lives.

```sh
huebox import dusk            # snapshot this terminal into a theme
huebox edit dusk              # edit it; every Ctrl+S saves and pushes
huebox use dusk               # switch to it and push, no editor
huebox use dusk --to ghostty,kitty   # push to specific terminals
huebox use dusk --no-push             # switch without touching a config
```

`huebox edit` with no name opens the current theme; `huebox show <name>` and
`huebox --dump <name>` read a theme file instead of a config. With no theme
library at all, `huebox edit` is what it always was: editing the terminal
config in place.

## Editing

Run `huebox edit` and drive it with the keyboard.

| Key | Action |
| --- | --- |
| arrows | move between slots — along a row, or up/down a row, in the grid on screen |
| `q` / `w` | hue −/+ |
| `a` / `s` | saturation −/+ |
| `z` / `x` | lightness −/+ |
| `f` | cycle step size ×1 → ×5 → ×20 |
| `i` | type a hex value |
| `Ctrl+S` | save — the session's only write: the theme file, then a push |
| `u` / `r` | undo / revert to the last save |
| `t` | theme picker — arrows, `Enter` opens, `n` makes a theme from the buffer, `Esc` back |
| `N` | save the buffer as a new theme (and then save it) |
| `Esc` | quit — twice if there are unsaved changes |

Edits live in an in-memory buffer: nothing is written until you press
`Ctrl+S`. The editor *renders* from that buffer, so everything on screen —
palette, interface, code sample, and the background / selection / cursor
examples — is live and truecolor before the file changes (a frame with room to
spare also shows a git diff of the sample). The frame's own text is drawn
from the buffer too: the `huebox` wordmark at the top wears the six bright
hues one letter each, a key in the hint line is bright (`arrows`), the label
beside it is teal (**move**), the furniture — a path, a hex, a count — is
muted, and a section header is the theme's own foreground in bold. Edit
`palette-11` and both the escape in the sample and the keys change colour on
the same frame. The floor is the buffer's too: every row of the editor —
the ground under the palette grid, the air between the widgets, the column
after the last hint — is painted in `background`, so the frame is a sample
of the theme rather than a preview beside it.

The `selected` row ends in a reading of the slot's own colour: three
bars, each a window on one axis — hue across ±60° of its own hue at its own
saturation and value, saturation and value likewise — with the value printed
in the middle of its own bar, because the middle cell *is* the value. Press
`q` and the wheel slides under a number that does not move; press `a`/`s` and
the saturation window slides too, black on white or white on black as the
cell underneath needs. The exact reading — `hue 207.0  sat 59.4%  val 93.7%`,
to the precision you are actually steering with — sits on the row below,
beside the specimen; the bars are the glance, the numbers are the truth, and
neither gives up a row for the other. On a terminal too narrow for the bars,
only the numbers are left. Quit with
unsaved changes and huebox asks for a second `Esc` first; `r` throws the
buffer away and goes back to your last save.

The header names what you are editing: `ember ● ghostty` for a theme (the `●`
marks unsaved buffer changes) or `direct:/path/to/config` in a legacy
direct-config session. `t` opens the theme picker without leaving the editor:
arrows and `Enter` to use a theme, `n` to make one from the buffer you are
looking at, `Esc` to go back. `Enter` is a save, not a peek — it writes the
theme, pushes it and reloads your terminal, so the theme you pick is the one
on screen; `Ctrl+S` stays for the buffer's own edits. Opening a theme while
the buffer has unsaved edits is refused with
`save (Ctrl+S) or revert (r) first` rather than losing them. `N` is the way
out of a direct-config session: it asks for a name, makes the theme, makes it
current, and saves it through the same pipeline. If a name is already taken,
huebox says so and waits for `y` (overwrite) or another name — no modal, and
nothing is written until you answer.

`Ctrl+S` is two writes: the theme file first, then a push into the terminal
config that holds your colours — the status bar says which
(`saved dusk → ghostty`). The theme file is the truth and is never rolled
back: if a push fails, the save still stands, huebox says why on stderr, and
the session ends with exit 1. Editing a terminal config directly (no themes
yet) takes a `<config>.huebox.bak` on its first save; theme files get none.

A push that lands asks the terminal to re-read its config, so the new colours
are live when the command finishes: ghostty is signalled the way `Ctrl+Shift+,`
signals it and kitty is asked over its own remote control. A terminal that
cannot be told keeps the advice line in the report — `Ctrl+Shift+,` for
ghostty, `Ctrl+Shift+F5` for kitty, and alacritty picks changes up by itself.
`--no-reload` turns the whole step off. The code sample inside the editor is
rendered in truecolor from the values you are editing, so it updates *before*
the reload. A tall enough frame also draws a
git diff of that sample — `+` lines wear palette 2, `-` lines palette 1 — so a
theme whose red and green are wrong says so before you reload; the hunk is
drawn only out of rows the sample did not need, so it never costs it a line.

A config is only ever edited line by line, so a colour the config does not
define is reported (`not carried by this config: cursor-text, …`) and left
alone — huebox will not invent a line in your terminal's config. A Ghostty
theme file is the one file huebox writes whole, and only because it is
named after the theme it holds.

### Ghostty themes

Ghostty keeps its colours in theme files, and a save joins it there whenever
your config is already organised that way — when it has a `theme =` line,
huebox writes the theme under *its own* name and repoints your config at
it:

```sh
huebox use dusk --to ghostty          # or edit dusk + Ctrl+S
```

That writes `~/.config/ghostty/themes/dusk` — 22 colours, in Ghostty's own
`palette = 0=#…` spelling, read back by huebox without drift — and points
your main config at it with a single `theme =` line: an existing one keeps
its spacing, its quotes and its comment and only the value changes, a config
without one gets the line appended, and every other byte of the file is left
exactly as it was. **Your previous theme file is not touched.** A theme's
colours never land in a file that belongs to another theme, which is the
whole reason the save goes through a file at all: saving `dusk` while your
config is on `Nightspice` gives you a `dusk` file and moves one line, where
before it would have rewritten `Nightspice` under a name that was no longer
true. The themes directory is shared with Ghostty's built-ins, so an export
overwrites a same-name file there (the report tells you when it did).

If your config holds its colours inline — or in a `config-file` include —
there is no theme name in play, so huebox edits the file the colours are
already in and leaves your layout alone. Two flags say it out loud:
`--ghostty-native` forces the export even there (adding the `theme =` line
for you), `--ghostty-in-place` forces the edit; asking for both is refused.
The forced export is the one case that can leave a colour behind a theme
file, and the report says so when it does. Both apply to the ghostty target
only — `--to ghostty,kitty` exports for Ghostty and pushes kitty the
ordinary way — and `--no-push` wins over either.

A `theme =` line pointing at a file that is not there (Ghostty calls that
a configuration error on reload) is treated as a broken config, not a
colourless one: `huebox use <name>` writes the file, fixes the pointer, and
tells you the file was missing. Nothing is overwritten to do it — the file
did not exist.

## Supported terminals

| Format | Config |
| --- | --- |
| Ghostty | `$XDG_CONFIG_HOME/ghostty/config.ghostty`, including `config-file` includes and `theme = Name` indirection |
| kitty | `$XDG_CONFIG_HOME/kitty/kitty.conf`, `~/.kitty.conf`, or `KITTY_CONFIG_DIRECTORY` |
| Alacritty | `$XDG_CONFIG_HOME/alacritty/alacritty.toml`, `$XDG_CONFIG_HOME/alacritty.toml`, `~/.alacritty.toml` (and the legacy `alacritty.yml`), dotted keys, `[section]` tables and inline tables |

The search order is each terminal's own (upstream docs, checked October
2026). `~/.config/...` paths follow `XDG_CONFIG_HOME` when you set it. Alacritty
documents no environment variable for its config path, so `--config` is the
only override there; for kitty, `KITTY_CONFIG_DIRECTORY` is upstream's
spelling (the old `KITTY_CONFIG_DIR` still works, deprecated, for one
release).

huebox only offers a terminal whose config actually contains colours, so a
leftover `ALACRITTY_SOCKET` from a session last week will not hijack your
Ghostty config — and it will not push into one either. Override the guess with
`--format`:

```sh
huebox edit --format kitty
huebox show --config ~/dotfiles/alacritty.toml
huebox use ember --to ghostty --config ~/dotfiles/ghostty/config
```

`--config` pushes one format only: with a single `--to` it names the file,
with several `--to` targets it is refused (ambiguity, not a guess).

## Safety

Writes are line-level: only the colour tokens are replaced, so comments,
ordering, alignment and every unrelated setting survive untouched. A save that
changes nothing is not just byte-identical, it does not write the file at all —
its mtime is untouched too, so a backup job or a config manager never notices a
save that saved nothing. Pushing a theme uses that same writer, so a push is no
more invasive than the v1 in-place edit. The only file huebox writes outside
your terminal config is its own `~/.config/huebox` library.

## Development

```sh
python3 -m unittest discover -s tests
```

The tests cover reading every slot, round-tripping without drift, changing
only the intended lines, leaving a config untouched (bytes *and* mtime) when
nothing changed, following Ghostty includes and `theme =` pointers, pushing
without inventing keys, and keeping the layout inside narrow terminals.

## License

MIT
