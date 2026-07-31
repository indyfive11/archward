"""Bundled Qt askpass — archward's guaranteed password-prompt fallback.

Installed as the ``archward-askpass`` console script. sudo invokes it via
``$SUDO_ASKPASS`` with the prompt text as argv[1]; the entered password is
written to stdout. Exit 0 on entry, non-zero on cancel or when no graphical
session is available.

Rationale: fresh installs on non-KDE desktops (Cinnamon, GNOME, Xfce, …)
ship no native askpass binary, and every external option is either a heavy
foreign-toolkit dependency (zenity → GTK4 + libadwaita) or visually ancient
(x11-ssh-askpass). archward already hard-depends on PySide6, so it can
always provide its own prompt with zero additional dependencies.
"""

from __future__ import annotations

import os
import sys

_DEFAULT_PROMPT = "[sudo] password:"


def _ask(prompt: str) -> str | None:
    """Show a modal Qt password dialog; return the entry, or None on cancel."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

    app = QApplication(sys.argv[:1])
    app.setApplicationName("archward-askpass")
    dialog = QInputDialog()
    dialog.setWindowTitle("archward")
    dialog.setLabelText(prompt)
    dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    accepted = dialog.exec() == QInputDialog.DialogCode.Accepted
    return dialog.textValue() if accepted else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    prompt = args[0] if args and args[0].strip() else _DEFAULT_PROMPT
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("archward-askpass: no graphical display", file=sys.stderr)
        return 1
    try:
        password = _ask(prompt)
    except ImportError:
        print("archward-askpass: PySide6 not available", file=sys.stderr)
        return 1
    if password is None:
        return 1
    print(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
