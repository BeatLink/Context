# Context, packaged.
#
# A GTK4 application written in Python, so the interesting parts are the two
# things a plain `python3 -m context` in a shell gets for free and a package
# does not: the introspection typelibs GTK is reached through, and the
# layer-shell library the sidebar needs loaded before libwayland.
#
# `hyprctl` is deliberately not a dependency. The Hyprland backend shells out to
# it, but it is part of the running compositor rather than of Context — pulling a
# whole compositor into this closure to talk to the one already running would be
# absurd, and `backends.detect()` falls back to NullBackend when it is absent.

{
  lib,
  stdenvNoCC,
  python3,
  gtk4,
  gtk4-layer-shell,
  glib,
  gobject-introspection,
  wrapGAppsHook4,
  makeWrapper,
  version ? "0.1.0",
}:

let
  python = python3.withPackages (ps: [
    ps.pygobject3
    ps.pycairo
  ]);
in
stdenvNoCC.mkDerivation {
  pname = "context";
  inherit version;

  src = lib.cleanSourceWith {
    src = ../.;
    filter =
      path: type:
      let
        name = baseNameOf path;
      in
      !(builtins.elem name [
        "__pycache__"
        ".pytest_cache"
        ".git"
        "result"
      ]);
  };

  nativeBuildInputs = [
    wrapGAppsHook4
    gobject-introspection
    makeWrapper
  ];

  # No libadwaita: `widgets.py` is a set of plain-GTK stand-ins for the Adwaita
  # widgets Context used to use, and nothing imports Adw any more. The test
  # suite still does, which is why the flake's check has it and this does not.
  buildInputs = [
    gtk4
    gtk4-layer-shell
    glib
  ];

  dontBuild = true;
  # The binary is a wrapper this file writes, so the hook's own wrapping would
  # wrap a wrapper. `gappsWrapperArgs` carries what it would have added — but it
  # is filled in by the hook's own preFixup, so the wrapper has to be built
  # there and not in installPhase, where the array is still empty. Built in
  # installPhase, the wrapper came out with GIO_EXTRA_MODULES and nothing else:
  # no GI_TYPELIB_PATH, so importing Gtk failed outside a dev shell.
  dontWrapGApps = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/${python.sitePackages}
    cp -r context $out/${python.sitePackages}/

    install -Dm444 /dev/stdin $out/share/applications/io.beatlink.Context.desktop <<'DESKTOP'
    [Desktop Entry]
    Type=Application
    Name=Context
    Comment=A context-oriented desktop shell
    Exec=context
    Icon=view-grid-symbolic
    Categories=Utility;
    StartupNotify=false
    DESKTOP

    runHook postInstall
  '';

  preFixup = ''
    makeWrapper ${python}/bin/python3 $out/bin/context \
      "''${gappsWrapperArgs[@]}" \
      --add-flags "-m context" \
      --prefix PYTHONPATH : "$out/${python.sitePackages}" \
      --set-default CONTEXT_LAYER_SHELL_LIB \
        "${gtk4-layer-shell}/lib/libgtk4-layer-shell.so"
  '';

  meta = {
    description = "A context-oriented desktop shell";
    longDescription = ''
      Rather than managing windows and workspaces, you work in contexts: named
      groups of applications opened to specific things, each focused on doing
      one job.
    '';
    homepage = "https://github.com/BeatLink/Context";
    # The repository carries the GPLv3 text with no "or any later version"
    # anywhere, so this claims only what is actually stated.
    license = lib.licenses.gpl3Only;
    mainProgram = "context";
    platforms = lib.platforms.linux;
  };
}
