Name:           huebox
Version:        0.5.1
Release:        1
Summary:        Terminal theme editor with live preview
License:        MIT
URL:            https://github.com/MikkelKappelPersson/huebox
Source0:        %{name}-%{version}.tar.gz
Source1:        vendor.tar.gz
BuildRequires:  cargo
BuildRequires:  rust

%description
huebox shows the colours your terminal is actually using and lets you
change them from a keyboard-driven interface -- no hex codes, no
hand-editing, no restarting.

%prep
# vendor.tar.gz comes from the cargo_vendor OBS service (build workers
# have no network); point crates-io at it.
%autosetup -a 1
mkdir -p .cargo
cat > .cargo/config.toml <<'EOF'
[source.crates-io]
replace-with = "vendored-sources"
[source.vendored-sources]
directory = "vendor"
EOF

%build
cargo build --release --offline --manifest-path rust/Cargo.toml

%install
install -Dm755 rust/target/release/huebox %{buildroot}%{_bindir}/huebox

%files
%{_bindir}/huebox

%changelog
