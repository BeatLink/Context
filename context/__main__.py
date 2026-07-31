from . import sidebar, theme

# Both must happen before GTK loads, so they precede importing app.
theme.pin_gtk_theme()
sidebar.ensure_preloaded()

from .app import main  # noqa: E402

raise SystemExit(main())
