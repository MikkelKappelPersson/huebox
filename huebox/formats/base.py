"""Shared machinery for the flat (line-oriented) formats (§6).

Every format is described by a list of rules. A rule is

    (compiled_pattern, resolver, rewriter)

where `resolver(match)` names the canonical slot the line carries and
`rewriter(line, match, value)` returns the line with a new colour spliced
in. Reading only needs the resolver; writing needs both, which is what
lets us edit files in place without disturbing comments or spacing.
"""

from __future__ import annotations

from ..color import normalize_hex

Rule = tuple


def _palette_resolver(match) -> str:
    return f"palette-{match.group('index')}"


def _named_resolver(match, mapping=None) -> str:
    name = match.group("name")
    return (mapping or {}).get(name, name.replace("_", "-"))


def _palette_rewriter(match) -> str:
    return f"{match.group('pre')}{match.group('index')}" \
           f"{match.group('mid')}#{{value}}"


def _named_rewriter(match) -> str:
    return f"{match.group('pre')}{match.group('name')}{match.group('mid')}#{{value}}"


def _palette_rule(pattern):
    """Rule for `key<N> = #hex` (and the space-separated kitty spelling)."""
    return (pattern,
            lambda m: f"palette-{m.group('index')}",
            lambda line, m, v: line[:m.start("val")] + v + line[m.end("val"):])


def _named_rule(pattern, mapping=None):
    """Rule for `name = #hex`, mapping terminal spellings to canonical slots."""
    return (pattern,
            lambda m: _named_resolver(m, mapping),
            lambda line, m, v: line[:m.start("val")] + v + line[m.end("val"):])


HEX = r"#?[0-9a-fA-F]{6}\b"


# --- dispatch ---------------------------------------------------------------

def read_flat(path: str, rules) -> dict:
    slots: dict[str, str] = {}
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return slots
    for line in lines:
        for pattern, resolver, _ in rules:
            match = pattern.match(line)
            if match:
                slots[resolver(match)] = normalize_hex(match.group("val"))
                break
    return slots


def write_flat(path: str, slots: dict, rules) -> None:
    lines = open(path, encoding="utf-8").read().splitlines()
    for index, line in enumerate(lines):
        for pattern, resolver, rewriter in rules:
            match = pattern.match(line)
            if match:
                slot = resolver(match)
                if slot in slots:
                    lines[index] = rewriter(line, match, slots[slot])
                break
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
