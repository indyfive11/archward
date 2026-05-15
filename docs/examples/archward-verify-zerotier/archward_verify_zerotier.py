"""archward-verify-zerotier — post-update ZeroTier health probe.

Demonstrates the archward.verify_checks entry-point contract with a real,
useful plugin: parses `zerotier-cli info -j` and `zerotier-cli listnetworks -j`,
emits one PASS / WARN / FAIL VerifyCheck for daemon health plus one per joined
network. JSON parsing + typed field access is exactly where a Python plugin
beats a shell post_verify hook — try writing the per-network status table in
pure shell and you end up reinventing `jq` poorly.

Contract (see archward's docs/plugins.md):
    verify(cfg: ConfigModel, snapshot: Snapshot) -> list[VerifyCheck]

Failure isolation: any exception raised here is caught by archward's plugin
loop and surfaces as a synthetic FAIL row tagged with this entry-point name,
so a broken plugin doesn't crash the verify phase. Per-call timeout
(30 s) is enforced by archward; the subprocess calls below cap themselves
at 5 s as a defense-in-depth.

If `zerotier-cli` isn't on PATH the plugin returns an empty list — users
without ZeroTier installed see no plugin rows, no noise.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from archward.models.verify import CheckStatus, VerifyCheck

log = logging.getLogger(__name__)

_BUCKET = "plugin"
_CLI = "zerotier-cli"
_CLI_TIMEOUT_S = 5


def verify(cfg, snapshot) -> list[VerifyCheck]:
    """Entry point — wired into archward via the `archward.verify_checks`
    entry-point group in this package's pyproject.toml."""
    if shutil.which(_CLI) is None:
        # ZeroTier not installed on this host; emit nothing.
        return []

    checks: list[VerifyCheck] = []

    # ── Daemon info ───────────────────────────────────────────────────────
    info_raw, info_err = _run_cli("info", "-j")
    if info_raw is None:
        # zerotier-cli was present but couldn't authenticate (typical when
        # archward runs as non-root and the user hasn't set up a per-user
        # token). Emit one actionable WARN row instead of being silent.
        if info_err and "authtoken.secret" in info_err:
            return [VerifyCheck(
                bucket=_BUCKET,
                name="zerotier-auth",
                status=CheckStatus.WARN,
                message="zerotier-cli can't authenticate (no per-user authtoken)",
                detail=(
                    "Run this once to enable archward checks without sudo:\n"
                    "  sudo cp /var/lib/zerotier-one/authtoken.secret "
                    "~/.zeroTierOneAuthToken\n"
                    "  sudo chown $USER:$USER ~/.zeroTierOneAuthToken\n"
                    "  chmod 600 ~/.zeroTierOneAuthToken"
                ),
            )]
        return [VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-info",
            status=CheckStatus.FAIL,
            message="zerotier-cli info failed",
            detail=(info_err or "").strip()[:500],
        )]

    try:
        info = json.loads(info_raw)
    except json.JSONDecodeError as e:
        return [VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-info",
            status=CheckStatus.FAIL,
            message=f"zerotier-cli info produced invalid JSON: {e}",
            detail=info_raw[:500],
        )]

    online = bool(info.get("online"))
    address = info.get("address", "?")
    version = info.get("version", "?")
    if online:
        checks.append(VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-daemon",
            status=CheckStatus.PASS,
            message=f"online — node {address} (v{version})",
        ))
    else:
        checks.append(VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-daemon",
            status=CheckStatus.FAIL,
            message="zerotier-one running but reports offline (no controller reachable)",
            detail=f"node address: {address}",
        ))

    # ── Per-network status ────────────────────────────────────────────────
    net_raw, net_err = _run_cli("listnetworks", "-j")
    if net_raw is None:
        checks.append(VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-networks",
            status=CheckStatus.WARN,
            message="zerotier-cli listnetworks failed; per-network status unavailable",
            detail=(net_err or "").strip()[:500],
        ))
        return checks

    try:
        networks = json.loads(net_raw)
    except json.JSONDecodeError as e:
        checks.append(VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-networks",
            status=CheckStatus.FAIL,
            message=f"zerotier-cli listnetworks produced invalid JSON: {e}",
            detail=net_raw[:500],
        ))
        return checks

    if not networks:
        checks.append(VerifyCheck(
            bucket=_BUCKET,
            name="zerotier-networks",
            status=CheckStatus.WARN,
            message="zerotier-one online but no networks joined",
        ))
        return checks

    for net in networks:
        nwid = net.get("nwid", "?")
        name = net.get("name") or nwid
        status = net.get("status", "?")
        ips = net.get("assignedAddresses") or []
        ip_str = ", ".join(ips) if ips else "(no IP)"

        check_name = f"zt:{name}"
        if status == "OK":
            checks.append(VerifyCheck(
                bucket=_BUCKET,
                name=check_name,
                status=CheckStatus.PASS,
                message=f"{ip_str}",
                detail=f"nwid={nwid}",
            ))
        elif status == "REQUESTING_CONFIGURATION":
            checks.append(VerifyCheck(
                bucket=_BUCKET,
                name=check_name,
                status=CheckStatus.WARN,
                message="awaiting controller configuration",
                detail=f"nwid={nwid}",
            ))
        else:
            checks.append(VerifyCheck(
                bucket=_BUCKET,
                name=check_name,
                status=CheckStatus.FAIL,
                message=f"status: {status}",
                detail=f"nwid={nwid}; ips={ip_str}",
            ))

    return checks


def _run_cli(*args: str) -> tuple[str | None, str | None]:
    """Run `zerotier-cli <args>`. Returns (stdout, None) on success or
    (None, stderr) on failure. Never raises — exceptions are translated
    into the None branch so the caller emits a clean WARN/FAIL row."""
    try:
        result = subprocess.run(
            [_CLI, *args],
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, f"{type(e).__name__}: {e}"
    if result.returncode != 0:
        return None, result.stderr or result.stdout
    return result.stdout, None
