# Context as a NixOS module.
#
# Writes a declaration into /etc/xdg, which is the first thing Context reads and
# therefore the weakest layer: a home-manager declaration overrides it, and
# anything changed on the settings screen overrides both. That order is the
# point — a system-wide default is a default, not a decree.
#
# Nothing here is forced over Context's own file. The two live in different
# files precisely so neither has to clobber the other.

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
    defaultPriority = 20;
    layerDescription = ''
      Written to `/etc/xdg/context/settings.d/${"\${priority}"}-nixos.json`, the
      weakest layer Context reads.
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
    environment.systemPackages = [ cfg.package ];

    environment.etc = lib.mkMerge [
      (lib.mkIf (shared.declaredSettings cfg.settings != { }) {
        "xdg/context/settings.d/${stem}-nixos.json".source =
          shared.settingsFile cfg.settings;
      })
      (lib.mkIf (cfg.contexts != [ ]) {
        "xdg/context/contexts.d/${stem}-nixos.json".source =
          shared.declaredFile cfg.contexts;
      })
      (lib.mkIf (cfg.style != "") {
        "xdg/context/style.css".text = cfg.style;
      })
    ];

    # Context reads XDG_CONFIG_DIRS for its system layer, and NixOS already puts
    # /etc/xdg there. Named rather than assumed: without it the file above is
    # written and never read, which looks exactly like the module not working.
    environment.sessionVariables.XDG_CONFIG_DIRS = lib.mkDefault [ "/etc/xdg" ];
  };
}
