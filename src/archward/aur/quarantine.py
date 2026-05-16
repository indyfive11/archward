"""AUR build quarantine system (v0.4.6).

Tracks packages that repeatedly fail to build and automatically skips them
for a configurable cooling-off period, retrying with escalating backoff.

Key design choices:
- Keyed on package name only; available version determines whether to clear.
- A failure is only counted once per 24h to prevent rapid re-runs from
  inflating the counter (3 counted failures ≈ 3 update sessions / weeks).
- Quarantine activates only after quarantine_min_failures counted failures.
- Escalating backoff: initial_days → 2× → max_days.
- Resolved entries (cleared by new version, retry success, or manual clear)
  are kept in the JSON for history; they have no effect on the pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from archward.config.paths import state_dir
from archward.models.config import AurConfig

log = logging.getLogger(__name__)

_STATE_FILE = "aur_quarantine.json"
_COUNT_GAP_S = 86_400  # 24 h minimum between counted failures


# ── data ──────────────────────────────────────────────────────────────────────

class QuarantineAction(Enum):
    FRESH    = "fresh"    # no entry — try normally
    COUNTING = "counting" # below threshold — try normally (but counting)
    SKIP     = "skip"     # actively quarantined — skip this package
    RETRY    = "retry"    # quarantined but window open — try (log as retry)


@dataclass
class QuarantineEntry:
    version: str
    status: str                  # "counting" | "quarantined" | "resolved"
    first_failure_at: float
    last_failure_at: float
    failure_count: int
    retry_after: float | None    # Unix timestamp; None when not yet quarantined
    retry_interval_days: int
    last_error: str
    resolved_at: float | None
    resolved_reason: str | None  # "new_version" | "manual_clear" | "retry_succeeded"


# ── error classifier ──────────────────────────────────────────────────────────

_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"NU1902|NU1903|NU3028|nuget.*audit|known.*severity.*vulnerabilit", re.I),
        "Upstream PKGBUILD blocked by a dotnet NuGet security audit — "
        "wait for the AUR maintainer to bump the vulnerable dependency.",
    ),
    (
        re.compile(r"(sha256|md5|b2)sums.*FAILED", re.I),
        "Source checksum mismatch — the AUR PKGBUILD may be out of date or "
        "the upstream source changed. Consider flagging the package on AUR.",
    ),
    (
        re.compile(r"Failed to download|curl.*error|Connection (refused|timed out)", re.I),
        "Network error during source download — check connectivity, then "
        "clear quarantine and retry: archward aur quarantine clear <pkg>.",
    ),
    (
        re.compile(r"could not satisfy dependencies|dependency cycle", re.I),
        "Dependency resolution failed — a required package may be missing "
        "or in conflict. Run 'yay -Sua' in a terminal for details.",
    ),
    (
        re.compile(r"failure occurred in (prepare|build|package)\(\)", re.I),
        "makepkg phase failed — run 'yay -Sua' in a terminal for the full "
        "build log.",
    ),
]


def _classify_error(last_lines: tuple[str, ...]) -> str | None:
    """Return a short actionable hint for known error patterns, or None."""
    text = "\n".join(last_lines)
    for pattern, hint in _ERROR_PATTERNS:
        if pattern.search(text):
            return hint
    return None


# ── state I/O ─────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return state_dir() / _STATE_FILE


def _entry_from_dict(d: dict) -> QuarantineEntry:
    return QuarantineEntry(
        version=d["version"],
        status=d.get("status", "counting"),
        first_failure_at=float(d["first_failure_at"]),
        last_failure_at=float(d["last_failure_at"]),
        failure_count=int(d["failure_count"]),
        retry_after=float(d["retry_after"]) if d.get("retry_after") is not None else None,
        retry_interval_days=int(d.get("retry_interval_days", 7)),
        last_error=d.get("last_error", ""),
        resolved_at=float(d["resolved_at"]) if d.get("resolved_at") is not None else None,
        resolved_reason=d.get("resolved_reason"),
    )


def _entry_to_dict(e: QuarantineEntry) -> dict:
    return asdict(e)


# ── main class ────────────────────────────────────────────────────────────────

class AurQuarantine:
    """Version-aware, timed AUR build quarantine.

    Lifecycle:
        q = AurQuarantine(cfg.aur)
        q.load()
        action, entry = q.check(pkg, available_version)
        # … run update …
        q.record_failure(pkg, version, last_lines)   # or record_success
        q.save()
    """

    def __init__(self, cfg: AurConfig) -> None:
        self._cfg = cfg
        self._data: dict[str, QuarantineEntry] = {}

    # ── I/O ───────────────────────────────────────────────────────────────────

    def load(self) -> None:
        path = _state_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._data = {pkg: _entry_from_dict(d) for pkg, d in raw.items()}
        except Exception as exc:
            log.warning("aur_quarantine: could not load state (%s) — starting fresh", exc)
            self._data = {}

    def save(self) -> None:
        path = _state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {pkg: _entry_to_dict(e) for pkg, e in self._data.items()}
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("aur_quarantine: could not save state: %s", exc)

    # ── query ─────────────────────────────────────────────────────────────────

    def check(self, pkg: str, available_version: str) -> tuple[QuarantineAction, QuarantineEntry | None]:
        """Return the action archward should take for this pending package.

        Also auto-clears stale entries when a new version is available.
        """
        if not self._cfg.quarantine_enabled:
            return QuarantineAction.FRESH, None

        entry = self._data.get(pkg)
        if entry is None or entry.status == "resolved":
            return QuarantineAction.FRESH, entry

        # New version available — clear old quarantine entry, try fresh
        if entry.version != available_version:
            log.info("quarantine: %s new version %s available — clearing quarantine for %s",
                     pkg, available_version, entry.version)
            resolved = _copy_resolved(entry, "new_version")
            self._data[pkg] = resolved
            return QuarantineAction.FRESH, resolved

        if entry.status == "counting":
            return QuarantineAction.COUNTING, entry

        # status == "quarantined"
        now = time.time()
        if entry.retry_after is not None and now >= entry.retry_after:
            return QuarantineAction.RETRY, entry
        return QuarantineAction.SKIP, entry

    def entry(self, pkg: str) -> QuarantineEntry | None:
        return self._data.get(pkg)

    def entries(self) -> list[tuple[str, QuarantineEntry]]:
        """All entries sorted: quarantined first, then counting, then resolved."""
        order = {"quarantined": 0, "counting": 1, "resolved": 2}
        return sorted(self._data.items(), key=lambda kv: order.get(kv[1].status, 3))

    def active_entries(self) -> list[tuple[str, QuarantineEntry]]:
        """Only counting + quarantined entries (affect the pipeline)."""
        return [(p, e) for p, e in self.entries() if e.status != "resolved"]

    # ── mutation ──────────────────────────────────────────────────────────────

    def record_failure(
        self,
        pkg: str,
        version: str,
        last_lines: tuple[str, ...],
    ) -> bool:
        """Record a build failure. Returns True when quarantine just activated."""
        if not self._cfg.quarantine_enabled:
            return False

        now = time.time()
        error_summary = _first_error_line(last_lines)
        entry = self._data.get(pkg)

        if entry is None or entry.status == "resolved" or entry.version != version:
            # Fresh tracking entry for this version
            self._data[pkg] = QuarantineEntry(
                version=version,
                status="counting",
                first_failure_at=now,
                last_failure_at=now,
                failure_count=1,
                retry_after=None,
                retry_interval_days=self._cfg.quarantine_initial_days,
                last_error=error_summary,
                resolved_at=None,
                resolved_reason=None,
            )
            return False

        # Existing entry — only count if ≥ 24h since last counted failure
        if now - entry.last_failure_at < _COUNT_GAP_S:
            log.debug("quarantine: %s failure within 24h window — not counting again", pkg)
            return False

        new_count = entry.failure_count + 1
        entry.last_failure_at = now
        entry.failure_count = new_count
        entry.last_error = error_summary

        just_activated = False
        if entry.status == "counting" and new_count >= self._cfg.quarantine_min_failures:
            # Threshold reached — activate quarantine
            entry.status = "quarantined"
            entry.retry_after = now + entry.retry_interval_days * 86_400
            just_activated = True
            # Emit full build log to rotating log (Option B)
            log.warning(
                "quarantine: %s quarantined after %d failures — "
                "last build output:\n%s",
                pkg, new_count, "\n".join(last_lines),
            )
        elif entry.status == "quarantined":
            # Retry window failure — escalate backoff
            new_interval = min(
                entry.retry_interval_days * 2,
                self._cfg.quarantine_max_days,
            )
            entry.retry_interval_days = new_interval
            entry.retry_after = now + new_interval * 86_400
            log.warning(
                "quarantine: %s retry failed — backoff escalated to %d days",
                pkg, new_interval,
            )

        self._data[pkg] = entry
        return just_activated

    def record_success(self, pkg: str) -> None:
        """Clear a package's quarantine entry on successful build."""
        entry = self._data.get(pkg)
        if entry is None or entry.status == "resolved":
            return
        self._data[pkg] = _copy_resolved(entry, "retry_succeeded")
        log.info("quarantine: %s built successfully — cleared from quarantine", pkg)

    def clear(self, pkg: str | None = None) -> int:
        """Mark entries as resolved. Returns count of entries affected.

        pkg=None clears all active (counting + quarantined) entries.
        """
        now = time.time()
        if pkg is not None:
            entry = self._data.get(pkg)
            if entry is None or entry.status == "resolved":
                return 0
            self._data[pkg] = _copy_resolved(entry, "manual_clear")
            return 1

        count = 0
        for p, e in list(self._data.items()):
            if e.status != "resolved":
                self._data[p] = _copy_resolved(e, "manual_clear")
                count += 1
        return count

    def remove_resolved(self) -> int:
        """Delete all resolved entries from state. Returns count removed."""
        to_delete = [p for p, e in self._data.items() if e.status == "resolved"]
        for p in to_delete:
            del self._data[p]
        return len(to_delete)

    def update_entry(self, pkg: str, patch: dict) -> None:
        """Apply a partial patch to an entry (for Preferences UI edits).

        Supported patch keys: failure_count (int), retry_after (float|None),
        status (str).
        """
        entry = self._data.get(pkg)
        if entry is None:
            return
        if "failure_count" in patch:
            entry.failure_count = int(patch["failure_count"])
        if "retry_after" in patch:
            entry.retry_after = patch["retry_after"]  # float timestamp or None
        if "status" in patch:
            new_status = patch["status"]
            entry.status = new_status
            if new_status == "resolved":
                entry.resolved_at = time.time()
                entry.resolved_reason = "manual_clear"
                entry.retry_after = None
        self._data[pkg] = entry


# ── helpers ───────────────────────────────────────────────────────────────────

def _copy_resolved(entry: QuarantineEntry, reason: str) -> QuarantineEntry:
    return QuarantineEntry(
        version=entry.version,
        status="resolved",
        first_failure_at=entry.first_failure_at,
        last_failure_at=entry.last_failure_at,
        failure_count=entry.failure_count,
        retry_after=None,
        retry_interval_days=entry.retry_interval_days,
        last_error=entry.last_error,
        resolved_at=time.time(),
        resolved_reason=reason,
    )


def _first_error_line(last_lines: tuple[str, ...]) -> str:
    """Extract the most informative single error line for storage."""
    for line in last_lines:
        stripped = line.strip()
        if stripped.startswith("==> ERROR:") or "error " in stripped.lower():
            return stripped[:200]
    # Fallback: last non-empty line
    for line in reversed(last_lines):
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""
