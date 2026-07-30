{
    description = "Context - a context-oriented desktop shell";

    inputs = {
        nixpkgs = {
            url = "github:NixOS/nixpkgs/nixos-unstable";
        };
    };

    outputs = { self, nixpkgs }:
        let
            system = "x86_64-linux";
            pkgs = nixpkgs.legacyPackages.${system};
            python = pkgs.python313;
        in
        {
            devShells.${system}.default = pkgs.mkShell {
                packages = [
                    (python.withPackages (ps: [
                        ps.pygobject3
                        ps.pycairo
                    ]))
                    pkgs.gtk4
                    pkgs.libadwaita
                    pkgs.gobject-introspection
                    pkgs.pkg-config
                ];

                shellHook = ''
                    export GSETTINGS_SCHEMA_DIR="${pkgs.gtk4}/share/gsettings-schemas/${pkgs.gtk4.name}/glib-2.0/schemas"

                    echo ""
                    echo "  Context dev shell"
                    echo "  ─────────────────"
                    echo "  python3 -m context     launch the launcher"
                    echo "  contexts.json          ''${XDG_DATA_HOME:-$HOME/.local/share}/context/contexts.json"
                    echo ""
                '';
            };
        };
}
