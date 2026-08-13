"""Compatibility shim (v0.5) — Preferences now lives in the
`archward.ui.preferences` package (one module per tab + dialog.py).

This module re-exports the full historical surface so existing imports and
test monkeypatch targets (`archward.ui.dialogs.preferences.QMessageBox.warning`
et al.) keep resolving. New code should import from `archward.ui.preferences`.
"""

from __future__ import annotations

# Qt classes kept as module attributes — established monkeypatch targets.
from PySide6.QtWidgets import (  # noqa: F401
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from archward.ui.preferences._common import (  # noqa: F401
    _field_with_help,
    _grey_out,
    _help_label,
    _lbl,
    _lines_to_tuple,
    _make_list_edit,
    _open_in_editor,
    _section_help,
    _tuple_to_lines,
    _wrap,
)
from archward.ui.preferences.advanced import _AdvancedTab  # noqa: F401
from archward.ui.preferences.aur import _AurTab  # noqa: F401
from archward.ui.preferences.cache import _CacheTab  # noqa: F401
from archward.ui.preferences.dialog import PreferencesDialog  # noqa: F401
from archward.ui.preferences.gates import _GatesTab  # noqa: F401
from archward.ui.preferences.general import _GeneralTab  # noqa: F401
from archward.ui.preferences.hooks import _HooksTab  # noqa: F401
from archward.ui.preferences.pacman import _PacmanTab  # noqa: F401
from archward.ui.preferences.pacnew import _PacnewTab  # noqa: F401
from archward.ui.preferences.privilege import _PrivilegeTab  # noqa: F401
from archward.ui.preferences.profiles import _DEFAULT_ROLE, _ProfilesTab  # noqa: F401
from archward.ui.preferences.risk import _RiskTab  # noqa: F401
from archward.ui.preferences.services import _ServicesTab  # noqa: F401
from archward.ui.preferences.tabs_base import _Tab  # noqa: F401
from archward.ui.preferences.verify import (  # noqa: F401
    _STALE_LIBS_SCAN_SCRIPT,
    _STALE_LIBS_SUDOERS_LINE,
    _STALE_LIBS_SUDOERS_PATH,
    _VerifyTab,
)
