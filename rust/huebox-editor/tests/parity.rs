//! Parity replay of `tests/fixtures/parity_session.json` (plan Phase 1).
//!
//! The scripted session — all adjust axes both ways, multiplier cycle,
//! undo, revert, hex entry, save — replays key-by-key to an IDENTICAL
//! trace and final state. The fixture is read at test time with a
//! std-only JSON reader; this crate has no other dependencies. The
//! `is_hex` / `normalize_hex` vectors from `parity_color.json` replay
//! here too (our ports of those two `color.py` helpers live in this
//! crate).

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::rc::Rc;

use huebox_core::Slots;
use huebox_editor::{apply_key, is_hex, normalize_hex, EditorState, Library, WriteOutcome};
use huebox_render::grid::grid_geometry;
use huebox_render::palette::SLOTS;

// ---------------------------------------------------------------------------
// std-only JSON reader (test code only; same shape as huebox-core's)
// ---------------------------------------------------------------------------

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

    fn arr(&self) -> &Vec<J> {
        match self {
            J::Arr(items) => items,
            _ => panic!("fixture: not an array"),
        }
    }

    fn num(&self) -> f64 {
        match self {
            J::Num(n) => *n,
            _ => panic!("fixture: not a number"),
        }
    }

    fn text(&self) -> &str {
        match self {
            J::Str(s) => s,
            _ => panic!("fixture: not a string"),
        }
    }

    fn boolean(&self) -> bool {
        match self {
            J::Bool(b) => *b,
            _ => panic!("fixture: not a bool"),
        }
    }

    fn is_null(&self) -> bool {
        matches!(self, J::Null)
    }
}

struct Parser<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') {
            self.i += 1;
        }
    }

    fn value(&mut self) -> J {
        self.ws();
        match self.b.get(self.i) {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => J::Str(self.string()),
            Some(b't') => self.literal("true", J::Bool(true)),
            Some(b'f') => self.literal("false", J::Bool(false)),
            Some(b'n') => self.literal("null", J::Null),
            Some(_) => J::Num(self.number()),
            None => panic!("fixture: unexpected end of input"),
        }
    }

    fn eat(&mut self, want: u8) {
        self.ws();
        assert_eq!(self.b.get(self.i), Some(&want), "fixture: bad byte");
        self.i += 1;
    }

    fn literal(&mut self, word: &str, v: J) -> J {
        assert!(
            self.b[self.i..].starts_with(word.as_bytes()),
            "fixture: bad literal"
        );
        self.i += word.len();
        v
    }

    fn object(&mut self) -> J {
        self.eat(b'{');
        let mut pairs = Vec::new();
        self.ws();
        if self.b.get(self.i) == Some(&b'}') {
            self.i += 1;
            return J::Obj(pairs);
        }
        loop {
            self.ws();
            let key = self.string();
            self.eat(b':');
            pairs.push((key, self.value()));
            self.ws();
            match self.b.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b'}') => {
                    self.i += 1;
                    break;
                }
                other => panic!("fixture: bad object tail {other:?}"),
            }
        }
        J::Obj(pairs)
    }

    fn array(&mut self) -> J {
        self.eat(b'[');
        let mut items = Vec::new();
        self.ws();
        if self.b.get(self.i) == Some(&b']') {
            self.i += 1;
            return J::Arr(items);
        }
        loop {
            items.push(self.value());
            self.ws();
            match self.b.get(self.i) {
                Some(b',') => self.i += 1,
                Some(b']') => {
                    self.i += 1;
                    break;
                }
                other => panic!("fixture: bad array tail {other:?}"),
            }
        }
        J::Arr(items)
    }

    fn string(&mut self) -> String {
        assert_eq!(self.b.get(self.i), Some(&b'"'), "fixture: bad string");
        self.i += 1;
        let mut out = String::new();
        loop {
            match *self.b.get(self.i).expect("fixture: unterminated string") {
                b'"' => {
                    self.i += 1;
                    return out;
                }
                b'\\' => {
                    self.i += 1;
                    match self.b[self.i] {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{0008}'),
                        b'f' => out.push('\u{000C}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let h = std::str::from_utf8(&self.b[self.i + 1..self.i + 5])
                                .expect("fixture: bad \\u");
                            let cp = u32::from_str_radix(h, 16).expect("fixture: bad \\u");
                            out.push(char::from_u32(cp).expect("fixture: bad scalar"));
                            self.i += 4;
                        }
                        e => panic!("fixture: bad escape {e:?}"),
                    }
                    self.i += 1;
                }
                _ => {
                    out.push(self.b[self.i] as char);
                    self.i += 1;
                }
            }
        }
    }

    fn number(&mut self) -> f64 {
        let start = self.i;
        while self.i < self.b.len()
            && matches!(
                self.b[self.i],
                b'-' | b'+' | b'0'..=b'9' | b'.' | b'e' | b'E'
            )
        {
            self.i += 1;
        }
        std::str::from_utf8(&self.b[start..self.i])
            .expect("fixture: bad number text")
            .parse::<f64>()
            .expect("fixture: bad number")
    }
}

