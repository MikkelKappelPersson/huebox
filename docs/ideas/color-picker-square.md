# Color picker square (sat/val grid via half-blocks)

Date: 2026-10-06 · Status: idea, not spec, not planned

## The trick
A terminal cell is ~1 wide x 2 tall and holds one fg + one bg. A half-block
glyph splits it in two visible halves:

- `▀` (U+2580): top = fg, bottom = bg
- `▄` (U+2584): top = bg, bottom = fg

So 2 adjacent cells = a ~square patch showing 4 colors:

```
cell 1: fg=A bg=C + ▀   cell 2: fg=B bg=D + ▀
=>  A | B
    C | D
```

Example: `fg(#ff0000) bg(#0000ff) ▀` + `fg(#00ff00) bg(#ffff00) ▀`
gives red/green over blue/yellow.

## The idea
A 2D picker square for the selected slot — e.g. saturation on one axis,
value (lightness) on the other at the current hue — painted with half-blocks
so it stays compact: N terminal rows = 2N color rows. Arrows already move
between slots, so the square would need its own focus/mode or key (or mouse
click via existing `hits=` mechanism) to move *within* the square.

## Why it could be nice
- Denser than the current 1D `hsv_readout` bars: see the neighbourhood of a
  color, not just three 1D slices.
- Roughly square "pixels" for free, no graphics protocol needed.
- Fits the live-everything rule: all cells computed from buffer + hue on the
  call, like `hsv_readout` / `sample_lines`.

## Open questions
- Font gaps: block glyphs assume gapless rendering; some fonts/terminals show
  hairlines. Fallback = full-cell ` ` with bg only (half the resolution)?
- Size budget: square costs rows x cols; §15 layout would need a floor and a
  rung (like `HSV_LADDER`). What gives way on narrow frames?
- Interaction: arrows are taken (§4.3.1 grid walk). Sticky mode? `i`-style
  prompt? Click-to-pick via `hits=` (a click is a keypress, same rule)?
- Precision: hex is the storage form (§5), so picking = quantize to `#rrggbb`
  anyway; preview the hex under the cursor?
- Higher density options if 2x is not enough: quadrants `▘▚▞▟` (2x2 per cell),
  sextants, braille `⠁-⣿` (2x4). Same fg+bg trick, smaller "pixels", worse
  font support.
- Closure harness: I2 holds it to the reference closure like any
  other frame change; keep it behind an env/flag until stable.

## Minimal next probe (if ever)
Hardcode a small e.g. 8x4-cell (16x8-color) SV square at fixed hue in the
`selected` block and look at it at 80x24. No interaction first — just: does
it read as a square on your font/terminal?
