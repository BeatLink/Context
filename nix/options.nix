# The options both modules share.
#
# There is one set of settings and one shape of declared context; whether they
# are written to /etc or into a home directory is the only difference between
# the NixOS module and the home-manager one, so everything else lives here.
#
# **Every setting is `null` until it is set, and nulls are not written.** This
# is the part that makes the layers work rather than merely exist. An option
# with an ordinary Nix default always has a value, so the module would write a
# complete settings file — and a complete file at one layer overrides every
# layer beneath it. Enabling the home-manager module would then silently undo a
# NixOS declaration, not because anything was configured, but because both files
# named every key. Unset means absent, absent means "whatever the layer below
# said", and that is the whole contract.
#
# The cost is that the defaults live in Context rather than here, so each
# description says what not setting it means.

{ lib, pkgs }:

let
  jsonType = (pkgs.formats.json { }).type;

  # A setting: documented and validated, but genuinely optional.
  setting =
    type: contextDefault: description:
    lib.mkOption {
      type = lib.types.nullOr type;
      default = null;
      description = ''
        ${description}

        Left unset, Context's own default applies (${contextDefault}) — or
        whatever an earlier layer declared.
      '';
    };

  t = lib.types;
in
rec {
  # A context as Context's own declaration file wants it. Everything but the
  # title is optional: a context with no layout is given the preset for however
  # many applications it holds the first time it opens.
  contextType = t.submodule {
    options = {
      title = lib.mkOption {
        type = t.str;
        description = "What you are doing — the name the launcher lists it under.";
      };
      apps = lib.mkOption {
        type = t.listOf t.str;
        default = [ ];
        example = [
          "firefox.desktop"
          "codium.desktop"
        ];
        description = "Desktop entry ids, in the order they should tile.";
      };
      urls = lib.mkOption {
        type = t.attrsOf (t.listOf t.str);
        default = { };
        example = {
          "firefox.desktop" = [ "https://github.com" ];
        };
        description = "What each application opens, by desktop entry id.";
      };
      isolated = lib.mkOption {
        type = t.bool;
        default = false;
        description = "Launch its applications under a private session bus.";
      };
    };
  };

  # Freeform underneath, so a setting added to Context is usable here the day it
  # lands rather than after this file is taught about it. The named options are
  # the ones worth spelling out: a default worth knowing, or a set of values
  # worth documenting.
  settingsType = t.submodule {
    freeformType = jsonType;
    options = {
      sidebar_edge = setting (t.enum [ "left" "right" "top" "bottom" ]) "`left`"
        "Which side of the screen the launcher docks to.";

      monitor = setting t.str "the compositor's choice" ''
        Which output it docks to. Empty leaves the choice to the compositor,
        `*` puts a launcher on every screen, anything else is a connector name.
      '';

      screen_order = setting (t.listOf t.str) "left to right" ''
        Which monitor is screen 1, screen 2, and so on. This is the whole of
        Context's screen identity — contexts themselves only ever say
        "screen 1", so moving a cable is a change here rather than in every
        context.
      '';

      max_screens = setting (t.ints.between 1 4) "2"
        "How many screen counts a context can hold a separate layout for.";

      sidebar_width = setting (t.ints.between 200 1200) "380"
        "Pixels the expanded launcher reserves.";

      rail_width = setting (t.ints.between 36 160) "56"
        "Pixels the collapsed rail reserves.";

      collapse_mode = setting (t.enum [ "rail" "hidden" "none" ]) "`rail`" ''
        What collapsing does: shrink to a rail of icons, hide entirely behind a
        sliver to hover over, or offer no collapsing at all.
      '';

      auto_expand = setting t.bool "false"
        "Open the launcher while the pointer is over it.";

      auto_expand_delay_ms = setting (t.ints.between 0 2000) "120"
        "How long to hover before it expands.";

      collapse_delay_ms = setting (t.ints.between 0 5000) "400"
        "How long it stays open after the pointer leaves its zone.";

      save_prompt = setting (t.enum [ "never" "change" "switch" "close" ]) "`close`"
        "When to offer to keep a context that has drifted from what was saved.";

      notifications = setting t.bool "true"
        "Report launches, closes and drift to the notification daemon.";

      show_search = setting t.bool "true" "Show the sidebar's search box.";

      show_new_context = setting t.bool "true"
        "Show the row that starts a context.";

      show_saved = setting t.bool "true"
        "Show saved contexts beneath the open ones.";

      show_save_button = setting t.bool "true"
        "Show the save button on a context's row.";

      show_restore_button = setting t.bool "true"
        "Show the put-the-windows-back button on a context's row.";

      show_add_app_button = setting t.bool "true"
        "Show the open-an-app-here button on a context's row.";

      show_close_button = setting t.bool "true"
        "Show the close button on a context's row.";

      show_edit_button = setting t.bool "true"
        "Show the edit button on a context's row.";

      show_apps = setting t.bool "true"
        "Show matching applications under the search results.";

      context_sort = setting (t.enum [ "recent" "created" "name" ]) "`recent`" ''
        How the context list is ordered, in the sidebar, the overview and the
        rail alike: by when each was last opened, by when it was made, or by
        name.
      '';

      overview_sort = setting (t.enum [ "recent" "name" "kind" "contexts" ]) "`recent`"
        "How the overview's application grid is ordered each time it opens.";

      scratchpad = setting t.bool "true"
        "Somewhere to type in the sidebar.";

      scratchpad_global = setting t.bool "true"
        "One scratchpad that is there wherever you are.";

      scratchpad_per_context = setting t.bool "true"
        "One scratchpad for each context, shown while you are in it.";

      show_notes = setting t.bool "true"
        "Show the scratchpad in the sidebar's narrow list.";

      scratchpad_show_both = setting t.bool "false" ''
        Show the global scratchpad and the context's stacked, rather than one
        at a time behind a switch.
      '';

      scratchpad_height = setting (t.ints.between 60 600) "132"
        "How tall the sidebar's writing area is, in pixels, per scratchpad.";

      poll_seconds = setting (t.ints.between 1 60) "2"
        "How often the open list is re-checked against the compositor.";

      log_level = setting (t.enum [ "debug" "info" "warning" "error" "critical" ]) "`info`"
        "How much detail Context writes to its log.";

      backend = setting (t.enum [ "auto" "hyprland" "none" ]) "`auto`"
        "Which window manager drives workspaces.";
    };
  };

  # What actually gets written: the settings that were set, and nothing else.
  declaredSettings = settings: lib.filterAttrs (_: value: value != null) settings;

  # The options every Context module has, whatever it writes them to.
  common =
    { defaultPriority, layerDescription }:
    {
      enable = lib.mkEnableOption "Context, a context-oriented desktop shell";

      priority = lib.mkOption {
        type = t.ints.between 0 99;
        default = defaultPriority;
        description = ''
          Where this module's file sits in Context's load order. Files are read
          in name order and the last one to mention a setting decides it, so a
          higher number wins over a lower one.

          Nothing written here ever outranks the settings screen, whose file is
          always read last.
        '';
      };

      settings = lib.mkOption {
        type = settingsType;
        default = { };
        description = ''
          Settings to declare. ${layerDescription}

          This is a *layer*, not the whole of Context's configuration. Settings
          left out here are not written at all, so they keep whatever an earlier
          layer said; and anything changed on Context's own settings screen wins
          over everything declared, because the file Context writes is read last.

          "Reset your changes" on the settings screen drops what was changed
          there and lets these values apply again.
        '';
      };

      contexts = lib.mkOption {
        type = t.listOf contextType;
        default = [ ];
        description = ''
          Contexts to hand Context the first time it sees them. Each is taken in
          once and is an ordinary context from then on, so editing or forgetting
          one in the launcher is not undone at the next start.
        '';
      };

      style = lib.mkOption {
        type = t.lines;
        default = "";
        example = ''
          @define-color ctx_accent #5ac0c0;
        '';
        description = ''
          CSS loaded over Context's built-in stylesheet. Redefining
          `@define-color ctx_*` names is the whole theming contract.
        '';
      };
    };

  # Contexts as the declaration file wants them: resources rather than a flat
  # app list, since that is what Context has read since adapters landed.
  declaredFile =
    contexts:
    (pkgs.formats.json { }).generate "context-contexts.json" {
      contexts = map (ctx: {
        inherit (ctx) title isolated;
        resources = map (app: {
          app_id = app;
          urls = ctx.urls.${app} or [ ];
        }) ctx.apps;
      }) contexts;
    };

  settingsFile =
    settings:
    (pkgs.formats.json { }).generate "context-settings.json" (declaredSettings settings);

  # Two digits so name order and numeric order agree: 9 sorting after 10 is the
  # classic way a load order stops meaning what it says.
  stem = priority: lib.fixedWidthNumber 2 priority;
}
