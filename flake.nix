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
                    pkgs.gtk4-layer-shell
                    pkgs.gobject-introspection
                    pkgs.pkg-config
                    # Backend tooling: the hyprland and cinnamon backends shell
                    # out to these, so they must be present to be detected.
                    pkgs.hyprland
                    pkgs.wmctrl
                ];

                shellHook = ''
                    export GSETTINGS_SCHEMA_DIR="${pkgs.gtk4}/share/gsettings-schemas/${pkgs.gtk4.name}/glib-2.0/schemas"
                    # gtk4-layer-shell has to be loaded before libwayland-client
                    # or its GDK hooks never install and is_supported() is False.
                    export CONTEXT_LAYER_SHELL_LIB="${pkgs.gtk4-layer-shell}/lib/libgtk4-layer-shell.so"

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
