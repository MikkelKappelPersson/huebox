{
  description = "Terminal theme editor with live preview";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
    in
    {
      packages = eachSystem (pkgs: {
        default = pkgs.rustPlatform.buildRustPackage {
          pname = "huebox";
          # keep in step with rust/Cargo.toml (release tags enforce tag == version)
          version = "0.5.4";
          src = ./rust;
          cargoLock.lockFile = ./rust/Cargo.lock;

          meta = with pkgs.lib; {
            description = "Terminal theme editor with live preview";
            homepage = "https://github.com/MikkelKappelPersson/huebox";
            license = licenses.mit;
            mainProgram = "huebox";
          };
        };
      });

      overlays.default = final: prev: {
        huebox = self.packages.${prev.system}.default;
      };
    };
}
