"""v0.5 preferences package: shim surface guard + parametrized load/dump
round-trip over every config-backed tab.

The round-trip is the guard against silent GUI-no-op fields (the
general.keep_logs class of bug): a field a tab displays but fails to
persist shows up as a dump() mismatch here. Fields a tab deliberately
does not expose stay at their loaded values and round-trip untouched.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from archward.config.defaults import default_config
from archward.models.config import PacnewRule
from archward.models.pacnew import PacnewRecommendation


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ── shim surface guard ────────────────────────────────────────────────────

_SHIM_NAMES = [
    "PreferencesDialog",
    "_Tab", "_GeneralTab", "_GatesTab", "_RiskTab", "_ServicesTab",
    "_PacnewTab", "_AurTab", "_PacmanTab", "_VerifyTab", "_PrivilegeTab",
    "_HooksTab", "_AdvancedTab", "_CacheTab", "_ProfilesTab",
    "QMessageBox", "QInputDialog", "QFileDialog",
    "_lines_to_tuple", "_tuple_to_lines", "_grey_out", "_open_in_editor",
    "_lbl", "_help_label", "_field_with_help", "_section_help", "_wrap",
    "_make_list_edit", "_DEFAULT_ROLE",
]


def test_shim_reexports_full_surface() -> None:
    """Existing tests patch attributes ON this module path; every historical
    name must keep resolving through the shim."""
    import archward.ui.dialogs.preferences as shim

    for name in _SHIM_NAMES:
        assert getattr(shim, name, None) is not None, f"shim lost {name}"


def test_shim_and_package_share_objects() -> None:
    import archward.ui.dialogs.preferences as shim
    from archward.ui.preferences import PreferencesDialog

    assert shim.PreferencesDialog is PreferencesDialog


# ── parametrized round-trip ───────────────────────────────────────────────

# section → field mutations a tab is expected to expose and persist.
_MUTATIONS: dict[str, dict] = {
    "general": dict(keep_snapshots=99, keep_days=33, keep_min=4,
                    after_snapshot=True, keep_logs=7, notify_on_completion=False),
    "gates": dict(snapshot_max_age_minutes=123, min_disk_gb=42,
                  allow_override=False, skip_news_check=True),
    "risk": dict(high=("custompkg",), medium_patterns=("*-custom",),
                 kernel_patterns=("linux-custom*",),
                 kernel_pattern_exclude=("linux-custom-docs*",)),
    "services": dict(to_verify=("sshd.service", "cronie.service"), auto_prune=True),
    "pacnew": dict(
        default_strategy=PacnewRecommendation.TAKE_NEW,
        rules=(PacnewRule(pattern="*custom*",
                          strategy=PacnewRecommendation.KEEP_OURS,
                          note="test rule"),),
    ),
    "aur": dict(enabled=False, helper_preference=("paru",), skip=True,
                quarantine_enabled=False, quarantine_min_failures=5,
                quarantine_initial_days=9, quarantine_max_days=40),
    "pacman": dict(noconfirm=False, extra_args=("--needed",)),
    "verify": dict(enabled=False, reboot_log="/tmp/custom-reboot.log",
                   security_advisories=False, stale_libs=True),
    "privilege": dict(mode="askpass", askpass="/usr/bin/custom-askpass"),
    "hooks": dict(pre_update=("echo pre",), post_verify=("echo post",),
                  timeout_seconds=99, fail_pipeline_on_error=True),
}


def _tab_classes():
    from archward.ui.dialogs import preferences as prefs

    return [
        prefs._GeneralTab, prefs._GatesTab, prefs._RiskTab, prefs._ServicesTab,
        prefs._PacnewTab, prefs._AurTab, prefs._PacmanTab, prefs._VerifyTab,
        prefs._PrivilegeTab, prefs._HooksTab,
    ]


@pytest.mark.parametrize("tab_cls", _tab_classes(), ids=lambda c: c.__name__)
def test_roundtrip_defaults(qapp, tab_cls) -> None:
    cfg = default_config()
    tab = tab_cls()
    tab.load(cfg)
    assert tab.dump() == getattr(cfg, tab.section), (
        f"{tab_cls.__name__} does not round-trip the default [{tab.section}] section"
    )


@pytest.mark.parametrize("tab_cls", _tab_classes(), ids=lambda c: c.__name__)
def test_roundtrip_mutated(qapp, tab_cls) -> None:
    cfg = default_config()
    tab = tab_cls()
    section_name = tab.section
    mutated_section = getattr(cfg, section_name).model_copy(
        update=_MUTATIONS[section_name]
    )
    cfg = cfg.model_copy(update={section_name: mutated_section})
    tab.load(cfg)
    assert tab.dump() == mutated_section, (
        f"{tab_cls.__name__} loses a field of [{section_name}] on load→dump "
        "(silent GUI-no-op)"
    )
