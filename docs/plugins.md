# archward — verify-check plugins

archward exposes a Python entry-point group, `archward.verify_checks`,
that lets third-party packages contribute additional checks to the
verify phase without modifying archward itself. A plugin is a regular
pip-installable Python package whose entry point points at a callable
that returns one or more `VerifyCheck` rows.

## Contract

```python
def my_checker(cfg: ConfigModel, snapshot: Snapshot) -> list[VerifyCheck]:
    ...
```

- **`cfg`** — the live `archward.models.config.ConfigModel`. Frozen
  Pydantic model. Read your tunables from your own section if you've
  added one (TOML is parsed strictly; unknown top-level sections are
  ignored), or from any existing field your check needs to consult.
- **`snapshot`** — the `archward.models.snapshot.Snapshot` produced by
  the snapshot phase of the current run. Has `.meta.path` (the snapshot
  dir on disk), `.package_files` (mapping of capture name → file Path
  in the snapshot), `.config_files`, `.service_files`, `.age_seconds`.
- **Return value** — a list of zero or more
  `archward.models.verify.VerifyCheck`, each with `bucket="plugin"`.
  Returning `None` is treated as an empty list.

Plugins should be **idempotent and side-effect-free**. They run after
the update has completed; they're a reporting surface, not an actor.

## Failure isolation

If your plugin raises any exception, archward catches it and renders a
synthetic FAIL row in the plugin bucket with the message
`plugin raised <ExceptionClass>: <message>`. Other plugins still run.
If your plugin returns a value that isn't a `VerifyCheck`, the same
synthetic FAIL appears.

You don't need defensive `try/except` boilerplate — let the exception
propagate and archward will surface it to the user. The traceback
also lands in `~/.local/state/archward/logs/archward.log`.

## Complete worked example

For a real, installable, fully-tested plugin see
**[`examples/archward-verify-zerotier/`](examples/archward-verify-zerotier/)**.
It parses `zerotier-cli info -j` and `zerotier-cli listnetworks -j` and
emits one PASS/WARN/FAIL VerifyCheck row per joined network — exactly
the kind of structured-output check that's awkward in shell. It also
demonstrates the cross-cutting concerns every plugin should handle:

- Graceful behavior when the CLI isn't installed (return empty list).
- Actionable error messages on auth failure (one WARN row with the
  recovery commands in `detail`).
- Subprocess timeouts (5 s defense-in-depth, on top of archward's 30 s
  per-plugin timeout).
- A test suite that stubs `subprocess.run` so the tests never hit a
  real daemon.

## Minimal sketch

Skeleton for a hypothetical `archward-verify-zfs` package that checks
each ZFS pool on the host:

```
archward-verify-zfs/
├── pyproject.toml
└── archward_verify_zfs.py
```

**`pyproject.toml`:**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "archward-verify-zfs"
version = "0.1.0"
description = "ZFS pool health checks for archward"
license = { text = "GPL-3.0-or-later" }
requires-python = ">=3.11"
dependencies = ["archward>=0.3.3"]

[project.entry-points."archward.verify_checks"]
zfs = "archward_verify_zfs:check_zfs_pools"
```

**`archward_verify_zfs.py`:**

```python
import subprocess
from archward.models.verify import CheckStatus, VerifyCheck


def check_zfs_pools(cfg, snapshot):
    # `zpool status -x` prints "all pools are healthy" when OK and exits 0;
    # exits non-zero with per-pool detail when something is degraded.
    proc = subprocess.run(
        ["zpool", "status", "-x"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0 and "all pools are healthy" in proc.stdout.lower():
        return [VerifyCheck(
            bucket="plugin",
            name="zfs",
            status=CheckStatus.PASS,
            message="all ZFS pools healthy",
        )]
    return [VerifyCheck(
        bucket="plugin",
        name="zfs",
        status=CheckStatus.FAIL,
        message="one or more ZFS pools are not healthy",
        detail=proc.stdout.strip() or proc.stderr.strip(),
    )]
```

Install + use:

```bash
pip install --user /path/to/archward-verify-zfs
archward          # or archward-gui — the "zfs" row appears in the plugin bucket
```

Uninstall:

```bash
pip uninstall archward-verify-zfs
```

archward picks up plugin changes only on next launch — entry-point
discovery happens at start-up.

## Conventions

- **Naming**: package `archward-verify-<thing>`, module
  `archward_verify_<thing>`, entry point `<thing> = ...:check_<thing>`.
  Not enforced, but improves discoverability.
- **One concern per plugin**: a ZFS plugin checks ZFS, a Docker plugin
  checks Docker. Don't bundle unrelated checks.
- **Cheap and bounded**: verify runs after every update. A plugin that
  takes 30 seconds is a 30-second tax on every update. Keep checks
  fast; if you need long-running probes, push them into a separate
  systemd service and have the plugin read the cached result.
- **No prompts, no mutations**: plugins are pure-function reporters.
  If you need user interaction, that's a different feature surface
  (file a feature request).
- **License clearly**: published plugins should pick a license; GPL-3.0
  or compatible is friendliest if you want them to coexist with
  archward.

## Versioning the contract

The `(cfg, snapshot) -> list[VerifyCheck]` shape is the public
contract. The internal layout of `ConfigModel` and `Snapshot` may
evolve, but additive changes that respect prior fields will stay
backward-compatible. Pin your plugin's `archward>=` lower bound to the
minimum version whose model fields you depend on.

A future archward may add a second argument (e.g. a verify-context
object). When it does, plugins will be encouraged to opt into the new
signature via inspection, and the existing signature will keep
working.
