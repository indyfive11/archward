"""Pacnew discovery, classification, diff rendering, action application.

Per audit C1: take_new must preserve original file ownership and mode.
"""

from __future__ import annotations

import difflib
import fnmatch
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from archward.models.config import PacnewConfig
from archward.models.pacnew import PacnewAction, PacnewFile, PacnewRecommendation
from archward.privilege.sudo import SudoStrategy

log = logging.getLogger(__name__)


def find_pacnew_files(roots: tuple[Path, ...] = (Path("/etc"),), since_epoch: int | None = None) -> list[Path]:
    """Find .pacnew files under `roots`. If `since_epoch` is given, filter to mtime > since.

    Test-mode env var: setting `ARCHWARD_PACNEW_INCLUDE_ALL=1` bypasses the mtime
    filter so all .pacnew files are returned regardless of age. Used to exercise
    PacnewView's per-row buttons against pre-staged files without triggering a
    real update. Logs a warning so the override isn't silent.

    Uses `sudo find` so the scan can traverse root-only directories like /etc/sudoers.d.
    """
    include_all = os.environ.get("ARCHWARD_PACNEW_INCLUDE_ALL") == "1"
    if include_all and since_epoch is not None:
        log.warning(
            "ARCHWARD_PACNEW_INCLUDE_ALL=1 — bypassing since_epoch filter; "
            "all .pacnew files will be reported"
        )

    found: list[Path] = []
    for root in roots:
        cmd = ["sudo", "-n", "find", str(root), "-name", "*.pacnew"]
        # -n on sudo so we don't spawn askpass for what is normally a NOPASSWD
        # operation; if the user lacks NOPASSWD, we fall back to a non-sudo find
        # which may produce permission-denied warnings but still picks up most files.
        try:
            r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                # Fall back to unprivileged find.
                r = subprocess.run(
                    ["find", str(root), "-name", "*.pacnew"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.error("pacnew scan error for %s: %s", root, e)
            continue
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if since_epoch is not None and not include_all:
                try:
                    if p.stat().st_mtime <= since_epoch:
                        continue
                except OSError:
                    continue
            found.append(p)
    return sorted(set(found))


def classify(path: Path, cfg: PacnewConfig) -> PacnewFile:
    """Match `path` against config rules; first fnmatch wins.

    Rules are matched against the **original** (.pacnew-stripped) path because
    users write rules thinking about target files (e.g. `*.hook` should match
    `/etc/.../foo.hook.pacnew`, not require `*.hook.pacnew`).
    """
    spath = str(path)
    original = spath.removesuffix(".pacnew")
    for rule in cfg.rules:
        if fnmatch.fnmatch(original, rule.pattern):
            return PacnewFile(
                path=path,
                original_path=Path(original),
                recommendation=rule.strategy,
                rule_pattern=rule.pattern,
                note=rule.note,
                detected_at=datetime.now(),
            )
    return PacnewFile(
        path=path,
        original_path=Path(original),
        recommendation=cfg.default_strategy,
        rule_pattern=None,
        note=None,
        detected_at=datetime.now(),
    )


def render_diff(orig: Path, new: Path, n_context: int = 3) -> str:
    """Return a unified diff of orig vs new. Falls back to a stub on read errors."""
    try:
        a = orig.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as e:
        return f"(could not read {orig}: {e})\n"
    try:
        b = new.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as e:
        return f"(could not read {new}: {e})\n"
    return "".join(
        difflib.unified_diff(a, b, fromfile=str(orig), tofile=str(new), n=n_context)
    )


def apply_action(pacnew: PacnewFile, action: PacnewAction, strategy: SudoStrategy) -> None:
    """Apply the user's chosen action to a pacnew file."""
    from archward.pacman.runner import run_capture  # local import to avoid cycle

    if action is PacnewAction.LEAVE:
        return

    if action is PacnewAction.KEEP_OURS:
        # Discard the .pacnew side.
        code, _, err = run_capture(["rm", "-f", str(pacnew.path)], strategy=strategy)
        if code != 0:
            raise RuntimeError(f"rm failed: {err.strip()}")
        return

    if action is PacnewAction.TAKE_NEW:
        _apply_take_new(pacnew, strategy)
        return

    if action is PacnewAction.EDIT:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
        # No sudo: editor invocation is user-driven; for /etc files the user is
        # expected to either run archward as root or use a sudo-aware editor.
        subprocess.run([editor, str(pacnew.original_path), str(pacnew.path)], check=False)
        return

    raise ValueError(f"Unknown PacnewAction: {action}")


def _apply_take_new(pacnew: PacnewFile, strategy: SudoStrategy) -> None:
    """Replace the original with the .pacnew, preserving original ownership and mode.

    Per audit C1: a naive `mv` inherits the .pacnew's perms (typically 644 root:root),
    which would silently downgrade hardened 600 files.

    v0.4.1 (F3): if chown or chmod fails AFTER the mv succeeded, restore
    from the `.pre-archward.bak` backup. Without recovery, a partial
    failure could leave a 600 file at 644 (exposing secrets) or with
    wrong ownership. The backup preserves perms + ownership via `cp -a`,
    so restoring it brings the original target back to the pre-op state.
    """
    from archward.pacman.runner import run_capture  # local import to avoid cycle

    orig = pacnew.original_path
    new = pacnew.path

    # Stat the original BEFORE modifying anything so we can restore ownership/mode.
    try:
        st = orig.stat()
    except FileNotFoundError:
        # No original to preserve perms from — just move the .pacnew into place.
        code, _, err = run_capture(["mv", str(new), str(orig)], strategy=strategy)
        if code != 0:
            raise RuntimeError(f"mv failed: {err.strip()}")
        return

    backup = orig.with_suffix(orig.suffix + ".pre-archward.bak")
    # cp -a preserves perms, ownership, timestamps, xattrs.
    code, _, err = run_capture(["cp", "-a", str(orig), str(backup)], strategy=strategy)
    if code != 0:
        raise RuntimeError(f"cp -a (backup) failed: {err.strip()}")

    code, _, err = run_capture(["mv", str(new), str(orig)], strategy=strategy)
    if code != 0:
        raise RuntimeError(f"mv failed: {err.strip()}")

    # From here on, the .pacnew is gone (moved over the original) and the
    # original's pre-op state lives in `backup`. Any failure restoring
    # ownership/mode means the live file has wrong perms — recover by
    # copying the backup back over the target.
    code, _, err = run_capture(
        ["chown", f"{st.st_uid}:{st.st_gid}", str(orig)], strategy=strategy
    )
    if code != 0:
        chown_err = err.strip()
        recovered = _restore_from_backup(backup, orig, strategy)
        if recovered:
            raise RuntimeError(
                f"chown failed: {chown_err}. "
                f"Original restored from {backup} (perms + ownership unchanged)."
            )
        raise RuntimeError(
            f"chown failed: {chown_err}. "
            f"Recovery from {backup} ALSO FAILED — target file may have wrong "
            f"ownership; restore manually from {backup}."
        )

    mode_str = format(st.st_mode & 0o7777, "o")
    code, _, err = run_capture(["chmod", mode_str, str(orig)], strategy=strategy)
    if code != 0:
        chmod_err = err.strip()
        recovered = _restore_from_backup(backup, orig, strategy)
        if recovered:
            raise RuntimeError(
                f"chmod failed: {chmod_err}. "
                f"Original restored from {backup} (perms + ownership unchanged)."
            )
        raise RuntimeError(
            f"chmod failed: {chmod_err}. "
            f"Recovery from {backup} ALSO FAILED — target file may have wrong "
            f"mode (potential perm downgrade); restore manually from {backup}."
        )


def _restore_from_backup(backup: Path, target: Path, strategy: SudoStrategy) -> bool:
    """Copy `backup` back over `target` with `cp -a`. Returns True on success.

    Used by `_apply_take_new` to recover from a partial chown/chmod failure
    after the `mv` already moved the .pacnew into place.
    """
    from archward.pacman.runner import run_capture  # local import to avoid cycle

    code, _, err = run_capture(
        ["cp", "-a", str(backup), str(target)], strategy=strategy
    )
    if code != 0:
        log.error(
            "recovery cp -a %s %s failed: %s",
            backup, target, err.strip(),
        )
        return False
    return True
