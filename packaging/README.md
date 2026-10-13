# OBS setup (one time, needs an openSUSE account)

`packaging/` holds everything OBS needs: the rpm spec, the Debian
packaging, and the `_service` file (git checkout + cargo vendoring +
tarball). What remains is clicking it together under your account:

1. Register at https://id.opensuse.org, then open
   https://build.opensuse.org → Home Project (e.g. `home:<you>`).
2. Create package `huebox`, upload `packaging/_service`,
   `packaging/huebox.spec`, and the `packaging/debian/*` files
   (as `debian.control`, `debian.rules`, … — OBS maps the prefix).
3. Add repositories: openSUSE_Tumbleweed, Debian_12, Ubuntu_24.04,
   Fedora_41 (Add Repositories → pick per distro).
4. Trigger the first build. Likely first-run fixes:
   - service tarball names must match `Source0`/`Source1` in the spec;
   - the `cargo_vendor` output layout must match the `.cargo/config.toml`
     the spec writes (both live in `huebox.spec` `%prep`).
5. Publish the repo URLs; users add them once, then
   `apt install huebox` / `dnf install huebox` / `zypper install huebox`.

Per release: bump `Version` in the spec, `revision` in `_service`, and
add a `debian/changelog` entry — or automate with `versionformat` +
`changesgenerate` (already on in `_service`).

## nix

`flake.nix` (repo root) builds the crate via `rustPlatform` from
`rust/Cargo.lock` — no hashes to maintain:

```sh
nix run github:MikkelKappelPersson/huebox
```

Keep `version` in the flake in step with `rust/Cargo.toml`.
Upstream nixpkgs submission (the discovery win) is a separate PR to
`NixOS/nixpkgs` — the flake's `buildRustPackage` block is the template.
