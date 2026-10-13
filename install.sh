#!/bin/sh
# huebox installer: fetches the prebuilt binary for this machine from a
# GitHub release, verifies its checksum, and installs it.
#
#   curl -fsSL https://raw.githubusercontent.com/MikkelKappelPersson/huebox/main/install.sh | sh
#
# Pinned version (leading v optional) or custom install dir:
#
#   HUEBOX_VERSION=0.5.1 sh install.sh
#   INSTALL_DIR=/usr/local/bin sh install.sh
set -eu

REPO="MikkelKappelPersson/huebox"

die() {
  echo "huebox: $*" >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"

os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
  Linux) platform="linux" ;;
  Darwin) platform="macos" ;;
  *) die "unsupported OS: $os (prebuilt binaries ship for Linux and macOS)" ;;
esac
case "$arch" in
  x86_64 | amd64) arch="x86_64" ;;
  aarch64 | arm64) arch="aarch64" ;;
  *) die "unsupported architecture: $arch" ;;
esac
case "$platform-$arch" in
  linux-x86_64) label="x86_64-linux" ;;
  linux-aarch64) label="aarch64-linux" ;;
  macos-aarch64) label="arm64-macos" ;;
  macos-x86_64) label="x86_64-macos" ;;
esac

version="${HUEBOX_VERSION:-${1:-latest}}"
if [ "$version" = "latest" ]; then
  tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | grep '"tag_name":' | cut -d'"' -f4)"
  [ -n "$tag" ] || die "could not resolve the latest release"
  version="$tag"
fi
version="${version#v}"
[ -n "$version" ] || die "empty version"

base="https://github.com/$REPO/releases/download/v$version/huebox-$version-$label"
echo "huebox: installing v$version ($label)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM
curl -fsSL -o "$tmp/huebox.tar.gz" "$base.tar.gz" \
  || die "no binary for $label at v$version"
curl -fsSL -o "$tmp/huebox.tar.gz.sha256" "$base.tar.gz.sha256" \
  || die "missing checksum for $label at v$version"

expected="$(cut -d' ' -f1 < "$tmp/huebox.tar.gz.sha256")"
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$tmp/huebox.tar.gz" | cut -d' ' -f1)"
else
  actual="$(shasum -a 256 "$tmp/huebox.tar.gz" | cut -d' ' -f1)"
fi
[ "$expected" = "$actual" ] || die "checksum mismatch (download may be corrupt)"

tar -xzf "$tmp/huebox.tar.gz" -C "$tmp"

install_dir="${INSTALL_DIR:-$HOME/.local/bin}"
mkdir -p "$install_dir"
cp "$tmp/huebox" "$install_dir/huebox"
chmod 755 "$install_dir/huebox"
"$install_dir/huebox" --version

echo "huebox: installed to $install_dir/huebox"
case ":$PATH:" in
  *":$install_dir:"*) ;;
  *) echo "huebox: add it to your PATH: export PATH=\"$install_dir:\$PATH\"" ;;
esac
