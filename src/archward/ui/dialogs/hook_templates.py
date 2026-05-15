"""Pre-baked hook snippets for the Hooks tab (F4, v0.4.0).

Each template is one shell command, intended to be inserted into the
pre_update or post_verify editor with an `# template: <name>` header
line prepended. Append-on-select; never overwrite the user's existing
text.

Curation: 4 snippets total — one common backup-style pre-update, one
gate-style pre-update (refuse if backup is stale), and two post-verify
notifications. Add sparingly; the goal is to *demonstrate* what hooks
can do, not provide a library.

The template body should:
- Be a single shell line (commands chained with && or ;) — Hooks treat
  each line as one invocation via /bin/sh -c.
- Use $ARCHWARD_PHASE / $ARCHWARD_RESULT env vars when relevant (these
  are passed by HookRunner to post_verify hooks).
- Not assume specific paths (use ~ / $HOME / $XDG_CACHE_HOME).
"""

from __future__ import annotations

from typing import Literal

HookKind = Literal["pre", "post"]

# Display label → (kind, body). The label is what shows up in the
# combobox; the body is appended verbatim under a `# template: <label>`
# header.
HOOK_TEMPLATES: dict[str, tuple[HookKind, str]] = {
    "btrfs read-only snapshot of /home (pre)": (
        "pre",
        # Requires /home to be a btrfs subvolume and /.snapshots to exist.
        # -r makes the snapshot read-only (safer rollback reference).
        'mkdir -p /.snapshots && sudo btrfs subvolume snapshot -r /home '
        '"/.snapshots/home-$(date +%s)"\n',
    ),
    "Refuse update if /mnt/backup is missing or stale (pre)": (
        "pre",
        # test -d guards against the mount being absent; -lt 86400 = <24h.
        'test -d /mnt/backup && test $(( $(date +%s) - $(stat -c %Y /mnt/backup) )) '
        '-lt 86400 || { echo "backup missing or older than 24h; aborting"; exit 1; }\n',
    ),
    "Discord webhook on completion (post)": (
        "post",
        # Replace REPLACE_ME with your webhook URL. $ARCHWARD_RESULT is
        # the RESULT: tag emitted by archward when the pipeline finishes.
        "curl -sS -X POST -H 'Content-Type: application/json' "
        '-d "{\\"content\\":\\"archward done: $ARCHWARD_RESULT\\"}" '
        "https://discord.com/api/webhooks/REPLACE_ME\n",
    ),
    "Restart user systemd services after kernel update (post)": (
        "post",
        "systemctl --user daemon-reexec && systemctl --user restart --all\n",
    ),
}


def format_template_for_insertion(label: str) -> str:
    """Return the text to append to a hook editor on template selection.

    Header line (`# template: <label>`) lets the user grep / spot
    where each block came from; the body lines follow. Always
    terminates with a blank line so consecutive insertions stay
    visually separated.
    """
    if label not in HOOK_TEMPLATES:
        return ""
    _kind, body = HOOK_TEMPLATES[label]
    header = f"# template: {label}\n"
    trailing = "" if body.endswith("\n") else "\n"
    return f"{header}{body}{trailing}\n"
