# archward-verify-zerotier

A worked example of the [`archward.verify_checks`](../../plugins.md)
entry-point contract. Adds post-update ZeroTier health rows to
**archward**'s Verify view:

- `zerotier-daemon` — PASS when zerotier-one is online and reports a
  controller address; FAIL when the daemon is running but reports
  offline.
- `zt:<network-name>` — one row per joined network. PASS when status is
  `OK` (with the assigned ZT IPs); WARN when `REQUESTING_CONFIGURATION`
  (typically a transient post-restart state); FAIL for anything else
  (`ACCESS_DENIED`, `NOT_FOUND`, `AUTHENTICATION_REQUIRED`, …).

If `zerotier-cli` isn't on PATH the plugin emits no rows — users without
ZeroTier installed see nothing.

## Why a plugin (not a `post_verify` shell hook)

Writing this in shell means parsing the `zerotier-cli listnetworks -j`
JSON with `jq`, looping rows, gluing together a status table that
renders as a single hook output line. The plugin gets:

- **Typed field access** (`net["status"]`) instead of `jq`-string
  brittleness.
- **One row per network** in archward's Verify view, each with its own
  PASS / WARN / FAIL classification (a single hook collapses everything
  to one row).
- **Failure isolation + timeout** for free: archward wraps the
  plugin in a daemon thread with a 30 s join timeout, and any exception
  becomes a synthetic FAIL row instead of crashing verify.
- **A "What to do?" hint pre-built** — the plugin bucket gets a
  generic remediation hint in the GUI (archward v0.4.0+ feature).

## Install

```bash
# From a checkout of archward:
cd docs/examples/archward-verify-zerotier
pip install --user .

# Or directly from the upstream tree without cloning:
pip install --user "archward-verify-zerotier @ \
  git+https://github.com/indyfive11/archward.git#subdirectory=docs/examples/archward-verify-zerotier"
```

After installing, restart `archward` / `archward-gui` (entry-point
discovery happens at import time). The next pipeline run will surface
ZeroTier rows in the Verify view's **plugin** bucket.

### One-time setup so non-root archward can read the ZeroTier authtoken

`zerotier-cli` defaults to reading the secret from
`/var/lib/zerotier-one/authtoken.secret`, which is root-only. The
documented portable workaround is a per-user copy at
`~/.zeroTierOneAuthToken`:

```bash
sudo cp /var/lib/zerotier-one/authtoken.secret ~/.zeroTierOneAuthToken
sudo chown $USER:$USER ~/.zeroTierOneAuthToken
chmod 600 ~/.zeroTierOneAuthToken
```

If you skip this step the plugin emits a single actionable WARN row
("zerotier-cli can't authenticate") with the recovery commands in the
**What to do?** detail — no crash, no failure, just a clear hint.

## Verify it loaded

```bash
python3 - <<'PY'
import importlib.metadata as m
for ep in m.entry_points(group="archward.verify_checks"):
    print(ep.name, "→", ep.value)
PY
# expected: zerotier → archward_verify_zerotier:verify
```

In the GUI: Verify view, after a pipeline run, look for the
**plugin** bucket header. The rows are bolded teal (archward's v0.4.0
brand styling for verify groups).

## Run the tests

```bash
pip install --user pytest
python3 -m pytest tests/ -q
```

All tests stub `subprocess.run`, so they never touch a real ZeroTier
daemon. Six tests cover: CLI-not-installed, no authtoken, online + no
networks, online + mixed network states, offline daemon, malformed
JSON, subprocess timeout.

## License

GPL-3.0-or-later (matches archward).
