"""Fetch AUR PKGBUILDs upfront for in-GUI review (F3, v0.4.0).

The AUR exposes every package as a git repo at
`https://aur.archlinux.org/<pkg>.git`. Cloning shallow gives us the
PKGBUILD without invoking a helper-specific subcommand — works the same
whether the user has yay, paru, or aurutils as their resolved helper.
git is a transitive dependency of any AUR workflow (base-devel pulls it
in), so this doesn't expand archward's runtime dep tree in practice.

KISS: 30s per-package timeout, return None on any failure. The caller
treats None as "fetch failed" and surfaces that in the modal so the user
can Skip / Retry / Cancel.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_AUR_GIT_BASE = "https://aur.archlinux.org"
_DEFAULT_TIMEOUT_S = 30


def fetch_pkgbuild(pkg: str, *, timeout_s: int = _DEFAULT_TIMEOUT_S) -> str | None:
    """Clone `pkg`'s AUR repo shallowly into a temp dir; return PKGBUILD content.

    Returns None if the clone fails (network error, no such package,
    timeout) or the PKGBUILD is missing. Logs the failure reason but
    never raises — callers expect None as the failure signal.
    """
    url = f"{_AUR_GIT_BASE}/{pkg}.git"
    with tempfile.TemporaryDirectory(prefix=f"archward-pkgbuild-{pkg}-") as td:
        target = Path(td) / pkg
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--quiet", url, str(target)],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except FileNotFoundError:
            log.error("git binary not found; cannot fetch PKGBUILD for %s", pkg)
            return None
        except subprocess.TimeoutExpired:
            log.warning("git clone of %s timed out after %ds", pkg, timeout_s)
            return None
        except subprocess.CalledProcessError as e:
            log.warning("git clone failed for %s: %s", pkg, e.stderr.strip()[:200])
            return None

        pkgbuild = target / "PKGBUILD"
        if not pkgbuild.exists():
            log.warning("no PKGBUILD found in cloned repo for %s", pkg)
            return None
        try:
            return pkgbuild.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("failed reading PKGBUILD for %s: %s", pkg, e)
            return None
