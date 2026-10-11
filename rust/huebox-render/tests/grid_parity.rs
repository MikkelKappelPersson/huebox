//! Cell-grid comparison: the Rust frame must equal the Python frame.
//!
//! `tests/dump_grid.py` dumps fixture frames as `(char, fg, bg)` triples
//! plus the click map and region checkpoints; this test replays the same
//! fixtures through [`huebox_render::frame::frame`] and asserts identical
//! grids — same rows, same cells, same hits, same regions. Any visual
//! drift fails here, not in a review.

use huebox_render::frame::{frame, FrameOpts};
use huebox_render::{clip, pack, visible};

// -- std-only JSON reader (test code only) -------------------------------

#[derive(Debug)]
enum J {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl J {
    fn get(&self, key: &str) -> &J {
        match self {
            J::Obj(pairs) => pairs
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v)
                .unwrap_or_else(|| panic!("fixture: missing key {key:?}")),
            _ => panic!("fixture: {key:?} not an object"),
        }
    }

    fn arr(&self) -> &[J] {
        match self {
            J::Arr(items) => items,
            _ => panic!("fixture: not an array"),
        }
    }

    fn text(&self) -> &str {
        match self {
            J::Str(s) => s,
            _ => panic!("fixture: not a string"),
        }
    }

    fn num(&self) -> usize {
        match self {
            J::Num(n) => *n as usize,
            _ => panic!("fixture: not a number"),
        }
    }
}

struct Parser<'a> {
    bytes: &'a [u8],
    pos: usize,
}

fn parse(text: &str) -> J {
    let mut p = Parser {
        bytes: text.as_bytes(),
        pos: 0,
    };
    p.value()
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.pos < self.bytes.len() && self.bytes[self.pos].is_ascii_whitespace() {
            self.pos += 1;
        }
    }

    fn value(&mut self) -> J {
        self.ws();
        match self.bytes[self.pos] {
            b'{' => self.object(),
            b'[' => self.array(),
            b'"' => J::Str(self.string()),
            b't' => {
                self.lit("true");
                J::Bool(true)
            }
            b'f' => {
                self.lit("false");
                J::Bool(false)
            }
            b'n' => {
                self.lit("null");
                J::Null
            }
            _ => J::Num(self.number()),
        }
    }

    fn lit(&mut self, word: &str) {
        assert!(self.bytes[self.pos..].starts_with(word.as_bytes()));
        self.pos += word.len();
    }

    fn object(&mut self) -> J {
        self.pos += 1;
        let mut pairs = Vec::new();
        loop {
            self.ws();
            if self.bytes[self.pos] == b'}' {
                self.pos += 1;
                return J::Obj(pairs);
            }
            let key = self.string();
            self.ws();
            assert_eq!(self.bytes[self.pos], b':');
            self.pos += 1;
            pairs.push((key, self.value()));
            self.ws();
            match self.bytes[self.pos] {
                b',' => self.pos += 1,
                b'}' => {}
                c => panic!("fixture: bad object byte {c}"),
            }
        }
    }

    fn array(&mut self) -> J {
        self.pos += 1;
        let mut items = Vec::new();
        loop {
            self.ws();
            if self.bytes[self.pos] == b']' {
                self.pos += 1;
                return J::Arr(items);
            }
            items.push(self.value());
            self.ws();
            match self.bytes[self.pos] {
                b',' => self.pos += 1,
                b']' => {}
                c => panic!("fixture: bad array byte {c}"),
            }
        }
    }

    fn string(&mut self) -> String {
        assert_eq!(self.bytes[self.pos], b'"');
        self.pos += 1;
        let mut out = String::new();
        loop {
            match self.bytes[self.pos] {
                b'"' => {
                    self.pos += 1;
                    return out;
                }
                b'\\' => {
                    self.pos += 1;
                    match self.bytes[self.pos] {
                        b'n' => out.push('\n'),
                        b't' => out.push('\t'),
                        b'r' => out.push('\r'),
                        b'u' => {
                            let hex = std::str::from_utf8(&self.bytes[self.pos + 1..self.pos + 5])
                                .unwrap();
                            let cp = u32::from_str_radix(hex, 16).unwrap();
                            // fixtures never emit lone surrogates
                            out.push(char::from_u32(cp).unwrap());
                            self.pos += 4;
                        }
                        c => out.push(c as char),
                    }
                    self.pos += 1;
                }
                _ => {
                    let rest = std::str::from_utf8(&self.bytes[self.pos..]).unwrap();
                    let ch = rest.chars().next().unwrap();
                    out.push(ch);
                    self.pos += ch.len_utf8();
                }
            }
        }
    }

    fn number(&mut self) -> f64 {
        let start = self.pos;
        while self.pos < self.bytes.len()
            && (self.bytes[self.pos].is_ascii_digit() || b".-eE+".contains(&self.bytes[self.pos]))
        {
            self.pos += 1;
        }
        std::str::from_utf8(&self.bytes[start..self.pos])
            .unwrap()
            .parse()
            .unwrap()
    }
}

