//! The `huebox` binary: parse, dispatch, exit with the code.

use std::io::IsTerminal;

fn main() {
    let tty = std::io::stdin().is_terminal() && std::io::stdout().is_terminal();
    let code = huebox::cli::run(
        std::env::args().collect(),
        &mut std::io::stdout().lock(),
        &mut std::io::stderr().lock(),
        tty,
        None,
    );
    std::process::exit(code);
}
