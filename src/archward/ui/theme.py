"""Theme-aware status colors for the archward GUI.

Why this exists
---------------
v0.1.0–0.1.3 used hard-coded hex literals (e.g. `#155724` dark green,
`#fff3cd` light amber, `#f8d7da` light pink) for status indicators. Those
read fine on Breeze / Adwaita light themes but render as near-black on dark
green or near-white on dark backgrounds when the user runs Breeze Dark,
Adwaita Dark, etc. v0.1.4 introduces this module so every view consumes
colors from one place; light vs dark detection happens at widget
construction via the active `QApplication.palette()`.

Detection
---------
`is_dark_theme()` reads the active palette's Window color and computes its
YIQ luminance (`0.299 R + 0.587 G + 0.114 B`). Below 128 → dark theme. This
is the standard heuristic used by most theming code; works correctly for
all the major Qt themes we tested against (Breeze, Breeze Dark, Adwaita,
Adwaita Dark, Oxygen, Fusion).

Theme change at runtime
-----------------------
Views call `status_palette()` at widget construction time. Switching system
theme mid-session won't repaint the views — restart archward to pick up the
new colors. Reactive theming is a v2 nicety, not v1.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class StatusPalette:
    """All theme-varying colors used by archward views.

    Foreground colors are `QColor` — applied via `QTreeWidgetItem.setForeground()`.
    Background pairs are CSS strings — applied via Qt stylesheet on banners.
    """

    # ── Status tree-item foregrounds ────────────────────────────────────
    pass_fg: QColor
    warn_fg: QColor
    fail_fg: QColor
    skipped_fg: QColor

    # ── Risk level foregrounds ──────────────────────────────────────────
    high_fg: QColor
    medium_fg: QColor
    kernel_fg: QColor

    # ── Pacnew recommendation foregrounds ───────────────────────────────
    keep_ours_fg: QColor
    take_new_fg: QColor
    review_needed_fg: QColor

    # ── Result banner bg/fg pairs (CSS strings) ─────────────────────────
    success_bg: str
    success_fg: str
    info_bg: str       # for REBOOT_NEEDED / PACNEW / NEEDS_REVIEW
    info_fg: str
    danger_bg: str     # for VERIFY_FAILED / UPDATE_FAILED
    danger_fg: str
    neutral_bg: str    # default fallback
    neutral_fg: str

    # ── Risk-view transaction-preview banner ────────────────────────────
    preview_warning_bg: str
    preview_warning_border: str
    preview_warning_fg: str

    # ── Diff highlighter ────────────────────────────────────────────────
    diff_add_bg: QColor
    diff_add_fg: QColor
    diff_del_bg: QColor
    diff_del_fg: QColor
    diff_hunk_fg: QColor
    diff_header_fg: QColor


# Light theme — sourced from Bootstrap 5 "alert" tokens, which are also what
# the v0.1.0 hand-coded literals approximated.
_LIGHT = StatusPalette(
    pass_fg=QColor(80, 180, 100),
    warn_fg=QColor(220, 170, 60),
    fail_fg=QColor(220, 70, 70),
    skipped_fg=QColor(160, 160, 160),
    high_fg=QColor(220, 70, 70),
    medium_fg=QColor(220, 170, 60),
    kernel_fg=QColor(245, 130, 60),
    keep_ours_fg=QColor(80, 180, 100),
    take_new_fg=QColor(80, 130, 200),
    review_needed_fg=QColor(220, 170, 60),
    success_bg="#d4edda",
    success_fg="#155724",
    info_bg="#fff3cd",
    info_fg="#856404",
    danger_bg="#f8d7da",
    danger_fg="#721c24",
    neutral_bg="#e2e3e5",
    neutral_fg="#383d41",
    preview_warning_bg="#fff3cd",
    preview_warning_border="#ffeeba",
    preview_warning_fg="#856404",
    diff_add_bg=QColor("#d4edda"),
    diff_add_fg=QColor("#155724"),
    diff_del_bg=QColor("#f8d7da"),
    diff_del_fg=QColor("#721c24"),
    diff_hunk_fg=QColor("#6c757d"),
    diff_header_fg=QColor("#383d41"),
)

# Dark theme — brighter foregrounds (saturated, not pastel) so they pop
# against dark surfaces; banner backgrounds are dark-tinted instead of
# pale-pastel; diff add/del use desaturated darker tints behind bright text.
_DARK = StatusPalette(
    pass_fg=QColor(126, 218, 147),       # #7eda93 bright green
    warn_fg=QColor(255, 204, 64),        # #ffcc40 gold
    fail_fg=QColor(255, 107, 107),       # #ff6b6b bright red
    skipped_fg=QColor(144, 144, 144),    # #909090 muted gray
    high_fg=QColor(255, 120, 120),       # #ff7878
    medium_fg=QColor(255, 204, 64),
    kernel_fg=QColor(255, 157, 77),      # #ff9d4d
    keep_ours_fg=QColor(126, 218, 147),
    take_new_fg=QColor(110, 165, 235),   # #6ea5eb
    review_needed_fg=QColor(255, 204, 64),
    success_bg="#1e3a25",
    success_fg="#7eda93",
    info_bg="#3a3015",
    info_fg="#ffd95c",
    danger_bg="#3a1e1e",
    danger_fg="#ff8080",
    neutral_bg="#2b2e30",
    neutral_fg="#c8c8c8",
    preview_warning_bg="#3a3015",
    preview_warning_border="#5a4d20",
    preview_warning_fg="#ffd95c",
    diff_add_bg=QColor("#1e3a25"),
    diff_add_fg=QColor("#7eda93"),
    diff_del_bg=QColor("#3a1e1e"),
    diff_del_fg=QColor("#ff8080"),
    diff_hunk_fg=QColor("#909090"),
    diff_header_fg=QColor("#c8c8c8"),
)


@dataclass(frozen=True)
class BrandPalette:
    """archward brand colors — sampled from the shield icon SVG.

    The light/dark split keeps contrast usable on both Breeze and Breeze
    Dark (and equivalents). The accent foreground is the saturated teal
    on light themes; on dark themes we shift to the lighter cyan that
    pops against dark surfaces. Tints are CSS rgba so they degrade
    gracefully when applied via stylesheet to widgets that don't accept
    QColor directly.
    """

    accent_fg: QColor       # primary — used for borders, group labels, highlight
    accent_strong: QColor   # higher contrast — outlines, focused borders
    accent_bg_tint: str     # CSS rgba — faint background fill
    accent_border: str      # CSS color — for stylesheet `border` rules
    accent_text_css: str    # CSS color — for stylesheet `color` rules


_BRAND_LIGHT = BrandPalette(
    accent_fg=QColor("#0e7490"),
    accent_strong=QColor("#083344"),
    accent_bg_tint="rgba(14, 116, 144, 0.10)",
    accent_border="#0e7490",
    accent_text_css="#0e7490",
)

_BRAND_DARK = BrandPalette(
    accent_fg=QColor("#14b8c4"),
    accent_strong=QColor("#5be4ed"),
    accent_bg_tint="rgba(20, 184, 196, 0.18)",
    accent_border="#14b8c4",
    accent_text_css="#14b8c4",
)


# Brand-tinted success banner (RESULT:SUCCESS) — supersedes the generic
# green from StatusPalette so a clean run carries the archward colors.
# Light theme uses a soft teal-on-pale-teal; dark uses bright cyan on a
# darker teal so it pops against Breeze Dark / Adwaita Dark.
BRAND_SUCCESS_BG_LIGHT = "#d4eef2"
BRAND_SUCCESS_FG_LIGHT = "#083344"
BRAND_SUCCESS_BG_DARK = "#0e3a44"
BRAND_SUCCESS_FG_DARK = "#5be4ed"


def brand_success_colors() -> tuple[str, str]:
    """Return (bg, fg) CSS strings for the brand-themed success banner."""
    if is_dark_theme():
        return BRAND_SUCCESS_BG_DARK, BRAND_SUCCESS_FG_DARK
    return BRAND_SUCCESS_BG_LIGHT, BRAND_SUCCESS_FG_LIGHT


def is_dark_theme() -> bool:
    """True when the active QPalette's Window color is dark.

    Returns False if no QApplication exists yet (importing this module before
    QApplication construction shouldn't trip the rest of the GUI).
    """
    app = QApplication.instance()
    if app is None:
        return False
    window = app.palette().color(QPalette.ColorRole.Window)
    # YIQ luminance via integer math so RGB(128,128,128) lands exactly at the
    # threshold without float rounding (0.299+0.587+0.114 isn't exactly 1.0
    # in float64; 128 × that sum was rounding to 127.999... → false-dark).
    luminance = (window.red() * 299 + window.green() * 587 + window.blue() * 114) // 1000
    return luminance < 128


def status_palette() -> StatusPalette:
    """Return the StatusPalette appropriate for the active theme."""
    return _DARK if is_dark_theme() else _LIGHT


def brand_palette() -> BrandPalette:
    """Return the BrandPalette appropriate for the active theme.

    Sampled from packaging/archward.svg:
      - light theme uses the shield's primary teal (#0e7490)
      - dark theme uses the inner edge cyan (#14b8c4) for better contrast
    """
    return _BRAND_DARK if is_dark_theme() else _BRAND_LIGHT