fn load_fixture(name: &str) -> J {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let path = manifest.join(format!("../../tests/fixtures/{name}"));
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|_| panic!("huebox-editor: fixture missing: {path:?}"));
    let mut p = Parser {
        b: text.as_bytes(),
        i: 0,
    };
    let v = p.value();
    p.ws();
    assert_eq!(p.i, p.b.len(), "fixture: trailing bytes");
    v
}

fn slots_of(obj: &J) -> Slots {
    let mut slots = Slots::default();
    let J::Obj(pairs) = obj else {
        panic!("fixture: slots not an object");
    };
    for (name, value) in pairs {
        slots.set(name, value.text());
    }
    slots
}

fn slots_map(slots: &Slots) -> BTreeMap<String, String> {
    slots
        .iter()
        .map(|(n, h)| (n.to_owned(), h.to_owned()))
        .collect()
}

// ---------------------------------------------------------------------------
// session replay
// ---------------------------------------------------------------------------

struct StubLibrary;

impl Library for StubLibrary {
    fn names(&self) -> Vec<String> {
        vec!["parity".to_owned()]
    }

    fn load(&self, _name: &str) -> Option<(Slots, String)> {
        None
    }

    fn create(&self, _name: &str, _slots: &Slots, _force: bool) -> Result<String, String> {
        Err("unexpected create".to_owned())
    }
}