// -- frame replay --------------------------------------------------------

fn fixture_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

#[test]
fn frames_match_python_cell_for_cell() {
    let text = std::fs::read_to_string(fixture_dir().join("frames.json")).unwrap();
    let frames = parse(&text);
    for fr in frames.arr() {
        let name = fr.get("name").text();
        let argv = fr.get("argv");
        let mut slots = huebox_core::Slots::default();
        if let J::Obj(pairs) = fr.get("slots") {
            for (k, v) in pairs {
                slots.set(k, v.text());
            }
        }
        let banner = argv.get("use_banner");
        let opts = FrameOpts {
            label: argv.get("label").text().to_owned(),
            path: argv.get("path").text().to_owned(),
            sel: argv.get("sel").num(),
            undo_len: argv.get("undo_len").num(),
            status: argv.get("status").text().to_owned(),
            cols: argv.get("cols").num(),
            rows: argv.get("rows").num(),
            use_banner: match banner {
                J::Bool(true) => Some(true),
                J::Bool(false) => Some(false),
                _ => None,
            },
            indent: "  ".to_owned(),
            footer: true,
        };
        let out = frame(&opts, &slots);
        let grid = fr.get("grid").arr();
        assert_eq!(out.rows.len(), grid.len(), "{name}: row count");
        for (y, (row, want)) in out.rows.iter().zip(grid.iter()).enumerate() {
            let want = want.arr();
            assert_eq!(row.len(), want.len(), "{name} row {y}: width");
            for (x, (cell, triple)) in row.iter().zip(want.iter()).enumerate() {
                let triple = triple.arr();
                assert_eq!(
                    cell.ch.to_string(),
                    triple[0].text(),
                    "{name} ({x},{y}): char"
                );
                assert_eq!(cell.fg, triple[1].text(), "{name} ({x},{y}): fg");
                assert_eq!(cell.bg, triple[2].text(), "{name} ({x},{y}): bg");
            }
        }
        let hits = fr.get("hits").arr();
        assert_eq!(out.hits.len(), hits.len(), "{name}: hit count");
        for (hit, want) in out.hits.iter().zip(hits.iter()) {
            let want = want.arr();
            assert_eq!(
                [hit.y, hit.x0, hit.x1, hit.slot],
                [want[0].num(), want[1].num(), want[2].num(), want[3].num()],
                "{name}: hit"
            );
        }
        let regions = fr.get("regions").arr();
        assert_eq!(out.regions.len(), regions.len(), "{name}: region count");
        for ((n, first, count), want) in out.regions.iter().zip(regions.iter()) {
            let want = want.arr();
            assert_eq!(
                (n.as_str(), *first, *count),
                (want[0].text(), want[1].num(), want[2].num()),
                "{name}: region"
            );
        }
    }
}

#[test]
fn clip_matches_python_on_adversarial_strings() {
    let text = std::fs::read_to_string(fixture_dir().join("clip_corpus.json")).unwrap();
    let corpus = parse(&text);
    for case in corpus.arr() {
        let input = case.get("input").text();
        assert_eq!(
            visible(input),
            case.get("visible").num(),
            "{input:?}: visible"
        );
        if let J::Obj(pairs) = case.get("clips") {
            for (cols, want) in pairs {
                let cols: usize = cols.parse().unwrap();
                assert_eq!(clip(input, cols), want.text(), "{input:?} at {cols}");
            }
        }
    }
}

#[test]
fn pack_matches_python_vectors() {
    let text = std::fs::read_to_string(fixture_dir().join("pack_cases.json")).unwrap();
    let cases = parse(&text);
    for case in cases.arr() {
        let items: Vec<String> = case
            .get("items")
            .arr()
            .iter()
            .map(|i| i.text().to_owned())
            .collect();
        let cols = case.get("cols").num();
        let sep = case.get("sep").text();
        let want: Vec<&str> = case.get("lines").arr().iter().map(|i| i.text()).collect();
        assert_eq!(pack(items, cols, sep), want);
    }
}

#[test]
fn splitter_matches_pygments_stream() {
    let text = std::fs::read_to_string(fixture_dir().join("sample_runs.json")).unwrap();
    let runs = parse(&text);
    let ours = huebox_render::sample::sample_runs();
    assert_eq!(ours.len(), runs.arr().len(), "run count");
    for ((slot, chunk), want) in ours.iter().zip(runs.arr().iter()) {
        assert_eq!(*slot, want.get("slot").text());
        assert_eq!(chunk, want.get("text").text());
    }
}
