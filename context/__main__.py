from context.ui import sidebar

# Must happen before GTK and libwayland are loaded, so it precedes importing app.
sidebar.ensure_preloaded()

from context.app import main  # noqa: E402

raise SystemExit(main())