#[test]
fn parity_session_replays_to_identical_trace_and_final_state() {
    let data = load_fixture("parity_session.json");
    let meta = data.get("meta");
    assert_eq!(meta.get("fmt").text(), "ghostty");
    assert_eq!(meta.get("theme").text(), "parity");
    assert_eq!(meta.get("path").text(), "parity.toml");
    let size = meta.get("size").arr();
    assert_eq!((size[0].num(), size[1].num()), (100.0, 24.0));

    let keys: Vec<String> = data
        .get("keys")
        .arr()
        .iter()
        .map(|k| k.text().to_owned())
        .collect();
    let mut answers: Vec<String> = data
        .get("prompt_responses")
        .arr()
        .iter()
        .map(|k| k.text().to_owned())
        .collect();
    answers.reverse(); // pop from the back, in order

    type Write = (String, String, BTreeMap<String, String>);
    let writes: Rc<RefCell<Vec<Write>>> = Rc::new(RefCell::new(Vec::new()));
    let w = writes.clone();
    let mut st = EditorState::new(
        slots_of(data.get("initial_slots")),
        Box::new(move |theme, path, slots| {
            w.borrow_mut().push((
                theme.unwrap_or("").to_owned(),
                path.to_owned(),
                slots_map(slots),
            ));
            Ok(WriteOutcome::status("saved"))
        }),
    );
    st.theme = Some("parity".to_owned());
    st.fmt = "ghostty".to_owned();
    st.path = "parity.toml".to_owned();
    st.library = Some(Box::new(StubLibrary));
    st.prompt_hex = Some(Box::new(move |_label| {
        if answers.is_empty() {
            None
        } else {
            Some(answers.pop().expect("prompt"))
        }
    }));
    // One geometry per frame, read by the frame and by the keys — what is
    // drawn and what the arrows step through cannot disagree.
    st.grid = grid_geometry(100, "  ");

    let snapshot = |st: &EditorState| {
        (
            st.sel,
            SLOTS[st.sel].to_owned(),
            st.mult,
            st.status.clone(),
            slots_map(&st.slots),
        )
    };

    let trace = data.get("trace").arr();
    assert_eq!(
        trace.len(),
        keys.len() + 1,
        "trace covers every key plus the start"
    );
    let check = |entry: &J,
                 key: Option<&str>,
                 snap: (usize, String, u32, String, BTreeMap<String, String>)| {
        if let Some(key) = key {
            assert_eq!(entry.get("key").text(), key);
        } else {
            assert!(entry.get("key").is_null(), "first trace entry has no key");
        }
        assert_eq!(entry.get("sel").num() as usize, snap.0, "sel after {key:?}");
        assert_eq!(entry.get("slot").text(), snap.1, "slot after {key:?}");
        assert_eq!(entry.get("mult").num() as u32, snap.2, "mult after {key:?}");
        assert_eq!(entry.get("status").text(), snap.3, "status after {key:?}");
        let mut want = BTreeMap::new();
        let J::Obj(pairs) = entry.get("slots") else {
            panic!("fixture: trace slots not an object");
        };
        for (name, value) in pairs {
            want.insert(name.clone(), value.text().to_owned());
        }
        assert_eq!(want, snap.4, "slots after {key:?}");
    };
    check(&trace[0], None, snapshot(&st));
    for (i, key) in keys.iter().enumerate() {
        apply_key(key, &mut st);
        check(&trace[i + 1], Some(key), snapshot(&st));
    }

    let final_ = data.get("final");
    assert_eq!(slots_map(&st.slots), {
        let mut want = BTreeMap::new();
        let J::Obj(pairs) = final_.get("slots") else {
            panic!("fixture: final slots not an object");
        };
        for (name, value) in pairs {
            want.insert(name.clone(), value.text().to_owned());
        }
        want
    });
    assert_eq!(st.sel, final_.get("sel").num() as usize);
    assert_eq!(SLOTS[st.sel], final_.get("slot").text());
    assert_eq!(st.mult, final_.get("mult").num() as u32);
    assert_eq!(st.status, final_.get("status").text());
    assert_eq!(st.dirty(), final_.get("dirty").boolean());
    assert_eq!(st.written, final_.get("written").boolean());
    assert_eq!(st.quit, final_.get("quit").boolean());

    let fixture_writes = data.get("writes").arr();
    let got = writes.borrow();
    assert_eq!(got.len(), fixture_writes.len(), "one write per save");
    for (entry, (theme, path, slots)) in fixture_writes.iter().zip(got.iter()) {
        assert_eq!(theme, entry.get("theme").text());
        assert_eq!(path, entry.get("path").text());
        let mut want = BTreeMap::new();
        let J::Obj(pairs) = entry.get("slots") else {
            panic!("fixture: write slots not an object");
        };
        for (name, value) in pairs {
            want.insert(name.clone(), value.text().to_owned());
        }
        assert_eq!(slots, &want);
    }
}

// ---------------------------------------------------------------------------
// is_hex / normalize_hex vectors (parity_color.json)
// ---------------------------------------------------------------------------

#[test]
fn parity_color_hex_helpers() {
    let data = load_fixture("parity_color.json");
    for case in data.get("is_hex").arr() {
        assert_eq!(
            is_hex(case.get("input").text()),
            case.get("expected").boolean(),
            "is_hex({:?})",
            case.get("input").text()
        );
    }
    for case in data.get("normalize_hex").arr() {
        assert_eq!(
            normalize_hex(case.get("input").text()),
            case.get("expected").text(),
            "normalize_hex({:?})",
            case.get("input").text()
        );
    }
}
