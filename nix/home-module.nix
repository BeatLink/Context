# Context as a home-manager module.
#
# Writes a declaration into the config directory as a drop-in — never as
# `settings.json` itself, which is Context's own file. That is the whole
# difference from the obvious implementation and the reason this module can
# coexist with the settings screen: two files, read in order, rather than one
# that home-manager and Context take turns clobbering.
#
# The layer sits above a NixOS declaration and below anything changed in the
# launcher.

self:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.programs.context;
  shared = import ./options.nix { inherit lib pkgs; };
  stem = shared.stem cfg.priority;
in
{
  options.programs.context = shared.common {
    defaultPriority = 50;
    layerDescription = ''
      Written to `''${XDG_CONFIG_HOME}/context/settings.d/${"\${priority}"}-home.json`,
      above any NixOS declaration and below the settings screen.
    '';
  } // {
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.context;
      defaultText = lib.literalExpression "context.packages.\${system}.context";
      description = "The Context package to use.";
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile = lib.mkMerge [
      (lib.mkIf (shared.declaredSettings cfg.settings != { }) {
        "context/settings.d/${stem}-home.json".source = shared.settingsFile cfg.settings;
      })
      (lib.mkIf (cfg.contexts != [ ]) {
        "context/contexts.d/${stem}-home.json".source = shared.declaredFile cfg.contexts;
      })
      (lib.mkIf (cfg.style != "") {
        # Not forced, and it does not need to be: Context writes settings.json,
        # never style.css, so home-manager's symlink is never replaced by a real
        # file behind its back.
        "context/style.css".text = cfg.style;
      })
    ];
  };
}
