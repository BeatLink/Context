from . import sidebar

# Must happen before GTK and libwayland are loaded, so it precedes importing app.
sidebar.ensure_preloaded()

from .app import main  # noqa: E402

raise SystemExit(main())
