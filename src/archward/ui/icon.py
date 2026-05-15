"""archward window-icon loader.

Resolves the application icon in priority order:

1. **Freedesktop icon theme** (`QIcon.fromTheme("archward")`) — when the
   AUR package is installed, the SVG lives at
   `/usr/share/icons/hicolor/scalable/apps/archward.svg` and this lookup
   wins. This is the cheapest path and matches what the .desktop
   launcher uses.
2. **Bundled package resource** — fallback for `pip install`-only
   environments where no system icon theme entry exists. The SVG ships
   inside the wheel at `archward/data/archward.svg`.
3. **Empty `QIcon()`** — last resort. Qt falls back to whatever the
   compositor uses for windowless apps.

Keeping the resolution logic here (rather than scattering across cli.py +
main_window.py) means tests can stub one place if needed and the rule
order stays single-sourced.
"""

from __future__ import annotations

import logging
from importlib import resources

from PySide6.QtGui import QIcon

log = logging.getLogger(__name__)

_ICON_NAME = "archward"
_BUNDLED_RESOURCE = ("archward.data", "archward.svg")


def archward_icon() -> QIcon:
    """Return the archward window icon. Always returns a QIcon (possibly empty)."""
    themed = QIcon.fromTheme(_ICON_NAME)
    if not themed.isNull():
        return themed

    try:
        ref = resources.files(_BUNDLED_RESOURCE[0]).joinpath(_BUNDLED_RESOURCE[1])
        with resources.as_file(ref) as path:
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    except (FileNotFoundError, ModuleNotFoundError) as e:
        log.warning("bundled archward.svg not loadable: %s", e)

    log.warning("falling back to empty QIcon — taskbar will show Qt's default")
    return QIcon()
