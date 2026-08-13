"""archward Preferences (v0.5 package split).

One module per tab + dialog.py; shared helpers in _common.py;
the old import path archward.ui.dialogs.preferences is a shim.
"""

from archward.ui.preferences.dialog import PreferencesDialog

__all__ = ["PreferencesDialog"]
