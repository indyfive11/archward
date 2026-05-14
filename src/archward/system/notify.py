"""Desktop notifications via libnotify (`notify-send`).

Used at pipeline completion to surface the RESULT tag without requiring the
user to be looking at the GUI / terminal. Silently no-ops if libnotify isn't
installed — the wrapper checks `shutil.which('notify-send')` and returns
early if not found.

Notification urgency mirrors the severity of the RESULT:
  - SUCCESS / NEEDS_REVIEW          → low      (auto-dismiss)
  - REBOOT_NEEDED / PACNEW_*        → normal   (user setting decides persistence)
  - VERIFY_FAILED / UPDATE_FAILED   → critical (persists until dismissed)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    urgency: str  # "low" | "normal" | "critical"


# RESULT tag → (urgency, title prefix, success-state flag for body framing).
_RESULT_NOTIFY: dict[str, tuple[str, str]] = {
    "RESULT:SUCCESS": ("low", "Update complete"),
    "RESULT:NEEDS_REVIEW": ("low", "Review needed"),
    "RESULT:REBOOT_NEEDED": ("normal", "Reboot required"),
    "RESULT:PACNEW_MERGE_NEEDED": ("normal", "Pacnew merge pending"),
    "RESULT:VERIFY_FAILED": ("critical", "Verify failed"),
    "RESULT:UPDATE_FAILED": ("critical", "Update failed"),
}


def is_available() -> bool:
    """True if `notify-send` is on PATH."""
    return shutil.which("notify-send") is not None


def send_notification(notification: Notification, app_name: str = "Archward", icon: str = "archward") -> bool:
    """Spawn `notify-send`. Returns True if invoked, False if libnotify missing."""
    if not is_available():
        log.debug("notify-send not on PATH; skipping notification")
        return False
    argv = [
        "notify-send",
        "-a", app_name,
        "-i", icon,
        "-u", notification.urgency,
        notification.title,
        notification.body,
    ]
    try:
        subprocess.Popen(argv)
    except OSError as e:
        log.warning("notify-send invocation failed: %s", e)
        return False
    return True


def compose_completion(result) -> Notification | None:
    """Translate a PipelineResult into a Notification, or None if no summary.

    Body lines:
      - the primary tag's human framing (e.g. "Kernel updated. Reboot when convenient.")
      - secondary signal counts (verify FAIL/WARN, AUR build failures)
      - any aborted_reason
    """
    if result is None or result.summary is None:
        return None

    summary = result.summary
    tag = summary.tag
    urgency, title = _RESULT_NOTIFY.get(tag, ("normal", tag))

    lines: list[str] = []

    # Primary signal-specific framing.
    if tag == "RESULT:SUCCESS":
        lines.append("No issues found.")
    elif tag == "RESULT:NEEDS_REVIEW":
        lines.append("Dry-run flagged HIGH-risk packages. Review before updating.")
    elif tag == "RESULT:REBOOT_NEEDED":
        lines.append("Kernel updated. Reboot when convenient.")
    elif tag == "RESULT:PACNEW_MERGE_NEEDED":
        lines.append("New .pacnew files need review.")
    elif tag == "RESULT:VERIFY_FAILED":
        lines.append("Post-update verify reported failures.")
    elif tag == "RESULT:UPDATE_FAILED":
        lines.append(result.aborted_reason or "pacman or pre-flight failed.")

    # Verify counts (if any).
    if summary.fail_count or summary.warn_count:
        lines.append(f"verify: {summary.fail_count} FAIL · {summary.warn_count} WARN")

    # AUR build failures (if any).
    if result.aur and result.aur.failures:
        names = ", ".join(f.package for f in result.aur.failures[:3])
        more = "" if len(result.aur.failures) <= 3 else f" (+{len(result.aur.failures) - 3} more)"
        lines.append(f"AUR build failures: {names}{more}")

    # Secondary tag annotations.
    for sec in summary.secondary_tags:
        _, sec_title = _RESULT_NOTIFY.get(sec, ("normal", sec))
        lines.append(f"also: {sec_title}")

    body = "\n".join(lines) or tag
    return Notification(title=title, body=body, urgency=urgency)


def notify_completion(result, cfg) -> bool:
    """Top-level helper called from CLI and GUI completion paths.

    Returns True if a notification was actually sent.
    """
    if not getattr(cfg.general, "notify_on_completion", True):
        return False
    notification = compose_completion(result)
    if notification is None:
        return False
    return send_notification(notification)
